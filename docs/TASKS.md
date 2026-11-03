# EquityDesk — Task Progress Ledger

Update after every verified task.

## Status Legend

| Marker | Meaning |
|---|---|
| `[ ]` | Pending |
| `[-]` | In Progress |
| `[x]` | Completed |

## Phase 0 — Governance Baseline

- [x] Create `CLAUDE.md`
- [x] Create `.claude/rules/*`
- [x] Create `README.md` / `PROJECT_STRUCTURE.md`
- [x] Create architecture, roadmap, phases, SSOT, diagrams, deployment and runbook docs

## Phase 1 — Foundation, Research Runs & Security Master

- [x] **Task 1.1** — Python Project Scaffold & Architecture Guardrails
- [x] **Task 1.2** — Configuration, Secrets & Structured Logging
- [x] **Task 1.3** — Core Domain Models & SQLite Persistence
- [x] **Task 1.4** — Company / Security Resolver
- [x] **Task 1.5** — Point-in-Time Source Provenance Contract

### Verification — Phase 1

| Item | Detail |
|---|---|
| Status | Completed |
| Tests | `pytest`: 99 passed (unit + SQLite integration); ruff, mypy --strict clean |
| Docs | ARCHITECTURE.md Implementation Contracts 1.1–1.5; DEPLOYMENT.md env keys; README dev setup |
| Notes | Python 3.14 venv used locally (project requires >=3.12) |

## Phase 2 — Primary-Source Collection & Document Pipeline

- [x] **Task 2.1** — Regulatory Filing Adapter
- [x] **Task 2.2** — Issuer Investor-Relations Adapter
- [x] **Task 2.3** — Document Parser & Normalized Document Model
- [x] **Task 2.4** — Document Hashing, Deduplication & Amendment/Version Links
- [x] **Task 2.5** — Point-in-Time Research Snapshot Builder

### Verification — Phase 2

| Item | Detail |
|---|---|
| Status | Completed |
| Tests | `pytest`: 163 passed; SEC/IR HTTP served by httpx.MockTransport fixtures |
| Docs | ARCHITECTURE.md contracts 2.1–2.5 |
| Notes | SEC acceptance time interpreted conservatively (New York wall-clock, not before filing date) |

## Phase 3 — Financial Statements & Deterministic Analytics

- [x] **Task 3.1** — Standardized Financial Fact Model & Statement Mapper
- [x] **Task 3.2** — Historical KPI & Ratio Engine
- [x] **Task 3.3** — Trend, Change & Quality-of-Earnings Diagnostics
- [x] **Task 3.4** — Peer Set & Comparable Metrics Foundation
- [x] **Task 3.5** — Valuation Primitives & Market Snapshot Contract

### Verification — Phase 3

| Item | Detail |
|---|---|
| Status | Completed |
| Tests | `pytest`: 232 passed; KPI fixtures hand-checked against Apple FY2021–FY2023 10-K figures |
| Docs | ARCHITECTURE.md contracts 3.1–3.5 |
| Notes | No pandas/numpy needed: analytics use the standard library only |

## Phase 4 — LLM Research Intelligence

- [x] **Task 4.1** — Structured LLM Provider & Schema Validation
- [x] **Task 4.2** — Business Model & Segment Evidence Extractor
- [x] **Task 4.3** — Guidance, Strategy & Management-Language Change Analyzer
- [x] **Task 4.4** — Risk, Catalyst & Open-Question Extractor
- [x] **Task 4.5** — Evidence Validator & Research Object Quality Gate

### Verification — Phase 4

| Item | Detail |
|---|---|
| Status | Completed |
| Tests | `pytest`: 288 passed; LLM always mocked (ScriptedLLMProvider / fake SDK client) |
| Docs | ARCHITECTURE.md contracts 4.1–4.5 |
| Notes | Anthropic adapter is an optional extra (.[llm]); default model claude-opus-5-5 with server-side refusal fallbacks |

## Phase 5 — Valuation, Scenarios, Thesis & Report Review

- [x] **Task 5.1** — Peer Comparison & Multiples Valuation
- [x] **Task 5.2** — DCF / Intrinsic Scenario Engine
- [x] **Task 5.3** — Bull / Base / Bear Scenario Builder
- [x] **Task 5.4** — Investment Thesis & Cited Research Report Composer
- [x] **Task 5.5** — Independent Reviewer / Contradiction & Completeness Gate

### Verification — Phase 5

| Item | Detail |
|---|---|
| Status | Completed |
| Tests | `pytest`: 335 passed; DCF, multiples and scenario fixtures hand-worked; reviewer LLM pass mocked |
| Docs | ARCHITECTURE.md contracts 5.1–5.5 |
| Notes | SQLite schema v5 (reports, reviews, review findings); store refuses Completed without a passing review |

## Phase 6 — API, Dashboard & Research Workflow Automation

- [x] **Task 6.1** — Core FastAPI Research API
- [x] **Task 6.2** — Streamlit Research Dashboard
- [x] **Task 6.3** — Research History, Snapshot Diff & Refresh Workflow
- [x] **Task 6.4** — Watchlist, Scheduled Refresh & Alert Hooks
- [x] **Task 6.5** — End-to-End Autonomous Research Run

### Verification — Phase 6

| Item | Detail |
|---|---|
| Status | Completed |
| Tests | `pytest`: 361 passed (incl. FastAPI integration, Streamlit AppTest, refresh/diff, watchlist, E2E research runs); ruff, mypy --strict clean |
| Docs | ARCHITECTURE.md contracts 6.1–6.5; DEPLOYMENT env keys; README run commands |
| Notes | SQLite schema v6; valuation ids scoped per run; E2E runs fully offline (SEC MockTransport, scripted LLM, static prices) |

## Phase 7 — Hardening, CI/CD & Operations

- [x] **Task 7.1** — Point-in-Time Leakage Audit & Reproducibility Suite
- [x] **Task 7.2** — Observability, Cost Telemetry & Health Checks
- [ ] **Task 7.3** — Dockerization & Production Configuration
- [ ] **Task 7.4** — GitHub Actions CI/CD & Release Workflow
- [ ] **Task 7.5** — Backup, Restore & Operational Runbook

### Verification — Phase 7

| Item | Detail |
|---|---|
| Status | Not Started |
| Tests | — |
| Docs | — |
| Notes | — |

## Project Verification Log

Record task completion evidence, commands, test counts, and important implementation notes here as work proceeds.

- **Task 1.1** (2026-10-13): Package scaffold (src layout, pyproject, dev tools), .env.example, layer import guardrail test. `pytest`: 16 passed; ruff + mypy strict clean.

- **Task 1.2** (2026-10-13): Typed settings (`config.py`) with fail-fast `ConfigError`, SecretStr secrets, JSON/text logging with secret scrubbing and correlation/run context (`logging_setup.py`). 35 tests passed; ruff + mypy clean.

- **Task 1.3** (2026-10-13): Domain models (Company, Security, ResearchRun lifecycle, SourceDocument, ResearchFinding, FinancialFact, ValuationRun) with tz-aware UTC timestamps; SQLite repositories with immutable inserts and point-in-time queries. 59 tests passed; ruff + mypy clean.

- **Task 1.4** (2026-10-13): SecurityResolver (CIK/ISIN/ticker+MIC/name) with structured ambiguous/not-found results; curated JSON security master incl. multi-listed ASML/SHOP and dual-class GOOGL/GOOG. 88 tests passed; ruff + mypy clean.

- **Task 1.5** (2026-10-13): Provenance contract: inclusive published_at<=as_of visibility, FutureLeakageError, required URL/official id + SHA-256 for primary docs, content re-hash verification. 99 tests passed; ruff + mypy clean.

- **Task 2.1** (2026-10-13): SEC EDGAR adapter: filing enumeration by form/as_of with conservative availability time, polite HttpFetcher (UA, rate limit, bounded retries), document fetch with provenance; HTTP mocked. 114 tests passed; ruff + mypy clean.

- **Task 2.2** (2026-10-13): Issuer IR adapter with manual registration, approved official domains/URL patterns (Apple, Microsoft, ASML), redirect and scheme checks, secondary downgrade only when explicitly allowed. 127 tests passed; ruff + mypy clean.

- **Task 2.3** (2026-10-13): Normalized document model + HTML/text/paged-text/PDF-adapter parsers with stable chunk locators, structured ParseError; normalized_documents table (schema v2, additive migration). 147 tests passed; ruff + mypy clean.

- **Task 2.4** (2026-10-13): Ingestion service: hash verification, content dedupe (keeping earlier publications), immutable accessions, URL-based version chains, amendment links; content-addressed filesystem raw store. 157 tests passed; ruff + mypy clean.

- **Task 2.5** (2026-10-13): Snapshot builder: as_of filter, newest-visible version, hash dedupe to earliest publication, deterministic content-derived snapshot id, persisted immutable membership (schema v3). 163 tests passed; ruff + mypy clean.

- **Task 3.1** (2026-10-13): XBRL company-facts parser + concept mapper (priority tags, FY/Q classification, unit checks, point-in-time restatements, conflicts as issues, derived gross profit) with Apple FY2021-2023 fixture. 175 tests passed; ruff + mypy clean.

- **Task 3.2** (2026-10-13): KPI engine: 27 declared metrics + revenue CAGR with formula/input provenance and ok/missing_input/undefined status; Apple FY2023 values match hand calculations. 210 tests passed; ruff + mypy clean.

- **Task 3.3** (2026-10-13): Trend summaries, margin deltas (bps) and 8 quality-of-earnings diagnostics exposing formula/components/threshold; verified on Apple fixture and constructed red-flag case. 217 tests passed; ruff + mypy clean.

- **Task 3.4** (2026-10-13): Explicit versioned PeerSet (no model-selected peers), insert-only peer_sets table (schema v4) with point-in-time version lookup, comparable table + peer-only summary statistics with explicit missing exclusions. 223 tests passed; ruff + mypy clean.

- **Task 3.5** (2026-10-13): Market observation contract with point-in-time lookup, explicit Assumption model, traceable valuation primitives (EV bridge, multiples, discounting, terminal values, sensitivity grid) verified by hand-worked tests. 232 tests passed; ruff + mypy clean.

- **Task 4.1** (2026-10-13): LLM provider port, versioned prompts, StructuredLLMClient (JSON + schema validation, bounded retries, structured failures), scripted fake provider, Anthropic SDK adapter (optional extra) with mocked error mapping. 254 tests passed; ruff + mypy clean.

- **Task 4.2** (2026-10-13): Grounding layer (delimited chunks, injection guard, verbatim quote verification, number guard) and business/segment extractor; unsupported fields become explicit unknown findings. 263 tests passed; ruff + mypy clean.

- **Task 4.3** (2026-10-13): Change analyzer with side-specific before/after grounding, forward-looking guard for guidance, ambiguity/number rules -> uncertain; invented guidance rejected. 272 tests passed; ruff + mypy clean.

- **Task 4.4** (2026-10-13): Risk/catalyst/open-question extractor with classification + horizon, grounding-required evidence, Jaccard duplicate merging; ungrounded claims rejected. 277 tests passed; ruff + mypy clean.

- **Task 4.5** (2026-10-13): Evidence quality gate: snapshot membership, future-evidence, locator and verbatim-quote checks; blocking rejections vs. warning downgrades (partial evidence, low confidence, secondary-only facts). 288 tests passed; ruff + mypy clean.

- **Task 5.1** (2026-11-03): Peer table with point-in-time prices, market cap/EV/EV-sales/EV-EBITDA/PE/FCF-yield, peer-only stats and p25/median/p75 implied per-share ranges; every missing input recorded as a note, never imputed; traceable ValuationRun. 294 tests passed; ruff + mypy clean.

- **Task 5.2** (2026-11-03): Versioned DcfAssumptions (model-authored sets rejected), FCFF forecast, Gordon/exit-multiple TV, end/mid-year discounting, EV->equity->per-share bridge, WACC x terminal sensitivity grid; hand-worked two-year fixture. 305 tests passed; ruff + mypy clean.

- **Task 5.3** (2026-11-03): ScenarioCase (bull/base/bear) on shared reported base facts; every value labeled fact/assumption/calculation with provenance; blocking checks (complete set, documented assumptions, horizon, probabilities, value ordering) and warnings; probability-weighted value; traceable scenario ValuationRun. 312 tests passed; ruff + mypy clean.

- **Task 5.4** (2026-11-03): Stable report.v1 schema (fixed sections, labeled claims, provenance invariants), deterministic composer from gated findings/metrics/valuation runs/thesis points with full-provenance citations, metric/valuation id links, unsupported thesis points demoted to open questions; Markdown renderer. 320 tests passed; ruff + mypy clean.

- **Task 5.5** (2026-11-03): Deterministic reviewer (critical sections, unsupported/consensus claims, future/unknown/out-of-snapshot/provenance-mismatch/superseded/stale sources, metric/valuation traceability, value and text-calculation mismatch, invented numbers, scenario consistency, contradictions) plus warning-only LLM contradiction pass; apply_review -> Completed/NeedsInput; reports, reviews and review findings persisted (schema v5) with a store-level completion gate. 335 tests passed; ruff + mypy clean.

- **Task 6.1** (2026-11-03): ResearchService (resolve, create run with as_of guard, point-in-time documents/financials/KPIs, valuations, report+review+markdown), bootstrap composition root, FastAPI app with structured error envelope (validation/invalid/not_found/ambiguous); API integration tests on real SQLite. 339 tests passed; ruff + mypy clean.

- **Task 6.2** (2026-11-03): Thin Streamlit dashboard over the API (ApiClient + display-only views): company search, run launcher, progress, source explorer, KPI charts, valuation/scenario views, review + cited report reader; headless AppTest of the primary workflow and errors; real uvicorn + streamlit smoke run. 344 tests passed; ruff + mypy clean.

- **Task 6.3** (2026-11-03): Refresh creates a new immutable run (refreshed_from link, later as_of only); point-in-time RunState per run and order-independent diff of sources, KPIs, guidance, risks, catalysts, thesis, valuation points and assumption sets; API refresh/diff endpoints and dashboard History tab; valuation ids now scoped to their run (collision found by the refresh test). Separate fix commit registers EQUITYDESK_API_URL (the 6.2 commit had broken test_env_example). 347 tests passed; ruff + mypy clean.

- **Task 6.4** (2026-11-03): Watchlist entries, scheduler tick with atomic per-company refresh-slot claims (duplicate refreshes refused), in-progress guard, new-filing and run-finished alerts referencing runs/reports/source documents, idempotent persisted alerts, webhook sink with secrets from settings (never in payloads/errors), scheduler process + API endpoints; schema v6. 355 tests passed; ruff + mypy clean.

- **Task 6.5** (2026-11-03): ResearchWorkflow (Collecting->Parsing->Analyzing->Valuing->Reviewing) over FilingSource/ReportedFactsSource/MarketDataSource/LLM ports with SEC and static-price adapters, DCF base from reported facts, rule-based thesis skeleton, execute API endpoint and bootstrap wiring; E2E tests: happy path with point-in-time and citation invariants, missing assumptions, source outage, LLM failure, future document, reproducibility. 361 tests passed; ruff + mypy clean.

- **Task 7.1** (2026-11-03): 15-vector leakage matrix across snapshot, SEC index, workflow, mapper, API, market, peers, gate, composer, schema, reviewer, runs and history; frozen offline AAPL run compared against documented golden values (tests/fixtures/reproducibility). Shared E2E setup moved to tests/e2e_support.py. 377 tests passed; ruff + mypy clean.

- **Task 7.2** (2026-11-03): Per-run telemetry (stage timings, LLM tokens/cost from configured prices only, source/parser/finding counters) persisted (schema v7) and exposed; run-scoped log correlation; request correlation middleware + access log; /healthz, /ready (DB schema + data dir, 503 on failure), /health/detailed without secret values; secret-scrubbing tests. 381 tests passed; ruff + mypy clean.
