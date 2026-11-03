# EquityDesk

**Autonomous Equity Research Analyst**

EquityDesk is an LLM-assisted equity-research platform that turns a ticker or company into an evidence-backed, reproducible research package built from primary sources and deterministic financial analysis.

Instead of producing a generic chatbot answer, EquityDesk follows a research workflow similar to an equity analyst:

```text
Ticker / Company
      ↓
Company & Security Resolution
      ↓
Primary-Source Collection
(10-K / 10-Q / 8-K / earnings / IR material)
      ↓
Document Normalization + Point-in-Time Snapshot
      ↓
Financial Statement & KPI Engine
      ↓
LLM Evidence Extraction
      ↓
Business / Segment / Management Analysis
      ↓
Peer Comparables + Valuation
      ↓
Catalysts / Risks / Bull-Base-Bear Scenarios
      ↓
Reviewer / Contradiction Check
      ↓
Cited Equity Research Report
```

## Core Question

> **Given the information that was available as of a chosen date, what does the evidence say about this company, its financial trajectory, valuation, major risks, catalysts, and scenario range?**

## Example Output

For a company such as ASML, the finished research package may contain:

```text
Company: ASML Holding N.V.
Research date: 2026-10-13

Business quality
- Revenue growth: calculated from sourced statements
- Gross / operating margin trends: deterministic
- Segment concentration: evidence-backed

Research view
- Thesis: structured and cited
- Catalysts: 4
- Risks: 5
- Open questions: 3

Valuation
- Trading multiples: calculated in Python
- Peer comparison: calculated in Python
- Base-case implied value: deterministic scenario model

Scenarios
- Bull / Base / Bear
- Explicit assumptions and sensitivity

Evidence
- Every material factual claim links to a filing, earnings release,
  investor presentation, or other approved source.
```

## v1 Scope

- Accept a ticker/company and an explicit `as_of` research date.
- Resolve the issuer and security identity.
- Collect primary public documents from official sources.
- Preserve immutable source snapshots, publication timestamps, hashes, and provenance.
- Parse filings, earnings releases, and investor materials into normalized documents.
- Build standardized historical financial statements and KPI time series.
- Calculate growth, margins, capital efficiency, cash conversion, leverage, and other ratios in Python.
- Extract business-model, segment, guidance, catalyst, risk, and management-language evidence using structured LLM outputs.
- Build peer-comparable tables and transparent valuation models.
- Generate explicit bull/base/bear scenarios from user- or rule-defined assumptions.
- Produce a cited research memo with an independent reviewer/checker pass.
- Expose results through FastAPI and a Streamlit dashboard.
- Support refresh/watchlist workflows, tests, Docker, CI/CD, and operational docs.

## Non-Goals for v1

- Autonomous order execution or brokerage integration.
- Personalized investment advice.
- LLM-generated financial statements, returns, or valuation math.
- Invented consensus estimates or management guidance.
- Using future filings, future prices, or later revisions in historical research.
- Scraping every financial website on the internet.
- Full institutional-grade accounting normalization across every country and industry in the first release.

## Research Integrity Principles

1. **Primary sources first.** Prefer regulator filings and issuer investor-relations material.
2. **Every material claim needs evidence.** Unsupported claims must be marked uncertain or omitted.
3. **LLM ≠ calculator.** Numerical finance is deterministic Python.
4. **Point-in-time by design.** A historical report may use only information available by its `as_of` timestamp.
5. **Assumptions are visible.** Valuation and scenarios must expose formulas and inputs.
6. **Facts, inference, and opinion are separate.** The report must label them clearly.
7. **Reproducibility matters.** Source, model, prompt, configuration, and calculation versions are recorded.

## Technology Baseline

| Area | Baseline |
|---|---|
| Language | Python 3.12+ |
| API | FastAPI |
| Dashboard | Streamlit |
| Persistence | SQLite |
| Models / validation | Pydantic |
| Data / analytics | pandas, NumPy |
| HTTP | httpx |
| LLM | Provider adapter with structured outputs |
| Document parsing | Adapter-based; format-specific libraries introduced only when needed |
| Tests | pytest |
| Deployment | Docker / Docker Compose |

## Local Development

```bash
python -m venv .venv                 # Python 3.12+
.venv/Scripts/activate               # Windows (or: source .venv/bin/activate)
pip install -e ".[dev]"
cp .env.example .env                 # fill in local values; never commit .env

pytest                               # full test suite
ruff check src tests                 # lint
ruff format --check src tests        # formatting
mypy                                 # strict type checks
```

Run the API locally (`pip install -e ".[api]"`):

```bash
uvicorn equitydesk.api.app:default_app --factory --port 8000
curl -fsS http://localhost:8000/healthz
```

Run the dashboard against the API (`pip install -e ".[dashboard]"`):

```bash
EQUITYDESK_API_URL=http://localhost:8000 streamlit run src/equitydesk/dashboard/app.py
```

Layer import rules (`domain` cannot import UI/HTTP/persistence/LLM code, the dashboard cannot import analytics or storage, etc.) are enforced by `tests/unit/test_architecture.py`.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Implementation Phases](docs/PHASES.md)
- [Atomic Tasks / SSOT](docs/PROJECT_PHASES_AND_TASKS.md)
- [Task Ledger](docs/TASKS.md)
- [Diagrams](docs/DIAGRAMS.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Runbook](docs/RUNBOOK.md)

## Development Model

`docs/PROJECT_PHASES_AND_TASKS.md` is the implementation source of truth. Implement exactly one atomic task at a time and do not mark it complete until its Definition of Done is satisfied.

Recommended first Claude Code prompt:

```text
Read CLAUDE.md and all project governance documents.
Inspect Task 1.1 in docs/PROJECT_PHASES_AND_TASKS.md.
Implement only Task 1.1.
Follow its Definition of Done, run the required tests,
and update the task ledgers only after verification succeeds.
Do not start Task 1.2.
```

## Disclaimer

EquityDesk is a research and decision-support project. Its outputs are informational, may contain errors, and are not personalized investment advice or a recommendation to trade securities.
