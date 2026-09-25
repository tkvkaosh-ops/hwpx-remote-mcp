# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements-remote.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements-remote.txt

FROM python:3.11-slim AS runtime

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/output \
    && chown -R appuser:appuser /app

COPY --from=builder /wheels /wheels
COPY requirements-remote.txt ./
RUN pip install --no-cache-dir --no-index --find-links=/wheels \
    -r requirements-remote.txt \
    && rm -rf /wheels

COPY --chown=appuser:appuser hwpx_mcp ./hwpx_mcp

USER appuser

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000
ENV MCP_PATH=/mcp
ENV MCP_STATELESS=true
ENV MCP_JSON_RESPONSE=true
ENV HWPX_OUTPUT_DIR=/app/output
ENV HWPX_DOWNLOAD_TTL_SECONDS=86400
ENV HWPX_MAX_UPLOAD_BYTES=20971520

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:${PORT:-${MCP_PORT}}/health || exit 1

CMD ["python", "-m", "hwpx_mcp.remote_server"]
