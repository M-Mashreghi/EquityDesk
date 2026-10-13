# EquityDesk Roadmap

Strategic roadmap for **EquityDesk — Autonomous Equity Research Analyst**.

Related: [ARCHITECTURE.md](./ARCHITECTURE.md) · [PHASES.md](./PHASES.md) · [DIAGRAMS.md](./DIAGRAMS.md) · [TASKS.md](./TASKS.md)

## 1. Product Vision

EquityDesk reduces the mechanical work of equity research while keeping evidence, finance math, assumptions, and analyst judgment inspectable.

A user supplies a company/ticker and `as_of` date. EquityDesk should autonomously collect approved primary sources, normalize financial history, extract evidence-backed qualitative findings, compute valuation, build scenarios, review contradictions, and produce a cited research memo.

## Value Proposition

| User | Value |
|---|---|
| Equity-research learner | Learns a professional research workflow instead of relying on chatbot summaries. |
| Analyst / researcher | Reduces document collection, extraction, repetitive calculations, and report assembly. |
| Quant / finance recruiter | Sees finance reasoning, LLM workflow, data engineering, valuation, and reproducibility in one project. |
| Developer | Gets modular adapters, deterministic analytics, tests, API, dashboard, and deployment. |

## Non-Goals

- live autonomous trading
- personalized buy/sell advice
- hidden black-box price targets
- invented consensus estimates
- institution-wide coverage across all jurisdictions in v1

## 2. Release Horizons

### M1 — Research Spine (Alpha)

**Theme:** Identity, persistence, source provenance, and research-run lifecycle.

Unlock: a ticker resolves to a canonical company/security and an `as_of` research run with auditable source metadata.

### M2 — Fundamentals & Documents (Beta)

**Theme:** Primary-source ingestion, parsing, standardized financials, and deterministic KPIs.

Unlock: a company has a reproducible historical fundamentals dataset tied to filings.

### M3 — Research Intelligence & Valuation (RC)

**Theme:** Structured LLM findings, peer comps, DCF/multiples, scenarios, reviewer, and cited report.

Unlock: one command produces a reviewable research package instead of disconnected metrics.

### M4 — Product & Production (v1.0)

**Theme:** API/dashboard, refresh workflows, observability, Docker, CI/CD, backups, and operational runbook.

Unlock: repeatable daily use and demonstrable portfolio-quality software engineering.

## 3. Core KPIs

| KPI | Target for v1 | Definition |
|---|---:|---|
| Primary-source provenance coverage | 100% | Every ingested primary document has source id/url, publication/retrieval time, type, hash. |
| Material factual claim citation coverage | ≥ 98% | Share of report material facts linked to source evidence or deterministic calculation provenance. |
| Structured extraction validity | ≥ 99% | LLM responses accepted by schema without manual repair after configured retry policy. |
| Financial calculation fixture accuracy | 100% | Deterministic unit fixtures match hand-verified expected values. |
| Point-in-time leakage tests | 100% pass | Historical research rejects future sources/data. |
| Report reproducibility | 100% on release fixtures | Same frozen inputs/config produce equivalent structured research outputs. |
| Research-run completion | ≥ 90% on curated US large-cap test set | Runs reach completed report without manual intervention, excluding unavailable upstream sources. |

## Supporting Metrics

- source acquisition success rate
- parser failure rate by document type
- unresolved security/entity rate
- financial fact coverage by period
- evidence rejection / uncertainty rate
- valuation missing-input rate
- reviewer warning count
- average research-run duration
- LLM token/cost per run

## 4. Phase Mapping

| Horizon | Phases |
|---|---|
| M1 Alpha | Phase 1 |
| M2 Beta | Phases 2–3 |
| M3 RC | Phases 4–5 |
| M4 v1.0 | Phases 6–7 |

## 5. Strategic Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Filing/parser drift | Missing or wrong facts | Adapter isolation, fixtures, provenance, graceful missing values. |
| LLM hallucination | False research claims | Structured outputs, evidence validator, uncertainty, reviewer gate. |
| Future-data leakage | Invalid historical analysis | `as_of` filtering and dedicated leakage tests. |
| Accounting concept mismatch | Bad ratios | Normalization metadata, explicit definitions, unit/period validation. |
| Valuation false precision | Misleading outputs | Assumption visibility, ranges, sensitivity tables, disclaimers. |
| Paid-data dependency creep | Unreproducible repo | Official/free sources first, adapters for optional providers. |
| Scope explosion | Delayed MVP | US public equities first; modular expansion later. |

## 6. Future Horizon

Post-v1 possibilities, not committed scope:

- non-US regulator adapters
- earnings-call transcript integration
- estimate-provider adapters
- sector-specific accounting templates
- forensic accounting module
- portfolio/watchlist research prioritization
- news-to-thesis update linkage
- multi-agent investment committee built on top of validated research objects
