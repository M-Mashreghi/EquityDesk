# EquityDesk — Project Phases & Atomic Tasks

**Single source of truth** for task-by-task implementation of EquityDesk.

Status: `[ ]` Not Started · `[-]` In Progress · `[x]` Completed

## Global Definition of Done

Before any task is marked `[x]`:

1. Required automated tests pass.
2. Relevant regression tests pass.
3. Scope is limited to the task.
4. No secrets are committed.
5. Point-in-time and finance-integrity rules remain valid.
6. New material claims/calculations preserve provenance.
7. Documentation and task ledgers are synchronized.
8. External dependencies are justified by current scope.

## Phase 1: Foundation, Research Runs & Security Master

**Status:** `[x]` Completed

### Task 1.1: Python Project Scaffold & Architecture Guardrails

**Status:** `[x]` Completed

**Business Value / User Story**  
Create the Python 3.12+ package, dependency baseline, test layout, configuration shell, lint/type/test commands, and architecture import rules.

**Technical Specification**

- Implement the smallest modular capability required for **Python Project Scaffold & Architecture Guardrails**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Repository installs cleanly; smoke test passes; domain does not import UI/infrastructure; docs and example env exist.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 1.2: Configuration, Secrets & Structured Logging

**Status:** `[x]` Completed

**Business Value / User Story**  
Implement typed settings, environment loading, secret-safe logging, correlation/research-run context, and example configuration.

**Technical Specification**

- Implement the smallest modular capability required for **Configuration, Secrets & Structured Logging**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] No secret literals; invalid required config fails clearly; log scrub tests pass.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 1.3: Core Domain Models & SQLite Persistence

**Status:** `[x]` Completed

**Business Value / User Story**  
Create Company, Security, ResearchRun, SourceDocument, ResearchFinding, FinancialFact, ValuationRun and supporting persistence contracts/schema.

**Technical Specification**

- Implement the smallest modular capability required for **Core Domain Models & SQLite Persistence**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Fresh DB initializes; round-trip integration tests pass; timestamps are timezone-aware.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 1.4: Company / Security Resolver

**Status:** `[x]` Completed

**Business Value / User Story**  
Resolve user ticker/company input to canonical issuer/security identifiers with explicit ambiguity handling.

**Technical Specification**

- Implement the smallest modular capability required for **Company / Security Resolver**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Known fixtures resolve; ambiguous/missing input is structured; ticker alone is not assumed globally unique.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 1.5: Point-in-Time Source Provenance Contract

**Status:** `[x]` Completed

**Business Value / User Story**  
Define source metadata, content hash, published/retrieved timestamps, `as_of` filtering and immutable snapshot rules.

**Technical Specification**

- Implement the smallest modular capability required for **Point-in-Time Source Provenance Contract**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Future-source rejection tests pass; provenance is required for primary documents.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

## Phase 2: Primary-Source Collection & Document Pipeline

**Status:** `[x]` Completed

### Task 2.1: Regulatory Filing Adapter

**Status:** `[x]` Completed

**Business Value / User Story**  
Implement an official-regulator filing adapter for the initial US-equity scope with respectful headers/rate handling and test fixtures.

**Technical Specification**

- Implement the smallest modular capability required for **Regulatory Filing Adapter**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Curated issuer filings enumerate by type/date; `as_of` filter enforced; HTTP mocked in CI.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 2.2: Issuer Investor-Relations Adapter

**Status:** `[x]` Completed

**Business Value / User Story**  
Collect official earnings releases and investor materials through configurable issuer adapters/manual source registration.

**Technical Specification**

- Implement the smallest modular capability required for **Issuer Investor-Relations Adapter**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] At least one real issuer pattern plus fixtures; only approved official domains accepted by default.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 2.3: Document Parser & Normalized Document Model

**Status:** `[x]` Completed

**Business Value / User Story**  
Parse supported HTML/text/PDF-like inputs behind adapters into sections/chunks with source locators.

**Technical Specification**

- Implement the smallest modular capability required for **Document Parser & Normalized Document Model**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Fixture documents produce stable normalized sections; malformed input fails structurally.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 2.4: Document Hashing, Deduplication & Amendment/Version Links

**Status:** `[x]` Completed

**Business Value / User Story**  
Deduplicate identical content and retain filing amendments/new versions without destructive overwrite.

**Technical Specification**

- Implement the smallest modular capability required for **Document Hashing, Deduplication & Amendment/Version Links**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Hash dedupe and amendment/version tests pass; historical version remains queryable.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 2.5: Point-in-Time Research Snapshot Builder

**Status:** `[x]` Completed

**Business Value / User Story**  
Build the exact document set visible to a ResearchRun and persist snapshot membership.

**Technical Specification**

- Implement the smallest modular capability required for **Point-in-Time Research Snapshot Builder**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Snapshot is deterministic for frozen inputs; future docs excluded; source list reproducible.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

## Phase 3: Financial Statements & Deterministic Analytics

**Status:** `[x]` Completed

### Task 3.1: Standardized Financial Fact Model & Statement Mapper

**Status:** `[x]` Completed

**Business Value / User Story**  
Map sourced structured facts into normalized income statement, balance sheet and cash-flow concepts with provenance.

**Technical Specification**

- Implement the smallest modular capability required for **Standardized Financial Fact Model & Statement Mapper**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Hand-curated fixtures map periods/units correctly; incompatible facts are not silently merged.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 3.2: Historical KPI & Ratio Engine

**Status:** `[x]` Completed

**Business Value / User Story**  
Calculate growth, margins, FCF, leverage, liquidity, share-count, and documented capital-efficiency metrics.

**Technical Specification**

- Implement the smallest modular capability required for **Historical KPI & Ratio Engine**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Numerical fixtures match hand calculations; missing inputs return explicit missing values.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 3.3: Trend, Change & Quality-of-Earnings Diagnostics

**Status:** `[x]` Completed

**Business Value / User Story**  
Calculate multi-period trends, margin deltas, cash conversion, accrual/cash divergence and selected accounting red flags.

**Technical Specification**

- Implement the smallest modular capability required for **Trend, Change & Quality-of-Earnings Diagnostics**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Fixture trend outputs verified; diagnostics expose formula/components.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 3.4: Peer Set & Comparable Metrics Foundation

**Status:** `[x]` Completed

**Business Value / User Story**  
Represent explicit versioned peer sets and calculate comparable operating/valuation metrics from available deterministic data.

**Technical Specification**

- Implement the smallest modular capability required for **Peer Set & Comparable Metrics Foundation**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Peer membership/date visible; no hidden model-selected peers in v1; metric fixtures pass.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 3.5: Valuation Primitives & Market Snapshot Contract

**Status:** `[x]` Completed

**Business Value / User Story**  
Implement market snapshot inputs and reusable EV/equity bridge, multiple, discounting and sensitivity primitives.

**Technical Specification**

- Implement the smallest modular capability required for **Valuation Primitives & Market Snapshot Contract**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Valuation primitives pass hand-worked tests; observation date and assumptions required.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

## Phase 4: LLM Research Intelligence

**Status:** `[-]` In Progress

### Task 4.1: Structured LLM Provider & Schema Validation

**Status:** `[x]` Completed

**Business Value / User Story**  
Create provider abstraction, retries/timeouts, prompt versioning, schema parsing, and safe failure behavior.

**Technical Specification**

- Implement the smallest modular capability required for **Structured LLM Provider & Schema Validation**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Mocked valid/malformed/timeout tests pass; malformed output never enters domain silently.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 4.2: Business Model & Segment Evidence Extractor

**Status:** `[x]` Completed

**Business Value / User Story**  
Extract sourced descriptions of business model, revenue drivers, segments, customers/geography where supported.

**Technical Specification**

- Implement the smallest modular capability required for **Business Model & Segment Evidence Extractor**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Every accepted finding has source evidence; unsupported fields remain unknown.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 4.3: Guidance, Strategy & Management-Language Change Analyzer

**Status:** `[x]` Completed

**Business Value / User Story**  
Compare approved documents to identify sourced guidance changes, strategy changes and management-language shifts.

**Technical Specification**

- Implement the smallest modular capability required for **Guidance, Strategy & Management-Language Change Analyzer**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [x] Before/after evidence attached; no invented guidance; ambiguous changes marked uncertain.
- [x] Relevant unit/integration tests are green.
- [x] No unrelated refactor or unsupported dependency was introduced.
- [x] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 4.4: Risk, Catalyst & Open-Question Extractor

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Extract and classify risks, catalysts and unresolved questions with horizon/confidence/evidence.

**Technical Specification**

- Implement the smallest modular capability required for **Risk, Catalyst & Open-Question Extractor**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Duplicate findings normalized; evidence required; model memory cannot create claims.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 4.5: Evidence Validator & Research Object Quality Gate

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Validate source spans, snapshot membership, citation locators, confidence rules and material-claim support.

**Technical Specification**

- Implement the smallest modular capability required for **Evidence Validator & Research Object Quality Gate**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Unsupported/future evidence is rejected; quality-gate tests cover edge cases.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

## Phase 5: Valuation, Scenarios, Thesis & Report Review

**Status:** `[ ]` Not Started

### Task 5.1: Peer Comparison & Multiples Valuation

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Produce transparent peer tables and selected multiples-based valuation ranges using explicit observations and formulas.

**Technical Specification**

- Implement the smallest modular capability required for **Peer Comparison & Multiples Valuation**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Peer/date/input table exposed; numerical fixtures pass; missing data does not get imputed by LLM.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 5.2: DCF / Intrinsic Scenario Engine

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Implement deterministic DCF/scenario model with explicit forecast, WACC/discount and terminal assumptions plus sensitivity tables.

**Technical Specification**

- Implement the smallest modular capability required for **DCF / Intrinsic Scenario Engine**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Hand-verified DCF fixture passes; all assumptions serializable/versioned.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 5.3: Bull / Base / Bear Scenario Builder

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Combine explicit assumptions with deterministic models to produce scenario operating and valuation outputs.

**Technical Specification**

- Implement the smallest modular capability required for **Bull / Base / Bear Scenario Builder**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Facts vs assumptions labeled; scenario consistency checks pass; no hidden assumption.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 5.4: Investment Thesis & Cited Research Report Composer

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Compose company overview, financial analysis, valuation, thesis, risks, catalysts, scenarios and open questions from validated objects.

**Technical Specification**

- Implement the smallest modular capability required for **Investment Thesis & Cited Research Report Composer**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Material facts have citations/provenance; calculations link to metric IDs; report schema stable.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 5.5: Independent Reviewer / Contradiction & Completeness Gate

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Run deterministic and LLM-assisted review for unsupported claims, contradictions, stale/future sources, calculation-text mismatch and missing critical sections.

**Technical Specification**

- Implement the smallest modular capability required for **Independent Reviewer / Contradiction & Completeness Gate**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Blocking errors prevent Completed status; reviewer findings are persisted and test-covered.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

## Phase 6: API, Dashboard & Research Workflow Automation

**Status:** `[ ]` Not Started

### Task 6.1: Core FastAPI Research API

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Expose company lookup, research-run create/status, documents, financials, valuation and report endpoints through application services.

**Technical Specification**

- Implement the smallest modular capability required for **Core FastAPI Research API**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] API integration tests pass; validation errors use structured responses; UI logic absent.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 6.2: Streamlit Research Dashboard

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Build company search, run launcher, progress, source explorer, financial charts, valuation/scenario views and report reader.

**Technical Specification**

- Implement the smallest modular capability required for **Streamlit Research Dashboard**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Primary workflow works on desktop; dashboard delegates to services/API.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 6.3: Research History, Snapshot Diff & Refresh Workflow

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Allow new runs for later `as_of` dates and show what changed in sources, KPIs, guidance, risks, thesis inputs and valuation.

**Technical Specification**

- Implement the smallest modular capability required for **Research History, Snapshot Diff & Refresh Workflow**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Refresh creates new immutable run; previous run unchanged; diff tests pass.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 6.4: Watchlist, Scheduled Refresh & Alert Hooks

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Add watchlist and configurable refresh scheduling/notifications for new filings or completed research runs.

**Technical Specification**

- Implement the smallest modular capability required for **Watchlist, Scheduled Refresh & Alert Hooks**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Duplicate refreshes are guarded; alerts include run/source references; secrets stay external.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 6.5: End-to-End Autonomous Research Run

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Prove ticker → sources → financials → LLM findings → valuation → reviewer → report using deterministic fixtures/fakes for external services.

**Technical Specification**

- Implement the smallest modular capability required for **End-to-End Autonomous Research Run**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Happy path and key failures pass; point-in-time and citation invariants asserted.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

## Phase 7: Hardening, CI/CD & Operations

**Status:** `[ ]` Not Started

### Task 7.1: Point-in-Time Leakage Audit & Reproducibility Suite

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Add dedicated tests that attempt future-document/data leakage and verify frozen-input reproducibility.

**Technical Specification**

- Implement the smallest modular capability required for **Point-in-Time Leakage Audit & Reproducibility Suite**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Leakage matrix green; reproducibility fixture documented.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 7.2: Observability, Cost Telemetry & Health Checks

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Add structured logs, stage timing, LLM token/cost metadata, source/parser metrics, and liveness/readiness.

**Technical Specification**

- Implement the smallest modular capability required for **Observability, Cost Telemetry & Health Checks**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Secrets scrubbed; health endpoints/tests green; run correlation works.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 7.3: Dockerization & Production Configuration

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Create non-root container, Compose config, persistent data/cache mounts, env template, and health checks.

**Technical Specification**

- Implement the smallest modular capability required for **Dockerization & Production Configuration**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] docker compose config/build succeeds; health smoke documented.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 7.4: GitHub Actions CI/CD & Release Workflow

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Run lint/type/tests/security checks on PRs and build versioned images/releases only after quality gates.

**Technical Specification**

- Implement the smallest modular capability required for **GitHub Actions CI/CD & Release Workflow**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Failed quality blocks publish; secrets/permissions follow least privilege.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

### Task 7.5: Backup, Restore & Operational Runbook

**Status:** `[ ]` Not Started

**Business Value / User Story**  
Back up SQLite/research metadata, document raw-cache policy, restore drill, provider outage procedures and release checklist.

**Technical Specification**

- Implement the smallest modular capability required for **Backup, Restore & Operational Runbook**.
- Follow `CLAUDE.md`, architecture boundaries, and the applicable `.claude/rules/`.
- Preserve provenance and `as_of` semantics wherever source/research data is touched.
- Add fixtures/fakes so CI does not require paid/live external services.

**Definition of Done (DoD)**

- [ ] Restore drill succeeds on fixture backup; RUNBOOK covers critical incidents; Phase 7 closes.
- [ ] Relevant unit/integration tests are green.
- [ ] No unrelated refactor or unsupported dependency was introduced.
- [ ] `TASKS.md` and this SSOT are updated only after verification.

---

## Progress Summary

| Phase | Tasks | Completed |
|---|---:|---:|
| 1 — Foundation, Research Runs & Security Master | 5 | 5 |
| 2 — Primary-Source Collection & Document Pipeline | 5 | 5 |
| 3 — Financial Statements & Deterministic Analytics | 5 | 5 |
| 4 — LLM Research Intelligence | 5 | 3 |
| 5 — Valuation, Scenarios, Thesis & Report Review | 5 | 0 |
| 6 — API, Dashboard & Research Workflow Automation | 5 | 0 |
| 7 — Hardening, CI/CD & Operations | 5 | 0 |
| **Total** | **35** | **18** |

## Change Log

| Date | Change |
|---|---|
| 2026-10-13 | Initial EquityDesk planning baseline: 7 phases / 35 atomic tasks |
| 2026-10-13 | Task 1.1 completed and verified |
| 2026-10-13 | Task 1.2 completed and verified |
| 2026-10-13 | Task 1.3 completed and verified |
| 2026-10-13 | Task 1.4 completed and verified |
| 2026-10-13 | Task 1.5 completed and verified |
| 2026-10-13 | Task 2.1 completed and verified |
| 2026-10-13 | Task 2.2 completed and verified |
| 2026-10-13 | Task 2.3 completed and verified |
| 2026-10-13 | Task 2.4 completed and verified |
| 2026-10-13 | Task 2.5 completed and verified |
| 2026-10-13 | Task 3.1 completed and verified |
| 2026-10-13 | Task 3.2 completed and verified |
| 2026-10-13 | Task 3.3 completed and verified |
| 2026-10-13 | Task 3.4 completed and verified |
| 2026-10-13 | Task 3.5 completed and verified |
| 2026-10-13 | Task 4.1 completed and verified |
| 2026-10-13 | Task 4.2 completed and verified |
| 2026-10-13 | Task 4.3 completed and verified |
