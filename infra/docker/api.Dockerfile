FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TP_HOST=0.0.0.0 TP_PORT=8380 TP_DATA_DIR=/data
WORKDIR /app
COPY packages/core ./packages/core
COPY apps/api ./apps/api
# BuildKit cache + retries: rebuilds are fast and survive a flaky network.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 10 --default-timeout 60 ./packages/core ./apps/api \
 && useradd --system --uid 10001 --create-home tp \
 && mkdir -p /data && chown -R 10001:10001 /data
USER tp
EXPOSE 8380
VOLUME ["/data"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8380/healthz')"
CMD ["tokenprofiler", "serve"]
