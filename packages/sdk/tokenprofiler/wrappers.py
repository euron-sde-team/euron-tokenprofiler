"""Drop-in wrappers: ``client = profiler.wrap_openai(OpenAI())`` and every call is recorded, with the same return values.

The wrapper times the call, takes usage from the response, and records errors. Anything it cannot read is skipped, never
raised. For streams it asks the provider for usage (``stream_options.include_usage``) so the numbers are exact, and hides
that extra chunk again unless the caller asked for it.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from .adapters import finish_reason, usage_from_anthropic, usage_from_openai

if TYPE_CHECKING:
    from .client import Profiler

LABELS = ("prompt_id", "user_id", "session_id", "tags", "app", "environment")


def _split(kwargs: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separates TokenProfiler labels (``tp_prompt_id=...``) from the provider's own arguments."""
    labels = dict(defaults)
    call = {}
    for k, v in kwargs.items():
        if k.startswith("tp_") and k[3:] in LABELS:
            labels[k[3:]] = v
        else:
            call[k] = v
    return labels, call


def _record(p: Profiler, model: str, labels: dict[str, Any], *, usage: dict[str, Any], start: float, ttft: float | None, stream: bool, finish: str | None, call: dict[str, Any], error: BaseException | None = None) -> None:  # fmt: skip
    try:
        fields: dict[str, Any] = {
            **labels,
            **usage,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "stream": stream,
        }
        if ttft is not None:
            fields["ttft_ms"] = round(ttft * 1000, 1)
        if finish:
            fields["finish_reason"] = finish
        if call.get("max_tokens") or call.get("max_completion_tokens"):
            fields["max_tokens"] = call.get("max_tokens") or call.get("max_completion_tokens")
        if call.get("temperature") is not None:
            fields["temperature"] = call["temperature"]
        if error is not None:
            fields.update(status="error", error_type=type(error).__name__)
            fields.setdefault("prompt_tokens", 0)
            fields.setdefault("completion_tokens", 0)
            if (code := getattr(error, "status_code", None)) and isinstance(code, int) and 400 <= code < 600:
                fields["http_status"] = code
        if (
            "prompt_tokens" not in fields
        ):  # the provider reported nothing: let the server estimate from the text we saw
            msgs = call.get("messages") or []
            fields["prompt_text"] = "\n".join(str(m.get("content", "")) for m in msgs if isinstance(m, dict))
        p.track(model, **fields)
    except Exception:
        p.stats.last_error = "record failed"


class _TrackedStream:
    """Wraps a provider stream, passing every chunk through and recording the call when the stream ends."""

    def __init__(
        self,
        inner: Any,
        p: Profiler,
        model: str,
        labels: dict[str, Any],
        start: float,
        call: dict[str, Any],
        hide_usage: bool,
    ) -> None:
        self._inner, self._p, self._model, self._labels, self._start, self._call, self._hide = (
            inner,
            p,
            model,
            labels,
            start,
            call,
            hide_usage,
        )
        self._usage: dict[str, Any] = {}
        self._ttft: float | None = None
        self._finish: str | None = None
        self._done = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _observe(self, chunk: Any) -> bool:
        """Returns whether the chunk should be passed on to the caller. Observing never raises."""
        keep = True
        with contextlib.suppress(Exception):
            if self._ttft is None and getattr(chunk, "choices", None):
                delta = getattr(chunk.choices[0], "delta", None)
                if getattr(delta, "content", None):
                    self._ttft = time.perf_counter() - self._start
            if u := usage_from_openai(chunk):
                self._usage = u
                if not getattr(chunk, "choices", None) and self._hide:
                    keep = False
            if fr := finish_reason(chunk):
                self._finish = fr
        return keep

    def _finish_up(self, error: BaseException | None = None) -> None:
        if not self._done:
            self._done = True
            _record(
                self._p,
                self._model,
                self._labels,
                usage=self._usage,
                start=self._start,
                ttft=self._ttft,
                stream=True,
                finish=self._finish,
                call=self._call,
                error=error,
            )

    def __iter__(self) -> Iterator[Any]:
        try:
            for chunk in self._inner:
                if self._observe(chunk):
                    yield chunk
        except BaseException as exc:
            self._finish_up(exc)
            raise
        finally:
            self._finish_up()

    async def __aiter__(self) -> AsyncIterator[Any]:
        try:
            async for chunk in self._inner:
                if self._observe(chunk):
                    yield chunk
        except BaseException as exc:
            self._finish_up(exc)
            raise
        finally:
            self._finish_up()

    def __enter__(self) -> _TrackedStream:
        return self

    def __exit__(self, *a: Any) -> None:
        self._finish_up()
        close = getattr(self._inner, "close", None)
        if close:
            close()


class _Namespace:
    """Forwards attribute access to the wrapped object, so everything not wrapped keeps working."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _prepare_stream(call: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not call.get("stream"):
        return call, False
    opts = dict(call.get("stream_options") or {})
    asked = bool(opts.get("include_usage"))
    opts["include_usage"] = True
    return {**call, "stream_options": opts}, not asked


class _Completions(_Namespace):
    def __init__(self, inner: Any, p: Profiler, defaults: dict[str, Any]) -> None:
        super().__init__(inner)
        self._p, self._defaults = p, defaults

    def create(self, **kwargs: Any) -> Any:
        labels, call = _split(kwargs, self._defaults)
        model = str(call.get("model", "unknown"))
        sent, hide = _prepare_stream(call)
        start = time.perf_counter()
        try:
            resp = self._inner.create(**sent)
        except BaseException as exc:
            _record(
                self._p,
                model,
                labels,
                usage={},
                start=start,
                ttft=None,
                stream=bool(call.get("stream")),
                finish=None,
                call=call,
                error=exc,
            )
            raise
        if call.get("stream"):
            return _TrackedStream(resp, self._p, model, labels, start, call, hide)
        _record(
            self._p,
            str(getattr(resp, "model", None) or model),
            labels,
            usage=usage_from_openai(resp),
            start=start,
            ttft=None,
            stream=False,
            finish=finish_reason(resp),
            call=call,
        )
        return resp


class _AsyncCompletions(_Namespace):
    def __init__(self, inner: Any, p: Profiler, defaults: dict[str, Any]) -> None:
        super().__init__(inner)
        self._p, self._defaults = p, defaults

    async def create(self, **kwargs: Any) -> Any:
        labels, call = _split(kwargs, self._defaults)
        model = str(call.get("model", "unknown"))
        sent, hide = _prepare_stream(call)
        start = time.perf_counter()
        try:
            resp = await self._inner.create(**sent)
        except BaseException as exc:
            _record(
                self._p,
                model,
                labels,
                usage={},
                start=start,
                ttft=None,
                stream=bool(call.get("stream")),
                finish=None,
                call=call,
                error=exc,
            )
            raise
        if call.get("stream"):
            return _TrackedStream(resp, self._p, model, labels, start, call, hide)
        _record(
            self._p,
            str(getattr(resp, "model", None) or model),
            labels,
            usage=usage_from_openai(resp),
            start=start,
            ttft=None,
            stream=False,
            finish=finish_reason(resp),
            call=call,
        )
        return resp


class _Embeddings(_Namespace):
    def __init__(self, inner: Any, p: Profiler, defaults: dict[str, Any]) -> None:
        super().__init__(inner)
        self._p, self._defaults = p, defaults

    def create(self, **kwargs: Any) -> Any:
        labels, call = _split(kwargs, self._defaults)
        model = str(call.get("model", "unknown"))
        start = time.perf_counter()
        try:
            resp = self._inner.create(**call)
        except BaseException as exc:
            _record(
                self._p,
                model,
                {**labels, "endpoint": "embedding"},
                usage={},
                start=start,
                ttft=None,
                stream=False,
                finish=None,
                call={},
                error=exc,
            )
            raise
        u = usage_from_openai(resp)
        u["completion_tokens"] = 0
        _record(
            self._p,
            model,
            {**labels, "endpoint": "embedding"},
            usage=u,
            start=start,
            ttft=None,
            stream=False,
            finish=None,
            call={},
        )
        return resp


def wrap_openai(p: Profiler, client: Any, **defaults: Any) -> Any:
    """Returns ``client`` with ``chat.completions.create`` and ``embeddings.create`` recorded. Pass ``tp_prompt_id=...`` (and
    ``tp_user_id``, ``tp_session_id``, ``tp_tags``) to a call to label it."""
    chat = _Namespace(client.chat)
    chat.completions = _Completions(client.chat.completions, p, defaults)  # type: ignore[attr-defined]
    wrapped = _Namespace(client)
    wrapped.chat = chat  # type: ignore[attr-defined]
    if hasattr(client, "embeddings"):
        wrapped.embeddings = _Embeddings(client.embeddings, p, defaults)  # type: ignore[attr-defined]
    return wrapped


def wrap_openai_async(p: Profiler, client: Any, **defaults: Any) -> Any:
    chat = _Namespace(client.chat)
    chat.completions = _AsyncCompletions(client.chat.completions, p, defaults)  # type: ignore[attr-defined]
    wrapped = _Namespace(client)
    wrapped.chat = chat  # type: ignore[attr-defined]
    return wrapped


class _Messages(_Namespace):
    def __init__(self, inner: Any, p: Profiler, defaults: dict[str, Any]) -> None:
        super().__init__(inner)
        self._p, self._defaults = p, defaults

    def create(self, **kwargs: Any) -> Any:
        labels, call = _split(kwargs, self._defaults)
        model = str(call.get("model", "unknown"))
        start = time.perf_counter()
        try:
            resp = self._inner.create(**call)
        except BaseException as exc:
            _record(
                self._p,
                model,
                labels,
                usage={},
                start=start,
                ttft=None,
                stream=bool(call.get("stream")),
                finish=None,
                call=call,
                error=exc,
            )
            raise
        if call.get("stream"):
            return resp  # Anthropic streams carry usage in different events: not wrapped, so nothing is mis-recorded
        _record(
            self._p,
            str(getattr(resp, "model", None) or model),
            labels,
            usage=usage_from_anthropic(resp),
            start=start,
            ttft=None,
            stream=False,
            finish=finish_reason(resp),
            call=call,
        )
        return resp


def wrap_anthropic(p: Profiler, client: Any, **defaults: Any) -> Any:
    """Records ``client.messages.create`` (non-streaming). Cache reads and writes are folded into the prompt total."""
    wrapped = _Namespace(client)
    wrapped.messages = _Messages(client.messages, p, defaults)  # type: ignore[attr-defined]
    return wrapped
