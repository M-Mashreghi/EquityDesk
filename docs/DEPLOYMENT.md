# EquityDesk — Deployment & CI/CD

Production-oriented deployment notes for the v1 modular monolith.

## 1. Runtime Shape

Recommended services:

- `equitydesk-api` — FastAPI application / research services
- `equitydesk-ui` — Streamlit dashboard (may run in same image with separate command)
- SQLite persistent volume
- optional scheduled worker process after Phase 6

Avoid adding Redis/Postgres/queues until the workload requires them.

## 2. Environment Variables

Settings are loaded by `equitydesk.config.load_settings()` from an optional `.env` file, overridden by the process environment. Invalid or missing required values fail at startup with one `ConfigError` listing every problem (secret values are never echoed). The authoritative template is `.env.example`.

| Key | Default | Notes |
|---|---|---|
| `EQUITYDESK_ENV` | `development` | `development` / `test` / `production` |
| `EQUITYDESK_DB_PATH` | `data/equitydesk.db` | SQLite system of record |
| `EQUITYDESK_DATA_DIR` | `data` | raw source snapshots live under `data/raw` |
| `EQUITYDESK_CACHE_DIR` | `data/cache` | rebuildable |
| `EQUITYDESK_LOG_LEVEL` | `INFO` | |
| `EQUITYDESK_LOG_FORMAT` | `json` | `json` or `text`; both are secret-scrubbed |
| `SEC_USER_AGENT` | — | required in production; must contain a contact e-mail |
| `SEC_MAX_REQUESTS_PER_SECOND` | `5` | capped at 10 (SEC fair-access limit) |
| `LLM_PROVIDER` | `fake` | `fake` (offline, deterministic) or `anthropic`; `fake` is rejected in production |
| `LLM_MODEL` | — | required for a real provider |
| `LLM_API_KEY` | — | secret; required for a real provider |
| `LLM_TIMEOUT_SECONDS` | `60` | |
| `LLM_MAX_RETRIES` | `2` | bounded retries for timeouts / schema failures |
| `ALERT_WEBHOOK_URL` | — | secret; optional alert hook |
| `ALERT_WEBHOOK_TOKEN` | — | secret; optional alert hook |
| `EQUITYDESK_API_URL` | `http://localhost:8000` | dashboard only: base URL of the research API |

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

## 4. Docker Requirements

- Python 3.12+ base
- non-root runtime user
- locked/pinned dependencies
- health endpoint for API
- explicit persistent data mount
- no secret copied into image
- graceful shutdown for in-flight research runs where feasible

Example startup shape:

```bash
docker compose up -d
curl -fsS http://localhost:8000/healthz
```

## 5. CI Quality Gates

PR/main CI should run:

1. dependency install from lock
2. formatting/lint checks
3. type checks where configured
4. full pytest suite
5. point-in-time leakage tests
6. security/dependency audit
7. build/import smoke

Release/image publish must depend on successful quality gates.

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
