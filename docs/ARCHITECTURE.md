# EquityDesk Architecture

EquityDesk is a modular equity-research platform that produces point-in-time, evidence-backed company research from primary sources, deterministic financial analytics, transparent valuation, and structured LLM interpretation.

## High-Level Architecture

```text
                         ┌─────────────────────┐
                         │ Streamlit Dashboard │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │     FastAPI API     │
                         └──────────┬──────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │ Research Workflow / Service │
                     └──────────────┬──────────────┘
                                    │
       ┌────────────────────────────┼────────────────────────────┐
       ▼                            ▼                            ▼
┌───────────────┐          ┌─────────────────┐          ┌─────────────────┐
│ Source Layer  │          │ Analytics Layer │          │  LLM Research   │
│ SEC / IR etc. │          │ Financial/Value │          │ Evidence Extract│
└──────┬────────┘          └────────┬────────┘          └────────┬────────┘
       │                             │                            │
       └───────────────┬─────────────┴───────────────┬────────────┘
                       ▼                             ▼
              ┌────────────────┐           ┌──────────────────┐
              │ SQLite / Store │           │ Report + Reviewer│
              └────────────────┘           └──────────────────┘
```

## 1. Research Run

A research run is immutable enough to reproduce a report.

Minimum identity:

- `research_run_id`
- company/security id
- `as_of`
- creation timestamp
- source snapshot ids
- configuration version
- model/prompt versions
- valuation/scenario config
- final status

Suggested lifecycle:

`Created → Collecting → Parsing → Analyzing → Valuing → Reviewing → Completed`

Failure/review states may include `NeedsInput`, `BlockedBySource`, and `Failed`.

## 2. Company & Security Master

The company/security layer resolves user input into a canonical issuer and security.

Conceptual entities:

- `Company`
- `Security`
- `ExchangeListing`
- `Identifier` (ticker, CIK, ISIN when available)
- `PeerSet`

The system must not assume ticker alone is globally unique.

## 3. Source & Provenance Layer

### Source priority

1. regulator filings / official regulator APIs
2. issuer investor-relations releases
3. official investor presentations / prepared remarks
4. approved structured market/estimate providers
5. secondary context sources

Every acquired document receives:

- canonical source id
- type (`10-K`, `10-Q`, `8-K`, earnings release, presentation, etc.)
- source URL / official id
- `published_at`
- `retrieved_at`
- coverage period
- content hash
- raw payload/path reference according to environment policy
- parser version

Historical runs filter sources by `published_at <= as_of`.

## 4. Document Normalization

Parsers convert raw filings/documents into a common representation:

- metadata
- sections
- paragraphs / chunks
- tables where supported
- page/section locators
- plain text

Parser output preserves source locators so the report can cite evidence.

## 5. Financial Statement Engine

The deterministic financial layer standardizes reported facts into comparable periods and concepts.

Core tables:

- income statement
- balance sheet
- cash-flow statement
- selected per-share/share-count facts
- segment / geographic metrics when reliably available

Each fact records source and transformation provenance.

Deterministic analytics include:

- YoY / CAGR growth
- gross / operating / net margins
- FCF and cash conversion
- working-capital trends
- leverage/liquidity
- ROIC/ROE/ROA where definitions are documented
- share dilution/buyback metrics

## 6. LLM Research Layer

The LLM never acts as the numerical source of truth.

It consumes bounded, cited source text plus validated deterministic outputs and returns schema-validated findings:

- business model
- segments / key drivers
- management priorities
- strategy changes
- guidance and guidance changes
- risks
- catalysts
- management tone
- open questions
- evidence / confidence

Each finding references evidence spans.

## 7. Peer & Valuation Engine

### Peer comparables

Peer sets are explicit and versioned. Calculations may include:

- EV/Sales
- EV/EBITDA
- P/E
- FCF yield
- growth/margin comparison

Only metrics supported by the available data are calculated.

### DCF / intrinsic scenarios

DCF, when enabled, is deterministic and assumption-driven.

Inputs may include:

- revenue growth
- margins
- tax
- reinvestment / capex / working capital
- discount rate
- terminal growth / exit multiple
- net debt and diluted shares

The engine exposes sensitivity rather than hiding assumptions in prose.

## 8. Thesis & Scenario Layer

The research layer builds:

- core thesis
- evidence for / against
- catalysts
- risks
- open questions
- bull/base/bear assumptions
- scenario valuation outputs

A scenario assumption is always labeled as an assumption, not a fact.

## 9. Reviewer & Citation Layer

Before completion, the reviewer validates:

- citation coverage
- stale/future source leakage
- contradictions
- mismatches between prose and calculations
- unsupported guidance/consensus references
- valuation traceability
- scenario consistency

Material failures block report completion or produce explicit warnings.

## 10. Persistence

SQLite is the v1 system of record for:

- companies/securities
- research runs
- source metadata and document index
- normalized financial facts
- extracted findings
- valuation/scenario configurations
- report metadata

Large raw documents may use filesystem/object-store adapters; the database stores references and hashes.

## 11. API & Dashboard

FastAPI provides application contracts. Streamlit consumes application/API services.

UI may configure and display research, but business logic stays outside the UI.

Primary screens:

- company search
- research-run launch
- source/document explorer
- financial trends
- peer/valuation view
- thesis/scenario view
- final cited report
- research history / refresh diff

## Architectural Invariants

1. Point-in-time filtering is mandatory.
2. LLMs do not own numerical financial truth.
3. Material factual claims require provenance/evidence.
4. Valuation assumptions are transparent.
5. External providers are adapters.
6. UI is thin.
7. Research runs are reproducible.
8. No autonomous trading in v1.
