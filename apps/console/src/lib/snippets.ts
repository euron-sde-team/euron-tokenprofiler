/** Copy-paste integration examples, filled in with this server's URL. */
export function snippets(apiUrl: string): { id: string; label: string; code: string }[] {
  return [
    { id: "sdk", label: "Python SDK", code: `pip install tokenprofiler\n\nfrom openai import OpenAI\nfrom tokenprofiler import Profiler\n\ntp = Profiler("${apiUrl}", "<your tp_ key>", app="my-app")\nclient = tp.wrap_openai(OpenAI())  # every call is recorded; return values are unchanged\n\nclient.chat.completions.create(\n    model="gpt-4o",\n    messages=[{"role": "user", "content": "Hello"}],\n    tp_prompt_id="greeting",  # optional label\n)` },
    { id: "proxy", label: "Proxy (any language)", code: `from openai import OpenAI\n\nclient = OpenAI(\n    base_url="${apiUrl}/proxy/v1",\n    api_key="<your tp_ key>",\n    default_headers={"X-Upstream-Api-Key": "<your provider key>", "X-TP-App": "my-app", "X-TP-Prompt-Id": "greeting"},\n)` },
    { id: "http", label: "curl (events)", code: `curl ${apiUrl}/v1/events \\\n  -H "Authorization: Bearer $TP_API_KEY" -H "Content-Type: application/json" \\\n  -d '{"events": [{"model": "gpt-4o", "app": "my-app", "prompt_tokens": 1200, "completion_tokens": 300, "latency_ms": 840}]}'` },
  ];
}
