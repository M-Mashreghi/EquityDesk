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

## Implementation Contracts

This section records the concrete contracts as tasks are implemented. It is updated in the same change set as the code.

### Package layout and dependency rules (Task 1.1)

`src/equitydesk/{domain,application,infrastructure,analytics,reporting,api,dashboard}`. Import direction is enforced by `tests/unit/test_architecture.py`: `domain` imports only the standard library and pydantic; `analytics` only `domain`; `application`/`reporting` never import infrastructure, HTTP, SQLite or LLM SDKs; `api` never imports `analytics`, SQLite or the dashboard; the dashboard never imports analytics, application, infrastructure or SQLite (it calls the API).

### Configuration and logging (Task 1.2)

`equitydesk.config.Settings` (typed, frozen) is the only place environment variables are read. `equitydesk.logging_setup` provides JSON/text logging with secret scrubbing and `correlation_id` / `research_run_id` context. Keys are listed in `docs/DEPLOYMENT.md`.

### Domain model and persistence (Task 1.3)

- All domain objects are frozen pydantic models (`domain/common.DomainModel`). Every timestamp is timezone-aware (`UtcDateTime`); naive datetimes are rejected and aware values are normalized to UTC.
- `ResearchRun` enforces `as_of <= created_at` and the lifecycle in `docs/DIAGRAMS.md` §3 (`ALLOWED_TRANSITIONS`); any non-terminal state may additionally move to `Failed`. Transitions return a new object and append to `status_history`.
- `ResearchFinding` carries `claim_type` (fact / inference / assumption / unknown) and `support_status`; a supported fact or inference must have at least one `EvidenceSpan`, and an unknown claim can never be supported.
- `FinancialFact` records issuer, concept, period, unit, source document/accession, locator, origin (reported / derived / normalized / adjusted) and `available_at` (point-in-time gate). Non-reported facts must list `derived_from`.
- `ValuationRun` persists method, formula, inputs, assumptions, outputs and version together; outputs without inputs are rejected.
- SQLite (`infrastructure/db/sqlite.py`): each table has indexed query columns plus the full validated object as JSON `payload` (lossless round-trip). Indexed timestamps are fixed-precision UTC text, so text comparison equals time comparison. Foreign keys are enforced; schema version is stored in `schema_meta`. Source documents, findings, facts and valuations are insert-only: re-inserting identical content is a no-op, different content under the same id raises `ImmutableRecordError`. Research runs may change status, but not identity fields, and never after reaching a terminal status.

### Company / security resolution (Task 1.4)

`application/resolver.SecurityResolver` resolves a query against a `SecurityMaster` port (v1 adapter: `infrastructure/sources/security_master.JsonSecurityMaster`, curated file `data/fixtures/security_master.json`, which can seed the SQLite company tables). Matching order: CIK → ISIN → ticker (optionally `TICKER:MIC` / `MIC:TICKER` or an `exchange` argument) → exact normalized name/alias → partial name. Results are structured (`resolved` / `ambiguous` / `not_found` / `invalid_input`) with candidates and suggestions. A ticker listed on several exchanges is always ambiguous without a MIC. Issuer-level matches pick the single flagged primary listing and record that choice in `notes`. Partial name matches are never auto-resolved.

### Point-in-time provenance (Task 1.5)

`domain/provenance.py` and the `SourceDocument` validator define the contract:

1. **Visibility** — a document is visible to a run iff `published_at <= as_of` (inclusive). `retrieved_at` is audit metadata only. `split_as_of()` applies the same rule to any timestamped item (e.g. `FinancialFact.available_at`). A naive `as_of` is rejected. `assert_no_future_documents()` raises `FutureLeakageError` listing offending ids.
2. **Required provenance** — primary documents (regulatory filing, issuer IR, issuer presentation) must have `source_url` or `official_id` and a SHA-256 `content_hash`; `retrieved_at >= published_at`; a `version > 1` must reference the document it supersedes.
3. **Immutability** — stored documents are insert-only (Task 1.3) and `verify_content()` re-hashes raw bytes against the recorded hash.

### Regulatory filing adapter (Task 2.1)

`infrastructure/sources/sec_edgar.SecEdgarAdapter` (SEC EDGAR, US-equity scope) enumerates filings from the submissions feed (optionally following history pages), filtered by form type (default: 10-K, 10-Q, 8-K, 20-F, 40-F and amendments) and by `as_of`, then downloads primary documents into `SourceDocument` records (`document_id = sec_<accession>`, `official_id = accession`, SHA-256 hash, archive URL). `company_facts()` reads the XBRL company-facts API for Phase 3.

- **Availability time:** `acceptanceDateTime` is interpreted as New York wall-clock time (the feed's `Z` suffix is unreliable) and never earlier than 00:00 New York on the official filing date. Both choices can only make a filing appear *later*, never earlier, so they cannot leak future information. `retrieved_at` is clamped to be no earlier than this conservative `published_at`.
- **Politeness:** `infrastructure/sources/http.HttpFetcher` sends the configured `SEC_USER_AGENT` (required), enforces a process-wide minimum interval (`SEC_MAX_REQUESTS_PER_SECOND`, ≤ 10), retries only 429/5xx/transport errors with exponential backoff (honouring `Retry-After`, capped at 60 s), and raises `SourceUnavailableError` after the bound. 4xx errors are not retried.
- **Tests:** all HTTP is served by `httpx.MockTransport` from `tests/fixtures/sec/` (synthetic subset of the EDGAR format).

### Issuer investor-relations adapter (Task 2.2)

`infrastructure/sources/investor_relations.IssuerIRAdapter` registers official IR documents from an explicit `ManualSourceRegistration` (company, URL, timezone-aware `published_at`, optional document type), because IR sites rarely expose reliable machine-readable timestamps. Per-issuer configuration lives in `data/fixtures/ir_sources.json` (approved official domains + URL patterns; Apple newsroom earnings releases and financial-statement PDFs, Microsoft earnings pages, ASML press releases).

- Only `https` URLs on an approved domain (or its subdomains), without credentials or non-standard ports, are accepted. Redirects off the approved domains are rejected.
- Unapproved URLs are rejected by default; with `allow_unapproved=True` they are stored as `secondary`, so they can never pose as primary sources.
- Matching patterns supply the document type; a `published_at` earlier than the date encoded in the URL is rejected as inconsistent.
- `document_id = ir_<sha256(url)[:16]>_<content-hash[:12]>`: the same URL with changed content becomes a new document (versioning is handled by Task 2.4).

### Document parsing and normalized model (Task 2.3)

`domain/normalized.NormalizedDocument` = ordered `Section`s (heading, level, SEC `item_code` such as `1A`/`7`), `Chunk`s (text, section, ordinal, optional page, character offsets into `plain_text`) and `Table`s (cell rows + the chunk that cites them). Chunk ids `<document_id>:s<section>:p<paragraph>` are the evidence locators used by research findings and are deterministic for a given content + parser version (verified by tests).

Parsers (`infrastructure/parsers/`, standard library only): `HtmlDocumentParser` (block tags, `<h1-6>` and SEC `PART`/`Item N.` headings, tables, skips script/style/comments, entity decoding, UTF-8 with cp1252 fallback), `PlainTextParser`, `PagedTextParser` (form-feed page breaks → page locators), `PdfParser` (delegates text extraction to an injected adapter; with none configured a PDF fails with `unsupported_format`). `ParserRegistry` dispatches by MIME type or content sniffing. Paragraphs longer than 2,000 characters are split at sentence boundaries. Every failure is a structured `ParseError(document_id, code)` (`empty_content`, `binary_content`, `decode_error`, `malformed`, `no_text`, `unsupported_format`), never a partial document.

Parsed output is stored in `normalized_documents` keyed by (document, parser name, parser version) — schema v2, applied additively to v1 databases; re-parsing with the same parser must reproduce identical output.
