# syntax=docker/dockerfile:1.7
# EquityDesk runtime image: API, dashboard and watchlist scheduler share one image and differ
# only by command (see docker-compose.yml). No secrets are copied into the image.

ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install -c constraints.txt ".[api,dashboard,llm]"

FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EQUITYDESK_ENV=production \
    EQUITYDESK_DATA_DIR=/data \
    EQUITYDESK_DB_PATH=/data/equitydesk.db \
    EQUITYDESK_CACHE_DIR=/data/cache \
    EQUITYDESK_SECURITY_MASTER_PATH=/app/data/fixtures/security_master.json
RUN groupadd --system --gid 10001 equitydesk \
 && useradd --system --uid 10001 --gid equitydesk --home-dir /app --shell /usr/sbin/nologin equitydesk \
 && mkdir -p /data /app/data/fixtures \
 && chown -R equitydesk:equitydesk /data
COPY --from=build /opt/venv /opt/venv
# Curated security master (EQUITYDESK_SECURITY_MASTER_PATH).
COPY --chown=equitydesk:equitydesk data/fixtures /app/data/fixtures
WORKDIR /app
USER equitydesk
VOLUME ["/data"]
EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "equitydesk.healthcheck", "http://127.0.0.1:8000/ready"]
STOPSIGNAL SIGTERM
CMD ["uvicorn", "equitydesk.api.app:default_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
