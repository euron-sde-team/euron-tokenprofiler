FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY packages/core ./packages/core
COPY apps/mock-llm ./apps/mock-llm
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 10 --default-timeout 60 ./packages/core ./apps/mock-llm && useradd --system --uid 10001 mock
USER mock
EXPOSE 8390
HEALTHCHECK --interval=15s --timeout=5s CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8390/healthz')"
CMD ["mock-llm"]
