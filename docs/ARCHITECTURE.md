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

### Hashing, deduplication and versions (Task 2.4)

`application/document_store.DocumentIngestionService.ingest(document, content)`:

1. Re-hashes the bytes (SHA-256) and rejects a mismatch with the declared hash.
2. An official id (SEC accession) already stored with the same bytes → `duplicate`; with different bytes → `ProvenanceError` (accessions are immutable upstream).
3. Identical bytes already stored for the company → `duplicate` of the existing record, unless the new record proves an *earlier* publication; then both are kept (the snapshot builder dedupes by hash) so earlier availability is never lost.
4. Same `source_url` with new bytes (IR page edits) → `new_version` with `version = latest + 1` and `supersedes_document_id`; a version back-dated before its predecessor is rejected.
5. `X/A` forms → `amendment`, linked by `amends_document_id` to the latest original `X` for the same `period_end` published before it (or flagged when none is stored).

Nothing is overwritten: every version stays queryable (`version_chain()`). Raw bytes go to `infrastructure/db/raw_store.FilesystemRawStore` — content-addressed (`<root>/<hash[:2]>/<hash>`), atomic writes, hash verified on every read; `SourceDocument.raw_ref` holds the hash.

### Point-in-time research snapshot (Task 2.5)

`application/snapshot.SnapshotBuilder.build(run)` selects the exact document set a run may use (`domain/snapshot.select_snapshot_documents`):

1. documents with `published_at <= as_of` only;
2. within a version chain, only the newest version already visible at `as_of` (a later correction never leaks backwards; the old version is kept for the earlier date);
3. byte-identical documents collapse to the earliest publication;
4. amendments and originals are both members (distinct filings).

Members are ordered by `(published_at, document_id)`. Excluded ids are recorded by reason (`future`, `superseded`, `duplicate`). `snapshot_id = snap_<sha256(company, as_of, builder version, sorted members)>`, so frozen inputs always produce the same id and member list, independent of input order. Membership is persisted (`research_snapshots`, `snapshot_documents`; schema v3) and immutable: rebuilding a run returns the stored snapshot even after newer documents arrive.

### Financial statement mapping (Task 3.1)

- `infrastructure/sources/xbrl_facts.parse_company_facts()` flattens SEC company-facts JSON into `domain.financials.RawReportedFact` (taxonomy, tag, unit, value, period, accession, form, filed date, `available_at`). Availability comes from the submissions feed acceptance time when supplied, else the end of the filing day (New York).
- `analytics/accounting.map_reported_facts(company_id, raw, as_of)` maps ~35 standard concepts (`CONCEPTS`: income statement, balance sheet, cash flow, per-share, share counts) from priority-ordered XBRL tags into `FinancialFact`s:
  - only facts with `available_at <= as_of`;
  - durations classified as fiscal year (330–380 days) or quarter (80–100 days); YTD and other durations are skipped;
  - unit kind must match the concept (monetary ISO currency / `shares` / `CCY/shares`), otherwise `unit_mismatch`; different currencies for the same concept and period are kept apart and flagged `multiple_units`;
  - per (concept, period, unit): highest-priority tag, then the latest filing visible at `as_of` (restatements apply only once public, flagged `restated`); disagreeing lower-priority tags are flagged `tag_conflict`;
  - fact ids are deterministic; `GrossProfit` is derived (`origin=derived`, `derived_from`) only when not reported;
  - `fiscal_year` for annual facts is the calendar year of the period end (approximation for non-calendar fiscal years).
- `annual_statements(facts, currency)` gives a fiscal-year view (FY durations plus balance-sheet instants at the FY end) without mixing currencies.

### KPI and ratio engine (Task 3.2)

`analytics/kpis.py` declares every metric as `MetricDef(name, unit, formula, inputs, fn)` and evaluates it per fiscal year against the prior fiscal year (330–380 days earlier). Results are `domain.metrics.MetricResult` with a stable `metric_id` (`m:<company>:<name>:<period_end>`), formula text, input values, input fact ids, `calc_version` and a status:

- `ok` — value computed;
- `missing_input` — a required input fact is absent (named in `missing`); nothing is imputed;
- `undefined` — inputs exist but the formula is meaningless (division by zero, growth from a non-positive base, CAGR with non-positive endpoints).

Metrics: revenue / diluted-EPS / FCF growth, gross / operating / net / FCF margins, EBITDA (= operating income + D&A), FCF (= OCF − capex), effective tax rate, total debt (= long-term + current debt + commercial paper), net debt (− cash − short-term investments), net debt / EBITDA, debt / equity, current and quick ratios, interest coverage, ROE and ROA (on average balances), ROIC (= operating income × (1 − effective tax rate) / average invested capital, invested capital = equity + total debt − cash − short-term investments), diluted-share and shares-outstanding change, DSO / DIO / DPO / cash-conversion cycle (365-day basis), cash conversion (OCF / net income), revenue CAGR. Optional debt/cash components that a filer does not report count as zero and are listed in `missing` with a note.

### Trends and quality-of-earnings diagnostics (Task 3.3)

`analytics/diagnostics.py`:

- `trend(metric_results, name)` → `TrendSummary` (points, first/last, change — in bps for ratios —, direction up/down/flat with a 5 bps flat band, consecutive increases/declines). Fewer than two computable points → `insufficient_data`.
- `margin_deltas_bps()` → year-over-year change of gross / operating / net / FCF margins in bps.
- `run_diagnostics(company_id, statements)` → `DiagnosticResult`s exposing formula, components (input values), threshold, comparison, `triggered` and status (`missing_input` / `undefined` leave `triggered = None`). Heuristic thresholds:

| Diagnostic | Formula | Triggers when |
|---|---|---|
| `low_cash_conversion` | OCF / NI (NI > 0 only) | < 0.8 |
| `high_accruals` | (NI − OCF) / avg total assets (Sloan) | > 0.10 |
| `receivables_outpacing_revenue` | growth(AR) − growth(revenue) | > 0.10 |
| `inventory_outpacing_revenue` | growth(inventory) − growth(revenue) | > 0.10 |
| `negative_fcf_with_profit` | OCF − capex (NI > 0 only) | < 0 |
| `share_dilution` | diluted shares growth | > 2% |
| `high_sbc_share_of_ocf` | SBC / OCF (OCF > 0 only) | > 25% |
| `operating_margin_compression` | Δ operating margin (bps) | < −200 bps |

A triggered diagnostic is a prompt for investigation, not a conclusion.

### Peer sets and comparable metrics (Task 3.4)

- `domain/peers.PeerSet` is explicit and versioned: `name`, `version`, `target_company_id`, members with a written rationale, `selection_method` (`user_defined` / `rule_based`), `created_by`, `created_at`, `effective_from`. The target cannot be its own peer, members are unique, and a `created_by` starting with `llm`/`model`/`ai:` is rejected (no hidden model-selected peers in v1). Membership may be applied to historical dates (an analyst choice); peer *values* are always point-in-time.
- SQLite `peer_sets` (schema v4) is insert-only: versions must be consecutive, an existing version cannot be rewritten, and `effective_as_of(name, as_of)` returns the latest version effective at that date.
- `analytics/peers.py`: `comparable_table()` takes each company's latest fiscal-year `MetricResult`s (default: revenue growth, gross / operating / net / FCF margin, ROIC, net debt / EBITDA), keeps the period end and metric id per cell, computes `summary_stats()` over peers only (n, median, mean, min, max, p25, p75 with linear-interpolation percentiles), lists peers excluded for missing values, and notes peers whose fiscal period is more than 120 days from the target's.

### Valuation primitives and market snapshot contract (Task 3.5)

- `domain/market.MarketObservation`: security, positive price, currency, timezone-aware `observed_at`, `price_source`; a share count requires `shares_source` and `shares_as_of` (not later than the observation). `observation_as_of()` returns the latest observation at or before `as_of` and raises `FutureLeakageError` when only later prices exist.
- `domain/valuation.Assumption`: name, value, unit, written rationale and a source that must be `user:`, `rule:` or `scenario:` (a model can never be the source of an assumption).
- `analytics/valuation.py` primitives return `Computation(name, value, formula, inputs, components)`: market cap, enterprise value (= market cap + debt + preferred + minority − cash & investments) and the inverse equity bridge, per-share value, multiples (non-positive denominator → `Undefined`), applying a multiple, discount factors (end- or mid-year), present value, Gordon terminal value (requires r > g), exit-multiple terminal value, and `sensitivity_table()` (undefined cells are `None`). `build_market_bridge()` pins market cap / EV to one dated observation, explicit balance-sheet inputs and the assumptions used.

### Structured LLM provider (Task 4.1)

- Port: `application/llm.LLMProvider.complete(LLMRequest) -> LLMResponse`; provider errors are classified as `LLMTimeoutError`, `LLMTransientError` (retryable), `LLMPermanentError` and `LLMRefusalError` (not retried).
- `PromptTemplate(prompt_id, version, system, user_template)` — prompts are versioned (`prompt_ref = id@version`); missing template variables fail before any call.
- `StructuredLLMClient.generate(prompt, variables, schema, schema_version)` sends a tightened JSON schema (`strict_json_schema`: closed objects, all keys required), parses JSON (tolerating a code fence), validates with the pydantic schema, and retries malformed JSON, schema violations, truncation (`max_tokens`), timeouts and transient errors with exponential backoff up to `LLM_MAX_RETRIES`. It returns `StructuredResult` — a validated value **or** a `StructuredFailure` (`malformed_json`, `schema_violation`, `timeout`, `provider_error`, `refused`, `truncated`) plus model, prompt ref, schema version, attempts and token usage. Raw model text never reaches the domain.
- Adapters: `infrastructure/llm/fake.ScriptedLLMProvider` (offline, deterministic; used in CI) and `infrastructure/llm/anthropic_provider.AnthropicProvider` (official `anthropic` SDK, optional extra `.[llm]`; default model `claude-opus-5-5`, `output_config.format` JSON schema, SDK retries disabled so the client owns the retry policy, server-side refusal fallbacks `fallbacks: "default"` enabled by default). CI never calls a live model.

### Evidence grounding and business-model extraction (Task 4.2)

`application/research/grounding.py` is shared by all extractors:

- Chunks from snapshot documents are rendered as `<chunk id="…" section="…">…</chunk>` within a character budget (never split mid-chunk); the system prompt includes an injection guard (document text is data, never instructions; no outside knowledge; verbatim quotes; no computed numbers; unsupported fields go to `not_found`).
- Every cited `(chunk_id, quote)` is re-verified: the chunk must be one actually sent, and the quote (≥ 12 chars) must occur in the chunk text (whitespace/quote-style/case-normalized). Unverifiable citations are dropped; a claim with no verified citation is **rejected**.
- A grounded claim becomes a `ResearchFinding` (fact/inference, evidence spans with document, section, page) that is `supported`, or `uncertain` when confidence < 0.5, when any citation failed, or when the statement contains numbers that do not appear in its cited quotes (the model cannot introduce numbers).
- Finding ids are deterministic (run, category, statement); model, prompt ref and schema version are stored on each finding.

`application/research/business.BusinessModelExtractor` (prompt `business_model_extractor@1.0.0`, schema `business_extraction.v1`) extracts business model, segments, revenue drivers, customers and geography. Any field without a grounded claim — or every field, if the LLM call fails — is emitted as an explicit `unknown` / `not_supported` finding without evidence.

### Guidance, strategy and tone change analyzer (Task 4.3)

`application/research/changes.ChangeAnalyzer` (prompt `guidance_strategy_tone_changes@1.0.0`, schema `change_analysis.v1`) compares a prior and a current document (the prior must be published first). Each change carries type (guidance / strategy / tone), topic, direction (raised, lowered, maintained, introduced, withdrawn, shifted, unclear), before/after statements with evidence and a summary. Acceptance rules:

- `before` evidence must ground in the **prior** document's chunks and `after` evidence in the **current** document's chunks; both sides are required except `introduced` (after only) and `withdrawn` (before only). Otherwise the change is rejected — so guidance can never be invented.
- Guidance must be quoted from forward-looking language (expect, outlook, guidance, anticipate, …); otherwise `uncertain`.
- `unclear` direction, confidence < 0.5, numbers not present in the quotes, or any dropped citation → `uncertain`.
- Accepted changes are `inference` findings (`guidance_change`, `strategy_change`, `management_tone`) with before/after statements, document ids and chunk ids in `attributes`.

### Risks, catalysts and open questions (Task 4.4)

`application/research/risks.RiskCatalystExtractor` (prompt `risk_catalyst_open_questions@1.0.0`, schema `risk_catalyst_extraction.v1`) returns risks and catalysts with title, statement, classification (supply_chain, competition, regulatory, legal, macro, geopolitical, product, technology, financial, customer, operational, other), horizon (near < 12 months, medium 1–3 years, long > 3 years, unspecified), confidence and evidence; and open questions with the evidence that prompted them.

- Every item goes through the grounding rules of Task 4.2; items without verifiable quotes are rejected, so model memory cannot create a risk, catalyst or question.
- Duplicates within risks or catalysts are normalized by title-token Jaccard similarity ≥ 0.6 (stop words and plural `s` removed): evidence is merged, the higher confidence kept, merged titles recorded.
- Open questions are always stored as `uncertain` inferences — never supported facts.

### Evidence validator and quality gate (Task 4.5)

`application/research/quality_gate.EvidenceQualityGate.validate(run_id, as_of, snapshot, findings, documents, normalized)` re-checks every finding independently of how it was produced:

| Check | Effect |
|---|---|
| finding belongs to another run | **blocking** — rejected |
| evidence document unknown, published after `as_of`, or not a snapshot member | span dropped |
| chunk locator does not exist in the normalized document | span dropped |
| quote not found verbatim in the chunk | span dropped |
| fact/inference with no surviving span | **blocking** — rejected |
| some spans dropped | warning — downgraded to `uncertain` |
| confidence below 0.5 | warning — `uncertain` |
| fact supported only by secondary sources | warning — `uncertain` (source hierarchy) |

Accepted findings carry the surviving evidence and the issue codes in `attributes.quality_issues`; the `QualityReport` lists accepted, rejected and all issues (with severity).

### Peer comparison and multiples valuation (Task 5.1)

`analytics/multiples.py` builds a point-in-time peer table and multiples-based ranges from explicit inputs only:

- `CompanyFundamentals` carries trailing revenue, EBITDA, net income, FCF, total debt, cash & investments, diluted shares, the fiscal period end and `sources` (metric / fact ids per input).
- `build_row()` picks the latest `MarketObservation` at or before `as_of` (`FutureLeakageError` if only later prices exist) and computes market cap, EV and `ev_sales`, `ev_ebitda`, `pe` (`market_cap / net_income`) and `fcf_yield`. A missing share count, debt or cash leaves market cap / EV as `None`; a non-positive denominator makes the multiple `None` with a note.
- `multiples_valuation(target, peers, observations, as_of, peer_set_id)` computes peer-only `summary_stats` and, for each applied multiple, implied per-share values at the peer p25 / median / p75 (`(multiple × target metric − debt + cash) / diluted shares` for EV multiples, `P/E × net income / diluted shares` for P/E). A range is produced only with at least two peer values, a positive target metric and sourced target shares / net-debt inputs; every blocking reason is recorded in `notes` — nothing is imputed, by a model or otherwise.
- Observations older than 7 days before `as_of`, or more than 7 days apart from the target's, are noted.
- `to_valuation_run()` persists the full peer/date/input table, the percentiles and outputs as a `ValuationRun` (`method=multiples`) with a content-derived id.

### DCF / intrinsic scenario engine (Task 5.2)

- `domain/valuation.DcfAssumptions` is a versioned, JSON-serializable assumption set (`assumption_set_id`, `version`, `source` = `user:` / `rule:` / `scenario:`, per-year `revenue_growth` and `ebit_margin`, `tax_rate`, `da_pct_revenue`, `capex_pct_revenue`, `nwc_pct_incremental_revenue`, `discount_rate`, `terminal_method` = `gordon` (needs `terminal_growth` < discount rate) or `exit_multiple` (needs `exit_ev_ebitda`), `mid_year`, written `rationale`). Invalid or model-authored sets are rejected at construction.
- `analytics/dcf.DcfBase` holds the reported starting point (base revenue, debt, cash, diluted shares, period end, `sources`).
- `run_dcf(base, assumptions)` forecasts revenue → EBIT → NOPAT → FCFF (`nopat + d&a − capex − Δnwc`), discounts with end- or mid-year factors, adds a Gordon or exit-multiple terminal value discounted at the end of year n, and bridges EV → equity → per share. The result keeps the yearly table, PV split and terminal share of EV.
- `dcf_with_sensitivity()` attaches a per-share grid over discount rate × terminal growth (or exit multiple); economically undefined cells are `None`.
- `DcfResult.to_valuation_run()` stores inputs with sources, the full assumption set plus its SHA-256 `assumptions_hash`, the forecast table, sensitivity grid and outputs (`method=dcf`, version `1.0.0/dcf-1.0.0`). A DCF value is a scenario output, not a price target.

### Bull / base / bear scenario builder (Task 5.3)

- `domain/valuation.ScenarioCase` = `name` (`bull` / `base` / `bear`), a full `DcfAssumptions` set, a narrative ("what has to be true") and an optional probability.
- `analytics/scenarios.build_scenarios(base, cases, price=None)` runs every case through `run_dcf` on the **same** reported base facts and returns a `ScenarioAnalysis`. Each case exposes `LabeledValue`s tagged `fact` (with its source id), `assumption` (with the assumption-set source) or `calculation` (with its formula): base facts, every DCF input, final-year revenue / EBIT, revenue CAGR, cumulative FCFF, EV, equity, value per share and terminal share of EV. `upside` is computed against a point-in-time price observation when supplied.
- Consistency checks (`ConsistencyIssue`, blocking or warning):

| Code | Severity | Rule |
|---|---|---|
| `scenario_set_incomplete` | blocking | exactly one bull, base and bear case |
| `undocumented_assumption` | blocking | revenue growth, EBIT margin, discount rate and the terminal input need a written rationale (no hidden assumption) |
| `horizon_mismatch` | blocking | all cases share the forecast horizon |
| `partial_probabilities` / `probabilities_do_not_sum_to_one` | blocking | probabilities are all-or-none and sum to 1 |
| `value_ordering` | blocking | per-share value bull ≥ base ≥ bear |
| `undefined_valuation` | blocking | the DCF is not computable for a case |
| `driver_ordering` | warning | final-year revenue and EBIT ordered bull ≥ base ≥ bear |
| `terminal_method_mismatch` | warning | cases use different terminal methods |
| `aggressive_terminal_growth` | warning | terminal growth > 4% |
| `terminal_value_dominates` | warning | PV of terminal value > 85% of EV |

- A probability-weighted value is computed only when every case has a probability and the probabilities are valid. `to_valuation_run()` persists facts, price, every case's assumptions (with hash, probability, narrative), labeled outputs and issues (`method=scenario`).

### Investment thesis and cited report composer (Task 5.4)

- Report schema `report.v1` (`domain/report.py`): `ResearchReport` (run, company, `as_of`, snapshot, composer version, sections, citations) → `ReportSection` in a fixed order (`company_overview`, `financial_analysis`, `guidance_and_strategy`, `valuation`, `investment_thesis`, `risks`, `catalysts`, `scenarios`, `open_questions`) → `ReportClaim` (`claim_type` fact / inference / assumption / unknown, `support_status`, `citation_ids`, `metric_ids`, `valuation_ids`, `finding_ids`, `assumption_source`, and the `values` quoted in the text). Schema invariants: a supported fact/inference must have a citation, metric id or valuation id; an unknown claim can never be supported; an assumption names its source; cited citations must exist; no citation may be published after `as_of`.
- `Citation` carries full provenance: document id, source type, document type, title, URL / official id, published / retrieved timestamps, content hash, chunk locator, section, page and verbatim quote. Citation ids are content-derived.
- `reporting/composer.compose_report(run, company, documents, findings, metrics, valuations, thesis)` only assembles validated objects (it computes no financial numbers):
  - quality-gated findings → overview / guidance & strategy / risks / catalysts / open questions, evidence spans → citations (a document published after `as_of` raises `FutureLeakageError`);
  - latest-period `MetricResult`s → fact claims linked by `metric_id` (unavailable metrics become explicit unknown claims naming the missing inputs);
  - persisted `ValuationRun`s → multiples ranges, DCF summary (always stated as an assumption-driven estimate, not a price target) and per-scenario assumption + calculation claims, probability-weighted value and scenario-check notes, all linked by `valuation_id`;
  - `ThesisPoint`s (statement, stance for/against, referenced finding / metric / valuation ids) become inference claims only when at least one reference resolves; points without support move to open questions as unknown claims. References to rejected or unknown objects are noted.
  - Empty sections carry an explicit note. The report id is content-derived (deterministic).
- `reporting/render.render_markdown(report)` renders labeled claims with numbered citations, metric ids and valuation ids, plus a source list with locators and quotes.

### Independent reviewer / contradiction & completeness gate (Task 5.5)

`reporting/reviewer.review_report(report, documents, metrics, valuations, snapshot_document_ids, llm_reviewer)` re-checks a composed report against the objects it claims to rest on and returns a `domain/review.ReviewResult` (deterministic `review_id`, reviewer version, `ReviewFinding`s with code, severity, message, claim / citation ids, origin). The reviewer never rewrites a claim.

| Code | Severity | Check |
|---|---|---|
| `missing_critical_section` | blocking | overview, financial analysis, valuation, thesis and risks need non-unknown content |
| `empty_section` | warning | other sections empty |
| `unsupported_claim` | blocking | fact/inference (outside open questions) without citation, metric or valuation link |
| `uncertain_claim` | warning | linked but uncertain / not supported |
| `unsupported_consensus` | blocking | consensus / street-estimate wording without a cited source |
| `unknown_source`, `future_source`, `citation_outside_snapshot`, `citation_provenance_mismatch` | blocking | citation document unknown, published after `as_of`, not a snapshot member, or hash / timestamp / official id differ from the stored document |
| `superseded_source` | warning | cited document amended or superseded by a document visible at `as_of` |
| `stale_sources` | warning | newest primary citation older than 400 days at `as_of` |
| `untraceable_metric`, `untraceable_valuation` | blocking | linked id not among the run's metric results / valuation records |
| `calculation_mismatch` | blocking | a value quoted in a claim differs from the linked metric value or is not an output of the linked valuation |
| `text_calculation_mismatch` | blocking | the claim text does not state the computed value |
| `number_not_in_evidence` | blocking | a number in prose that is neither in a cited quote nor linked to a calculation |
| `scenario_inconsistent` | blocking | a scenario valuation record carries blocking consistency issues |
| `contradictory_values` | blocking | the same metric is stated with different values |
| `contradictory_thesis_evidence` | warning | one finding is evidence both for and against the thesis |
| `llm_contradiction` | warning | optional LLM pass (`report_contradiction_review@1.0.0`, schema `contradiction_review.v1`) flags a claim pair; only existing claim ids with confidence ≥ 0.5 are kept; LLM output can never block |
| `llm_invalid_reference`, `llm_review_unavailable` | info | LLM referenced unknown claims / the LLM call failed |

- `apply_review(run, review)` moves a `Reviewing` run to `Completed` when there are no blocking findings, otherwise to `NeedsInput` with the blocking codes as the reason.
- Persistence (SQLite schema v5, insert-only): `research_reports`, `reviews` and `review_findings` (`SqliteReportRepository`). `SqliteResearchRunRepository.update_run` independently refuses to store a `Completed` run unless the run's latest review passed (`ReviewGateError`), so blocking findings cannot be bypassed.

### Core FastAPI research API (Task 6.1)

- `application/research_service.ResearchService` is the only thing the API calls. It resolves companies (`SecurityResolver`), creates runs (status `Created`, `as_of` must be timezone-aware and not in the future, company/security persisted from the security master), and returns point-in-time results: snapshot documents (or documents published at or before `as_of` when no snapshot exists yet), financial facts available at `as_of` plus KPIs computed by the deterministic engine, persisted valuation runs, and the latest report with its review and Markdown rendering. Failures raise `ServiceError` subclasses (`InvalidRequest`, `NotFound`, `Ambiguous`) carrying structured details (e.g. resolution candidates).
- `bootstrap.build_container(settings, db_path)` is the composition root (SQLite repositories, JSON security master, service).
- `api/app.create_app(service)` (FastAPI, optional extra `.[api]`; `default_app()` for `uvicorn --factory`):

| Method | Path | Result |
|---|---|---|
| GET | `/healthz` | liveness + version |
| GET | `/companies/resolve?q=&exchange=` | resolution status, company, candidates, suggestions |
| GET | `/companies/{company_id}` | company |
| GET | `/companies/{company_id}/runs` | runs of the company |
| POST | `/research-runs` | create run `{query, exchange?, as_of?}` → 201 |
| GET | `/research-runs/{id}` | run with status history |
| GET | `/research-runs/{id}/documents` | point-in-time documents |
| GET | `/research-runs/{id}/financials` | facts + KPI metric results at `as_of` |
| GET | `/research-runs/{id}/valuations` | valuation records |
| GET | `/research-runs/{id}/report?format=json\|markdown` | report + review (or Markdown) |

- Every error has the shape `{"error": {"code", "message", "details"}}`: `validation_error` (422, with `loc`/`msg`/`type` per field), `invalid_request` (422), `domain_rule_violation` (422), `not_found` (404), `ambiguous` (409, with candidates). Request bodies reject unknown fields. The API contains no research, valuation or persistence logic (enforced by the layer guardrail test).

### Streamlit research dashboard (Task 6.2)

- The dashboard is a thin client (optional extra `.[dashboard]`): it reaches EquityDesk only through `dashboard/client.ApiClient` (httpx over the research API; API errors become `ApiError` with the structured code, message and details). The layer guardrail forbids it from importing application, analytics, reporting, infrastructure or storage code.
- `dashboard/views.py` only reshapes API JSON for display (run progress over the lifecycle, document table, KPI series/table, valuation and scenario tables, labeled scenario assumptions, review findings ordered by severity); it derives no numbers.
- `dashboard/app.py` (run with `EQUITYDESK_API_URL=… streamlit run src/equitydesk/dashboard/app.py`): sidebar company search (resolve, ambiguity candidates), run launcher (`as_of` = end of the chosen UTC day, capped at now), open-run-by-id; main page with progress bar and tabs Sources, Financials (KPI line chart + latest-period table with formulas and missing inputs), Valuation, Scenarios (per-scenario fact / assumption / calculation table) and Report (review verdict, findings, Markdown report).
- The primary workflow (search → create run → view results/report) and error display are tested headless with Streamlit `AppTest` against the real API and SQLite.
