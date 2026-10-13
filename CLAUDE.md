# EquityDesk — Claude Code Project Instructions

EquityDesk is an autonomous equity-research system that produces evidence-backed company research from primary documents, deterministic financial analysis, transparent valuation models, and structured LLM reasoning.

## Product Objective

The canonical workflow is:

`Company/Ticker → Security Resolution → Source Collection → Point-in-Time Snapshot → Parsing/Normalization → Financial/KPI Engine → LLM Evidence Extraction → Peer/Valuation Engine → Risks/Catalysts → Bull/Base/Bear → Reviewer → Cited Report`

The system must clearly distinguish:

- source facts,
- deterministic calculations,
- model-extracted interpretation,
- analyst assumptions,
- final research conclusions.

## Source of Truth

Before implementing any task, read:

1. `docs/PROJECT_PHASES_AND_TASKS.md`
2. `docs/ARCHITECTURE.md`
3. `docs/TASKS.md`
4. the relevant phase in `docs/PHASES.md`
5. any referenced diagram, schema, or contract

Do not invent requirements that conflict with these documents.

## Task Execution Workflow

For every task:

1. Read the complete task specification and Definition of Done.
2. Inspect the existing repository before editing.
3. Identify the smallest correct implementation for the current task.
4. Implement only the current task scope.
5. Add/update tests for critical paths and failure paths.
6. Run targeted tests and the relevant regression suite.
7. Fix failures before claiming completion.
8. Update `docs/TASKS.md` and `docs/PROJECT_PHASES_AND_TASKS.md` only after verification succeeds.
9. Update architecture/configuration docs when behavior or contracts change.
10. Do not start the next task unless explicitly requested.

## Architecture Boundaries

Keep explicit boundaries between:

- company/security master
- source collection/adapters
- raw document storage/provenance
- parsing and normalization
- financial statement normalization
- deterministic analytics / KPI calculation
- LLM research extraction
- peer-comparable and valuation engines
- thesis/scenario construction
- report composition/review
- persistence
- API
- dashboard / workflow automation

UI code must not contain accounting logic, valuation math, source parsing, or persistence logic.

External providers must be wrapped behind interfaces/adapters and mockable in tests.

## Source Hierarchy

Prefer sources in this order unless a task explicitly requires otherwise:

1. Regulatory filings / official regulator APIs.
2. Company investor-relations documents and earnings releases.
3. Company presentations and official prepared remarks.
4. Approved market-data or estimates providers.
5. Reputable secondary sources for context only.

Do not allow a secondary source to silently override a primary source.

## Point-in-Time Research Integrity

Every report has an explicit `as_of` timestamp.

Historical research must use only information available at or before `as_of`.

Store and respect:

- source `published_at`
- source `retrieved_at`
- filing period/end date
- amendment/revision relationships
- `as_of`
- market timezone/session where relevant
- model version
- prompt/schema version
- calculation/version metadata

Never leak future filings, later guidance, future prices, later peer values, or revised information into an earlier research snapshot.

## Financial Integrity

LLMs must NOT fabricate or perform authoritative financial calculations.

LLMs may:

- extract facts and evidence from source text
- classify business drivers, risks, catalysts, and management language
- identify segment or strategy changes supported by evidence
- compare wording across filings/earnings materials
- summarize deterministic calculations already produced by code
- draft research prose from validated facts and calculations

Deterministic Python code must calculate:

- revenue / EPS / FCF growth
- margins and margin deltas
- ROIC/ROE/ROA where defined
- working-capital metrics
- leverage and liquidity ratios
- share-count changes
- trading multiples
- peer summary statistics
- DCF / scenario valuation outputs
- sensitivity tables
- return calculations if market data is later used

Never accept a financial number from an LLM when the same number should be parsed or computed from structured data.

## Valuation Rules

Valuation must be transparent and reproducible.

Every valuation output must preserve:

- valuation date
- share price / share count source where applicable
- formula / method
- input assumptions
- forecast horizon
- discount rate / terminal assumptions if DCF is used
- peer set and peer observation date for multiples
- output range and sensitivity

LLMs may explain valuation, but valuation math belongs in code.

Do not present an LLM guess as a price target.

## LLM Structured Output

Application-facing LLM output must be schema-validated.

Research extraction should use structured objects such as:

- `business_model`
- `segments`
- `strategy_changes`
- `guidance_changes`
- `risks`
- `catalysts`
- `management_tone`
- `open_questions`
- `evidence`
- `confidence`

Every material extracted claim must link to evidence in a source document.

If evidence is insufficient, return `uncertain` / `not_supported` rather than inventing a conclusion.

## Evidence & Citations

Persist enough provenance to recreate every material report claim:

- source id
- source type
- source URL / official identifier
- publication timestamp
- retrieval timestamp
- document hash
- section/page/paragraph locator where available
- extracted text span
- parser version
- model/prompt version for model-derived claims

A final report must not contain uncited material factual claims.

## Fact / Inference / Assumption Separation

Research objects and reports should distinguish:

- **Fact** — directly supported by a source or deterministic calculation.
- **Inference** — interpretation derived from cited facts.
- **Assumption** — explicit analyst/scenario input.
- **Unknown** — missing or unresolved information.

Do not blur these categories.

## Reviewer / Contradiction Rules

The reviewer stage must check for:

- unsupported claims
- stale source use
- contradictory statements
- mismatch between text and calculations
- inconsistent scenario assumptions
- valuation output without traceable inputs
- future-data leakage

The reviewer may flag or block a report, but must not silently rewrite source facts.

## Data Safety & Secrets

Never hardcode or commit:

- LLM API keys
- market-data credentials
- paid research-provider credentials
- webhook tokens
- database passwords

Use environment variables and `.env.example` placeholders.

Do not log full secrets or authorization headers.

## Testing

Every task requires appropriate tests.

Critical test families include:

- company/security resolution
- source/provenance tests
- filing/IR parser fixtures
- document hash/versioning tests
- financial normalization fixtures
- ratio/KPI numerical fixtures
- structured LLM schema and evidence tests
- point-in-time / no-look-ahead tests
- peer-set and valuation calculation tests
- scenario sensitivity tests
- citation coverage tests
- report contradiction/reviewer tests
- API tests
- E2E research-run tests

Do not weaken tests simply to make failing implementation pass unless the test itself is demonstrably wrong.

## Scope Discipline

- No unrelated refactors.
- No speculative microservices for the MVP.
- No autonomous trade execution.
- No dependency added without a concrete task need.
- Prefer a simple modular monolith.
- Prefer official/free data sources in v1 where they satisfy the requirement.
- Do not expand into full portfolio management unless explicitly approved.

## Documentation

All repository documentation and code comments should be in English.

When behavior changes, update the corresponding docs in the same change set.

Keep Mermaid diagrams aligned with implementation.

## Definition of Complete

Never claim a task is complete unless:

- implementation exists,
- required tests pass,
- task DoD is satisfied,
- documentation is synchronized,
- no secrets were introduced,
- point-in-time and finance-integrity rules remain satisfied.
