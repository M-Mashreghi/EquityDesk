# EquityDesk — Recommended Repository Structure

```text
EquityDesk/
├── CLAUDE.md
├── README.md
├── PROJECT_STRUCTURE.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
│
├── .claude/
│   └── rules/
│       ├── architecture.md
│       ├── finance-integrity.md
│       ├── llm-analysis.md
│       ├── testing.md
│       └── documentation.md
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ROADMAP.md
│   ├── PHASES.md
│   ├── PROJECT_PHASES_AND_TASKS.md
│   ├── TASKS.md
│   ├── DIAGRAMS.md
│   ├── DEPLOYMENT.md
│   └── RUNBOOK.md
│
├── src/equitydesk/
│   ├── domain/
│   │   ├── companies.py
│   │   ├── documents.py
│   │   ├── financials.py
│   │   ├── research.py
│   │   └── valuation.py
│   ├── application/
│   │   ├── ports.py
│   │   ├── research_service.py
│   │   └── workflows.py
│   ├── infrastructure/
│   │   ├── db/
│   │   ├── sources/
│   │   ├── parsers/
│   │   ├── llm/
│   │   ├── market_data/
│   │   └── jobs/
│   ├── analytics/
│   │   ├── accounting.py
│   │   ├── kpis.py
│   │   ├── peers.py
│   │   ├── dcf.py
│   │   └── scenarios.py
│   ├── reporting/
│   │   ├── citations.py
│   │   ├── reviewer.py
│   │   └── composer.py
│   ├── api/
│   └── dashboard/
│
├── data/
│   ├── raw/          # local development only; production policy documented
│   ├── fixtures/
│   └── cache/
│
└── tests/
    ├── unit/
    ├── integration/
    ├── fixtures/
    └── e2e/
```

The exact folders may evolve task-by-task, but module boundaries from `docs/ARCHITECTURE.md` should remain intact.
