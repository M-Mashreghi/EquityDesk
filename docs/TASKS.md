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
- [ ] **Task 2.5** — Point-in-Time Research Snapshot Builder

### Verification — Phase 2

| Item | Detail |
|---|---|
| Status | In Progress (4/5) |
| Tests | — |
| Docs | — |
| Notes | — |

## Phase 3 — Financial Statements & Deterministic Analytics

- [ ] **Task 3.1** — Standardized Financial Fact Model & Statement Mapper
- [ ] **Task 3.2** — Historical KPI & Ratio Engine
- [ ] **Task 3.3** — Trend, Change & Quality-of-Earnings Diagnostics
- [ ] **Task 3.4** — Peer Set & Comparable Metrics Foundation
- [ ] **Task 3.5** — Valuation Primitives & Market Snapshot Contract

### Verification — Phase 3

| Item | Detail |
|---|---|
| Status | Not Started |
| Tests | — |
| Docs | — |
| Notes | — |

## Phase 4 — LLM Research Intelligence

- [ ] **Task 4.1** — Structured LLM Provider & Schema Validation
- [ ] **Task 4.2** — Business Model & Segment Evidence Extractor
- [ ] **Task 4.3** — Guidance, Strategy & Management-Language Change Analyzer
- [ ] **Task 4.4** — Risk, Catalyst & Open-Question Extractor
- [ ] **Task 4.5** — Evidence Validator & Research Object Quality Gate

### Verification — Phase 4

| Item | Detail |
|---|---|
| Status | Not Started |
| Tests | — |
| Docs | — |
| Notes | — |

## Phase 5 — Valuation, Scenarios, Thesis & Report Review

- [ ] **Task 5.1** — Peer Comparison & Multiples Valuation
- [ ] **Task 5.2** — DCF / Intrinsic Scenario Engine
- [ ] **Task 5.3** — Bull / Base / Bear Scenario Builder
- [ ] **Task 5.4** — Investment Thesis & Cited Research Report Composer
- [ ] **Task 5.5** — Independent Reviewer / Contradiction & Completeness Gate

### Verification — Phase 5

| Item | Detail |
|---|---|
| Status | Not Started |
| Tests | — |
| Docs | — |
| Notes | — |

## Phase 6 — API, Dashboard & Research Workflow Automation

- [ ] **Task 6.1** — Core FastAPI Research API
- [ ] **Task 6.2** — Streamlit Research Dashboard
- [ ] **Task 6.3** — Research History, Snapshot Diff & Refresh Workflow
- [ ] **Task 6.4** — Watchlist, Scheduled Refresh & Alert Hooks
- [ ] **Task 6.5** — End-to-End Autonomous Research Run

### Verification — Phase 6

| Item | Detail |
|---|---|
| Status | Not Started |
| Tests | — |
| Docs | — |
| Notes | — |

## Phase 7 — Hardening, CI/CD & Operations

- [ ] **Task 7.1** — Point-in-Time Leakage Audit & Reproducibility Suite
- [ ] **Task 7.2** — Observability, Cost Telemetry & Health Checks
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
