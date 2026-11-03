# EquityDesk Operational Runbook

Operational procedures for the EquityDesk research service (API, dashboard, watchlist scheduler, SQLite store). Commands assume `docker compose` deployments; for a local install drop the `docker compose exec api` prefix.

## 1. Health Diagnostics

| Probe | Meaning |
|---|---|
| `GET /healthz` | process alive (liveness) |
| `GET /ready` | database answers with the expected schema version and the data directory is writable; `503` with per-check detail otherwise |
| `GET /health/detailed` | environment, versions, LLM/SEC/webhook configuration flags (never secret values), watchlist size |
| `GET /research-runs/{id}/telemetry` | stage timings, LLM calls/tokens/cost, source/parser counters of one run |

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:8000/health/detailed
docker compose ps
docker compose logs --tail 200 api scheduler
```

Every log line is JSON with `correlation_id` (requests: the `X-Correlation-ID` header; runs: one id per run) and `research_run_id`. To follow one run: `docker compose logs api | grep '"research_run_id": "run_…"'`.

## 2. Research Run Stuck or Failed

| Final status | Meaning | Action |
|---|---|---|
| `BlockedBySource` | no primary document could be collected | §3 |
| `NeedsInput` | missing analyst inputs (scenarios, base facts) or blocking review findings | read the last `status_history` reason; supply inputs or fix the cause, then refresh the run |
| `Failed` | unexpected error (incl. `FutureLeakageError`) | read the reason and the run's log lines; for leakage see §6 |

A run never re-executes: create a refresh (`POST /research-runs/{id}/refresh`) so the failed run remains as audit history.

## 3. Source Provider Outage (SEC EDGAR / issuer IR)

Symptoms: `BlockedBySource`, notes such as `filing index unavailable … (HTTP 503)`, telemetry `collection_failures` > 0.

1. Check https://www.sec.gov status and `GET /health/detailed` (`sec.user_agent_configured` must be true; SEC rejects anonymous clients with 403).
2. HTTP 429/5xx are retried with backoff by `HttpFetcher`; do not lower `SEC_MAX_REQUESTS_PER_SECOND` below fair-access needs or raise it above 10.
3. Confirm `as_of` did not legitimately exclude every document (a very early `as_of`).
4. Pause scheduled refreshes if the outage is long: `DELETE /watchlist/{company_id}` or stop the `scheduler` service; already claimed slots are not repeated.
5. Re-run with a refresh after the provider recovers. Never substitute an unapproved secondary source.

## 4. LLM Provider Outage / Malformed Output

Symptoms: notes `llm … extraction failed: provider_error|timeout|malformed_json`, overview fields reported as `unknown`, review blocked by `missing_critical_section`.

1. `GET /health/detailed`: provider, model, `api_key_configured`.
2. Inspect the run telemetry `llm.by_call` (failure code, attempts). Logs never contain the API key.
3. Retries are bounded by `LLM_MAX_RETRIES`; failures degrade to explicit unknown findings, never to invented content.
4. After recovery, refresh the run. If a model or prompt changed, the new run records the new versions (`model_version`, `prompt_versions`).
5. Key rotation: update `LLM_API_KEY` in `.env` / the secret store, `docker compose up -d` (no image rebuild).

## 5. Review Gate Blocks Completion

`NeedsInput` with reason `review … blocked: <codes>`. The report and review are stored: `GET /research-runs/{id}/report` shows findings.

| Code | Typical fix |
|---|---|
| `missing_critical_section` | extraction produced nothing for overview/risks; check LLM (§4) or sources |
| `unsupported_claim`, `number_not_in_evidence` | composer/extractor lost a citation or a number was invented; fix code, add a regression test |
| `calculation_mismatch`, `text_calculation_mismatch`, `contradictory_values` | §7 |
| `future_source`, `citation_outside_snapshot`, `citation_provenance_mismatch` | §6 |
| `scenario_inconsistent` | fix the analyst scenario inputs (ordering, probabilities, rationales) |

Never mark a run Completed by hand: the store refuses `Completed` without a passing review.

## 6. Point-in-Time Incident

If a report appears to include future information:

1. Leave the run untouched (runs, documents, reports and reviews are immutable).
2. Capture `as_of`, the offending document/fact/price id, its `published_at` / `available_at` / `observed_at` and `retrieved_at`.
3. Reproduce with the leakage matrix (`pytest tests/e2e/test_point_in_time_audit.py`) and add a new vector for the case.
4. Fix the filter, then create a corrected run; reference the invalid run id in the incident notes.

## 7. Financial Calculation or Valuation Mismatch

1. Locate the metric id (`m:<company>:<metric>:<period>`) or valuation id in the report.
2. Check facts behind it: units, periods, currency, share basis, restatement flags (`/research-runs/{id}/financials`).
3. For valuations check the market observation date and source, net-debt bridge, diluted shares, scenario assumptions (with rationale and hash) and peer set version.
4. Recompute with a small hand-checked fixture; fix deterministic code; add a regression test. Never patch report prose to hide a mismatch, never let a model change numerical inputs.

## 8. Database Corruption, Data Loss or Host Replacement — Restore

Backups: `python -m equitydesk.infrastructure.db.backup create --keep 14` (online-consistent SQLite copy + raw source store + manifest with row counts and SHA-256 per file). Schedule it daily (cron / host scheduler) and copy archives off-host.

Restore drill (tested by `tests/e2e/test_backup_restore.py`):

```bash
docker compose stop api scheduler dashboard              # stop writers
docker compose run --rm api python -m equitydesk.infrastructure.db.backup verify /data/backups/equitydesk-<ts>.tar.gz
docker compose run --rm api python -m equitydesk.infrastructure.db.backup restore /data/backups/equitydesk-<ts>.tar.gz
docker compose up -d
curl -fsS http://127.0.0.1:8000/ready                     # schema check
curl -fsS http://127.0.0.1:8000/research-runs/<known run id>/report?format=markdown   # citations resolve
```

`verify` checks every file hash, `PRAGMA integrity_check`, row counts and schema compatibility; restore refuses an invalid archive and moves the existing database / raw store to `*.pre-restore-<timestamp>` instead of deleting them (forensic copy). Archives are tar files: path-traversal members are rejected.

## 9. Raw Source and Cache Policy

- `data/raw` (content-addressed by SHA-256) is **persistent** and backed up: reports must stay reproducible even if an issuer removes or changes a document. Content is re-hashed on every read.
- `data/cache` and parsed intermediates are **rebuildable**; they are not backed up.
- `data/market/observations.json` (analyst-maintained prices with source and timestamp) is persistent.
- Disk full: prune old backups (`--keep`), never delete `data/raw` objects referenced by stored documents.

## 10. Secret Exposure

1. Rotate the credential at the provider immediately (LLM key, webhook URL/token).
2. Update `.env` / secret store; restart services.
3. Search logs: values are scrubbed, but check for unregistered formats; purge affected log storage.
4. Add a scrubbing regression test if a new secret shape leaked.

## 11. Scheduler and Alerts

- Duplicate refreshes cannot happen: each company/interval window is claimed once in `refresh_claims`; several scheduler instances are safe.
- Undelivered alerts keep `delivered=false` and `delivery_error` (`GET /alerts`); fix the webhook and resend with `deliver_pending`.

## 12. Research Reproducibility Check

`pytest tests/e2e/test_point_in_time_audit.py -k golden` replays the frozen run and compares it with `tests/fixtures/reproducibility/frozen_run.json` (snapshot membership, fact ids, valuation values, report digest, review verdict). See the fixture README before regenerating it.

## 13. Release Checklist

- [ ] CI green on `main` (quality, compatibility, security, image)
- [ ] point-in-time leakage matrix and golden reproducibility green
- [ ] `pip-audit` clean; `constraints.txt` regenerated if dependencies changed
- [ ] `.env.example` and `docs/DEPLOYMENT.md` list every configuration key
- [ ] backup created and `verify` passes; restore drill test green
- [ ] SQLite schema change is additive and `SCHEMA_VERSION` bumped
- [ ] runbook and architecture contracts synchronized
- [ ] version bumped in `pyproject.toml`, tag `vX.Y.Z` pushed (the release workflow publishes only after all gates)

Rollback: deploy the previous image tag (`ghcr.io/<owner>/equitydesk:<previous>`). Schema changes are additive, so an older image can read a newer database only if its `SCHEMA_VERSION` is not lower; otherwise restore the pre-upgrade backup (§8).
