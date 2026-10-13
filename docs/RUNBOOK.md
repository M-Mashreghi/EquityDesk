# EquityDesk Operational Runbook

Operational procedures for the EquityDesk research service.

## 1. Health Diagnostics

Expected endpoints after implementation:

| Probe | Meaning |
|---|---|
| `/healthz` | process alive |
| `/ready` | database + required dependencies usable |
| `/health/detailed` | component diagnostics without secrets |

Basic checks:

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/ready
docker compose ps
docker compose logs --tail 200
```

## 2. Source Collection Failure

Symptoms:

- research run stuck/failed in Collecting
- regulator/IR timeout
- upstream 403/429/5xx

Actions:

1. inspect run correlation id and source adapter
2. verify upstream status and configured user-agent/contact requirements
3. respect retry/backoff; do not tight-loop
4. confirm `as_of` did not legitimately exclude all documents
5. retry the run only after the source issue is understood
6. do not substitute an unapproved secondary source silently

## 3. Parser Failure

1. retain source metadata/hash
2. identify document type/version
3. reproduce with local fixture
4. add parser regression fixture before changing parser logic
5. if data cannot be parsed safely, represent it as missing rather than fabricate it

## 4. LLM Provider Failure / Malformed Output

1. confirm provider/model configuration
2. inspect sanitized error (never API key)
3. apply bounded retry/fallback policy
4. schema failure must become structured failure/uncertain result
5. do not insert raw free-form output into the research domain

## 5. Point-in-Time Incident

If a report appears to include future information:

1. mark the research run invalid
2. capture `as_of`, offending source/data id, `published_at`, `retrieved_at`
3. reproduce with leakage tests
4. fix filtering before re-running
5. do not overwrite the invalid run; retain audit metadata and create a corrected run

## 6. Financial Calculation Mismatch

1. locate metric id and source financial facts
2. verify units, periods, sign convention, currency, share basis
3. recompute with a small isolated fixture
4. fix deterministic logic; never patch report prose to hide the mismatch
5. add regression test

## 7. Valuation Incident

If valuation looks unreasonable:

- verify market snapshot date
- verify diluted share count/net debt bridge
- inspect scenario assumptions
- inspect peer set and observation date
- run sensitivity table
- confirm missing values were not implicitly filled

Do not allow the reviewer/LLM to change numerical inputs silently.

## 8. Citation / Unsupported Claim Failure

A material uncited claim should block or warn the report depending on severity.

Recovery:

1. trace claim to structured finding/calculation
2. validate evidence is in the run snapshot
3. repair extraction/composer if citation was lost
4. remove claim if support does not exist

## 9. Database Backup / Restore

Before restore:

1. stop write processes
2. copy current DB for forensic backup
3. restore latest verified backup
4. run integrity/schema checks
5. start services
6. verify a historical research run and citations resolve

## 10. Research Reproducibility Check

For a frozen fixture run, verify:

- identical snapshot membership
- identical deterministic calculations
- compatible structured LLM result under mocked provider
- stable report schema/citation references

## 11. Release-Day Checklist

- CI green
- point-in-time tests green
- source/parser fixtures green
- valuation fixtures green
- no secret in repo/image
- health/readiness green
- backup/restore verified
- docs synchronized
