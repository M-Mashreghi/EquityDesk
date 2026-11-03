# EquityDesk — Deployment & CI/CD

Production-oriented deployment notes for the v1 modular monolith.

## 1. Runtime Shape

Recommended services:

- `equitydesk-api` — FastAPI application / research services
- `equitydesk-ui` — Streamlit dashboard (may run in same image with separate command)
- SQLite persistent volume
- `equitydesk-scheduler` — watchlist scheduler (`python -m equitydesk.infrastructure.jobs.scheduler`); several instances are safe (refresh slots are claimed atomically)

Avoid adding Redis/Postgres/queues until the workload requires them.

## 2. Environment Variables

Settings are loaded by `equitydesk.config.load_settings()` from an optional `.env` file, overridden by the process environment. Invalid or missing required values fail at startup with one `ConfigError` listing every problem (secret values are never echoed). The authoritative template is `.env.example`.

| Key | Default | Notes |
|---|---|---|
| `EQUITYDESK_ENV` | `development` | `development` / `test` / `production` |
| `EQUITYDESK_DB_PATH` | `data/equitydesk.db` | SQLite system of record |
| `EQUITYDESK_DATA_DIR` | `data` | raw source snapshots live under `data/raw` |
| `EQUITYDESK_CACHE_DIR` | `data/cache` | rebuildable |
| `EQUITYDESK_SECURITY_MASTER_PATH` | repository `data/fixtures/security_master.json` | curated security master JSON (set in the image) |
| `EQUITYDESK_LOG_LEVEL` | `INFO` | |
| `EQUITYDESK_LOG_FORMAT` | `json` | `json` or `text`; both are secret-scrubbed |
| `SEC_USER_AGENT` | — | required in production; must contain a contact e-mail |
| `SEC_MAX_REQUESTS_PER_SECOND` | `5` | capped at 10 (SEC fair-access limit) |
| `LLM_PROVIDER` | `fake` | `fake` (offline, deterministic) or `anthropic`; `fake` is rejected in production |
| `LLM_MODEL` | — | required for a real provider |
| `LLM_API_KEY` | — | secret; required for a real provider |
| `LLM_TIMEOUT_SECONDS` | `60` | |
| `LLM_MAX_RETRIES` | `2` | bounded retries for timeouts / schema failures |
| `LLM_COST_INPUT_PER_MTOK` | — | USD per million input tokens, cost telemetry only |
| `LLM_COST_OUTPUT_PER_MTOK` | — | USD per million output tokens, cost telemetry only |
| `ALERT_WEBHOOK_URL` | — | secret; optional alert hook |
| `ALERT_WEBHOOK_TOKEN` | — | secret; optional alert hook |
| `EQUITYDESK_API_URL` | `http://localhost:8000` | dashboard only: base URL of the research API |
| `EQUITYDESK_SCHEDULER_TICK_SECONDS` | `300` | watchlist scheduler tick (5–86400 s) |

Logging: `equitydesk.logging_setup.configure_logging()` installs a JSON (or text) handler that redacts registered secret values, `Authorization`/API-key shapes, and secret-named dict keys. Every record carries `correlation_id` and `research_run_id` bound with `log_context()`.

Never commit real values.

## 3. Persistent vs Rebuildable Data

Persistent:

- SQLite database
- explicit user research configuration / watchlists
- required source snapshots if configured for durable audit
- backup archives

Rebuildable/cache:

- HTTP cache
- parsed intermediates that can be reproduced from retained raw inputs
- temporary report exports

The final implementation must document whether raw source documents are persisted or can be re-fetched without breaking reproducibility.

## 4. Docker

One image (`Dockerfile`) serves all three processes; `docker-compose.yml` runs them:

| Service | Command | Port (host-local only) | Health |
|---|---|---|---|
| `api` | `uvicorn equitydesk.api.app:default_app --factory` (30 s graceful shutdown) | `127.0.0.1:8000` | `python -m equitydesk.healthcheck http://127.0.0.1:8000/ready` |
| `dashboard` | `streamlit run …/equitydesk/dashboard/app.py` with `EQUITYDESK_API_URL=http://api:8000` | `127.0.0.1:8501` | `/_stcore/health` via the same probe |
| `scheduler` | `python -m equitydesk.infrastructure.jobs.scheduler` | — | — |

Image properties:

- multi-stage build on `python:3.14-slim`; dependencies installed into `/opt/venv` with `pip install -c constraints.txt` (versions of the verified environment);
- non-root runtime user `equitydesk` (uid/gid 10001), `no-new-privileges`, read-only root filesystem with `/tmp` as tmpfs;
- persistent data only in the named volume `equitydesk-data` mounted at `/data` (`EQUITYDESK_DATA_DIR`, `EQUITYDESK_DB_PATH=/data/equitydesk.db`, raw store `/data/raw`, cache `/data/cache`, prices `/data/market/observations.json`); the security master is baked in at `/app/data/fixtures/security_master.json` (`EQUITYDESK_SECURITY_MASTER_PATH`);
- no secret is copied into the image (`.dockerignore` excludes `.env*`, keys, `.git`, local databases); secrets come from `.env` via `env_file` at runtime;
- the image defaults to `EQUITYDESK_ENV=production`, which fails fast unless `LLM_PROVIDER`/`LLM_MODEL`/`LLM_API_KEY` and `SEC_USER_AGENT` are set. For a local smoke test without credentials put `EQUITYDESK_ENV=development` in `.env`.

Health smoke:

```bash
cp .env.example .env                  # fill in values; EQUITYDESK_ENV=development for a local smoke
docker compose config --quiet         # validates the compose file
docker compose build
docker compose up -d
docker compose ps                     # api and dashboard should become "healthy"
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:8000/health/detailed
docker compose down                   # data stays in the equitydesk-data volume
```

The same build and an API health smoke run in CI (`.github/workflows/ci.yml`, job `image`).

## 5. CI/CD

`.github/workflows/ci.yml` (pull requests, pushes to `main`, and reused by the release):

| Job | Checks |
|---|---|
| `quality` | Python 3.14 with `constraints.txt`: ruff lint, ruff format, mypy --strict, point-in-time leakage + reproducibility suite, full pytest |
| `compatibility` | Python 3.12 (minimum supported), unpinned install, full pytest |
| `security` | `pip-audit` of `constraints.txt` (any known vulnerability fails), no committed `.env`, no secret-like literals outside tests |
| `image` | after `quality` and `security`: `docker compose config`, `docker build`, container started read-only with a volume, `/ready`, `/healthz`, `/health/detailed`, non-root user and Docker health status `healthy` |

`.github/workflows/release.yml` runs on tags `vX.Y.Z`: it first calls the whole CI workflow (`gates`); only if every gate passes does `publish` run, which checks that the tag equals the `pyproject.toml` version, builds and pushes `ghcr.io/<owner>/equitydesk:<tag>` and `:latest` with OCI labels, and creates the GitHub release with generated notes.

Least privilege: both workflows default to `contents: read`; only the `publish` job gets `contents: write` and `packages: write`, uses only the built-in `GITHUB_TOKEN`, and runs in the `release` environment (protect it with required reviewers in the repository settings). Checkouts do not persist credentials. CI needs no provider secrets because SEC, prices and the LLM are faked in tests.

Updating dependencies: change the environment, run the full suite, then regenerate `constraints.txt` with `pip freeze --exclude-editable` (keep the header) and re-run `pip-audit -r constraints.txt --disable-pip --no-deps`.

## 6. Release Checklist

- [ ] tests green
- [ ] no future-leakage regression
- [ ] example env updated
- [ ] migrations/backups tested
- [ ] Docker health green
- [ ] runbook synchronized
- [ ] tag/release notes generated

## 7. Secret Handling

- GitHub Actions secrets for CI/CD only where required.
- Production app secrets live in host/platform secret storage or `.env` excluded from Git.
- Do not expose source-provider credentials in logs or reports.

## 8. Backup

At minimum back up SQLite and any durable source snapshot store required for report reproducibility.

A release is not production-ready until restore has been tested.
