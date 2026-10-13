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

Example keys (exact names finalized by implementation tasks):

```text
EQUITYDESK_ENV=production
EQUITYDESK_DB_PATH=/app/data/equitydesk.db
EQUITYDESK_DATA_DIR=/app/data
EQUITYDESK_CACHE_DIR=/app/cache
LLM_PROVIDER=...
LLM_MODEL=...
LLM_API_KEY=...
# optional market/estimate provider keys
```

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
