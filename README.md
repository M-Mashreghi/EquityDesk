# EquityDesk

[![ci](https://github.com/M-Mashreghi/EquityDesk/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/M-Mashreghi/EquityDesk/actions/workflows/ci.yml)

**Autonomous, evidence-backed, point-in-time equity research.**

EquityDesk turns a ticker or company into a reproducible research package built from primary sources and deterministic financial analysis. An LLM reads and classifies source text; Python calculates every number; an independent reviewer checks the result before a run can be marked complete.

```text
Ticker / Company
  → Security resolution
  → Primary-source collection (SEC EDGAR 10-K / 10-Q, issuer IR)
  → Point-in-time snapshot (hashes, publication timestamps, provenance)
  → Parsing / normalization
  → Financial statements (XBRL) + KPI engine
  → LLM evidence extraction (business model, segments, risks, catalysts, guidance changes)
  → Evidence quality gate
  → Peer multiples + DCF + bull / base / bear scenarios
  → Cited report (facts / inferences / assumptions / unknowns labeled)
  → Independent reviewer (completion gate)
```

> **Core question:** given only the information that was public as of a chosen date, what does the evidence say about this company, its financial trajectory, valuation, risks, catalysts and scenario range?

## Status

All 7 phases / 35 tasks of the v1 plan are implemented and verified (see [Task Ledger](docs/TASKS.md)). 392 automated tests (unit, integration, end-to-end) pass locally and in CI, with `ruff`, `mypy --strict`, `pip-audit` and a container health smoke.

| Area | What exists |
|---|---|
| Sources & provenance | SEC EDGAR adapter (fair-access user agent, rate limit, retries), issuer IR adapter with approved domains, content-addressed raw store, amendments / version chains, immutable point-in-time snapshots |
| Financials | XBRL company-facts mapper (restatements only once public, unit/period checks), 27 KPIs with formula + input provenance, trend and quality-of-earnings diagnostics |
| LLM research | Provider port (Anthropic adapter, offline fake), schema-validated structured outputs, verbatim-quote grounding, evidence quality gate; unsupported fields become explicit `unknown` |
| Valuation | Explicit versioned peer sets, multiples ranges (p25 / median / p75), DCF with sensitivity grid, bull / base / bear scenarios with consistency checks; no model-authored assumptions |
| Report & review | `report.v1` schema with citations, metric ids and valuation ids; reviewer for unsupported claims, future/stale sources, calculation–text mismatches, contradictions (optional LLM pass can only warn) |
| Workflow | End-to-end run orchestration with `BlockedBySource` / `NeedsInput` / `Failed` outcomes, refresh + run diff, watchlist with duplicate-safe scheduled refresh and webhook alerts |
| Interfaces | FastAPI research API, Streamlit dashboard (thin client over the API) |
| Operations | Run telemetry (stage timings, LLM tokens / cost), correlated JSON logs with secret scrubbing, `/healthz` `/ready` `/health/detailed`, Docker + Compose, GitHub Actions CI / release, backup / verify / restore with a tested restore drill |

## Example output (fixture run)

Excerpt of the Markdown report produced by the offline end-to-end test (Apple, `as_of` 2024-01-15):

```text
## Financial Analysis
- Fact: Gross margin for the period ended 2023-09-30: 44.1% (GrossProfit / Revenue). `m:co_apple:gross_margin:2023-09-30`
- Fact: Net debt for the period ended 2023-09-30: 49,533,000,000 (TotalDebt - CashAndEquivalents - ShortTermInvestments).

## Valuation
- Fact: DCF (5-year forecast, discount rate 9.0%, terminal growth 3.0%) gives ... 106.37 per share;
  the terminal value is 76% of EV. This is an assumption-driven estimate, not a price target. `val_dcf_…`

## Risks
- Inference: Supply chain concentration: Manufacturing depends on outsourcing partners concentrated in Asia. [4]

## Sources
4. 10-K (regulatory_filing, published 2023-11-02, 0000320193-23-000106; …): "manufacturing is performed in whole or in part by outsourcing partners"
```

## Quick start

### Local

```bash
python -m venv .venv                       # Python 3.12+ (CI pins 3.14 via constraints.txt)
.venv/Scripts/activate                     # Windows (or: source .venv/bin/activate)
pip install -c constraints.txt -e ".[dev,api,dashboard,llm]"
cp .env.example .env                       # fill in local values; never commit .env

uvicorn equitydesk.api.app:default_app --factory --port 8000
EQUITYDESK_API_URL=http://localhost:8000 streamlit run src/equitydesk/dashboard/app.py
python -m equitydesk.infrastructure.jobs.scheduler        # optional: watchlist scheduler
```

Collecting filings requires `SEC_USER_AGENT` (with a contact e-mail). With `LLM_PROVIDER=fake` extraction degrades to explicit unknowns; set `LLM_PROVIDER=anthropic`, `LLM_MODEL` and `LLM_API_KEY` for real extraction.

### Docker

```bash
cp .env.example .env                       # EQUITYDESK_ENV=development for a smoke test without credentials
docker compose up -d --build               # api :8000, dashboard :8501, scheduler (host-local ports)
curl -fsS http://127.0.0.1:8000/ready
```

Details: [Deployment](docs/DEPLOYMENT.md).

## Running research through the API

```bash
# 1. create a run (ticker, CIK, ISIN or name; as_of must not be in the future)
curl -s -X POST localhost:8000/research-runs -H 'content-type: application/json' \
  -d '{"query": "AAPL", "as_of": "2024-01-15T00:00:00Z"}'

# 2. execute it with explicit analyst inputs: a ResearchInputs JSON (src/equitydesk/application/workflow.py)
#    with bull / base / bear DCF assumptions and their rationale
curl -s -X POST localhost:8000/research-runs/<run_id>/execute -H 'content-type: application/json' -d @inputs.json

# 3. read results
curl -s localhost:8000/research-runs/<run_id>/report?format=markdown
```

| Endpoint | Purpose |
|---|---|
| `GET /companies/resolve?q=` | resolve ticker / CIK / ISIN / name (ambiguity returns candidates) |
| `POST /research-runs`, `GET /research-runs/{id}` | create a run, read status and history |
| `POST /research-runs/{id}/execute` | run the full pipeline with `ResearchInputs` |
| `GET /research-runs/{id}/documents` · `/financials` · `/valuations` · `/report` · `/telemetry` | point-in-time results |
| `POST /research-runs/{id}/refresh`, `GET /research-runs/{id}/diff?against=` | new run for a later `as_of`, and what changed |
| `PUT/DELETE /watchlist/{company_id}`, `POST /watchlist/tick`, `GET /alerts` | watchlist, scheduled refresh, alerts |
| `GET /healthz`, `/ready`, `/health/detailed` | liveness, readiness, diagnostics (no secret values) |

Errors always use `{"error": {"code", "message", "details"}}`.

## Research integrity principles

1. **Primary sources first.** Regulator filings and issuer material; a secondary source never silently overrides a primary one.
2. **Every material claim needs evidence.** Unsupported claims are marked uncertain / unknown or blocked by the reviewer.
3. **LLM ≠ calculator.** Ratios, valuation and scenarios are deterministic Python; numbers in LLM prose must appear in cited quotes.
4. **Point-in-time by design.** Documents, facts, prices and peer sets are filtered by `as_of`; a 15-vector leakage matrix tests this.
5. **Assumptions are visible.** Every valuation stores formula, inputs with sources, versioned assumptions with rationale, and sensitivity.
6. **Facts, inferences, assumptions and unknowns are labeled separately** in every report.
7. **Reproducibility.** Source hashes, model / prompt / schema / calculation versions are recorded; a frozen run is compared to golden values in CI.

## Non-goals (v1)

No order execution or brokerage integration, no personalized investment advice, no LLM-generated financial statements or valuation math, no invented consensus or guidance, no use of future information in historical research.

## Development

```bash
pytest                                     # full suite (unit, integration, e2e — all offline)
ruff check src tests && ruff format --check src tests
mypy                                       # strict
```

- Layered modular monolith: `domain` → `application` → `infrastructure` / `api` / `dashboard`; import rules are enforced by `tests/unit/test_architecture.py`.
- External services (SEC, LLM, prices, webhooks) are adapters behind ports; tests use fixtures and fakes, so CI needs no secrets.
- `docs/PROJECT_PHASES_AND_TASKS.md` is the source of truth for scope; changes follow one task at a time with tests and synchronized docs (see `CLAUDE.md`).

## Documentation

- [Architecture & implementation contracts](docs/ARCHITECTURE.md)
- [Deployment, Docker & CI/CD](docs/DEPLOYMENT.md)
- [Operational runbook](docs/RUNBOOK.md)
- [Diagrams](docs/DIAGRAMS.md)
- [Roadmap](docs/ROADMAP.md) · [Phases](docs/PHASES.md)
- [Atomic tasks (SSOT)](docs/PROJECT_PHASES_AND_TASKS.md) · [Task ledger](docs/TASKS.md)

## Disclaimer

EquityDesk is a research and decision-support project. Its outputs are informational, may contain errors, and are not personalized investment advice or a recommendation to trade securities. A scenario or DCF value is an assumption-driven estimate, not a price target.
