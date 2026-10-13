# EquityDesk Implementation Phases

Each phase defines scope, deliverables, technical challenges, Definition of Done, and test expectations.

Related: [ROADMAP](./ROADMAP.md) · [ARCHITECTURE](./ARCHITECTURE.md) · [PROJECT_PHASES_AND_TASKS](./PROJECT_PHASES_AND_TASKS.md) · [TASKS](./TASKS.md)

## Phase 1 — Foundation, Research Runs & Security Master

**Goal:** Establish the Python modular-monolith foundation, SQLite persistence, canonical company/security identity, configuration, and point-in-time research-run model.

**Deliverables:** project scaffold, domain models, DB, security resolver, source-provenance schema.

**Challenges:** ticker ambiguity, time semantics, clean boundaries.

**DoD:** clean setup works; company/security and research-run persistence tested; future-source filtering contract defined.

## Phase 2 — Primary-Source Collection & Document Pipeline

**Goal:** Collect official filings and issuer IR materials, normalize them, hash/version them, and preserve source locators.

**Deliverables:** regulator adapter, IR adapter, parser abstraction, normalized document model, dedupe/versioning, refresh job.

**Challenges:** document formats, amended filings, availability timestamps, HTML/table extraction.

**DoD:** curated companies can build a point-in-time document snapshot with provenance and deterministic fixture tests.

## Phase 3 — Financial Statements & Deterministic Analytics

**Goal:** Build standardized historical financials and reproducible KPI/ratio calculations.

**Deliverables:** statement normalization, accounting fact provenance, KPI engine, trend engine, peer-data interface, valuation primitives.

**Challenges:** concept mapping, units, periods, restatements, industry differences.

**DoD:** hand-verified fixtures pass; reports never rely on LLM math.

## Phase 4 — LLM Research Intelligence

**Goal:** Extract evidence-backed qualitative research from approved source snapshots.

**Deliverables:** structured LLM client, business/segment findings, guidance/tone change analysis, risks/catalysts, evidence validator.

**Challenges:** hallucination, prompt injection in documents, ambiguous language, citation locators.

**DoD:** all accepted material findings validate schema + evidence; insufficient evidence becomes uncertain.

## Phase 5 — Valuation, Scenarios, Thesis & Report Review

**Goal:** Combine validated facts, calculations, and explicit assumptions into transparent equity research.

**Deliverables:** peer comps, DCF/scenario engine, thesis builder, cited report composer, contradiction/reviewer gate.

**Challenges:** false precision, peer selection, scenario consistency, fact/inference separation.

**DoD:** a curated company can generate a cited report with traceable valuation and reviewer checks.

## Phase 6 — API, Dashboard & Research Workflow Automation

**Goal:** Turn the engine into a usable research product.

**Deliverables:** FastAPI, Streamlit dashboard, research-run history, source explorer, watchlist/refresh, report export, E2E run.

**Challenges:** long-running jobs, user inputs, keeping UI thin, refresh diffs.

**DoD:** user can launch and inspect an end-to-end run without editing code.

## Phase 7 — Hardening, CI/CD & Operations

**Goal:** Make the project reproducible, observable, deployable, and safe to operate.

**Deliverables:** point-in-time audit suite, structured logs/health, Docker, GitHub Actions, backup/restore, runbook.

**Challenges:** external-provider failures, reproducibility, secret handling, cache/storage hygiene.

**DoD:** CI green, Docker health green, restore drill documented, release checklist complete.

## Cross-Phase Quality Gates

After every task:

1. tests pass,
2. task DoD is satisfied,
3. ledgers are updated,
4. finance/point-in-time rules remain valid,
5. no unsupported source or hidden assumption is introduced,
6. docs remain synchronized.
