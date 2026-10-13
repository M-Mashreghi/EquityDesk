# EquityDesk Diagrams

Mermaid diagrams for architecture, research lifecycle, point-in-time sourcing, and report review.

Related: [ARCHITECTURE](./ARCHITECTURE.md) · [PHASES](./PHASES.md) · [ROADMAP](./ROADMAP.md)

## 1. System Architecture

```mermaid
flowchart TB
    USER[Research User]
    UI[Streamlit Dashboard]
    API[FastAPI]
    WF[Research Workflow]
    DB[(SQLite)]
    SRC[Primary Source Adapters]
    PARSE[Document Normalizer]
    FIN[Financial / KPI Engine]
    LLM[LLM Evidence Extractor]
    VAL[Peer + Valuation Engine]
    REV[Reviewer / Citation Gate]
    REPORT[Research Report]

    USER --> UI --> API --> WF
    WF --> SRC --> PARSE
    PARSE --> DB
    WF --> FIN
    WF --> LLM
    FIN --> VAL
    LLM --> VAL
    VAL --> REV
    REV --> REPORT
    WF <--> DB
```

## 2. End-to-End Research Run

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant A as API / Workflow
    participant S as Source Adapters
    participant D as Document Store
    participant F as Financial Engine
    participant L as LLM Research
    participant V as Valuation Engine
    participant R as Reviewer

    U->>A: Research(company, as_of)
    A->>S: Collect sources published <= as_of
    S-->>A: Filings / IR documents + provenance
    A->>D: Normalize + snapshot + hash
    A->>F: Build statements / KPIs
    F-->>A: Deterministic metrics
    A->>L: Analyze bounded cited source text
    L-->>A: Structured findings + evidence
    A->>V: Peers / DCF / scenarios
    V-->>A: Traceable valuation outputs
    A->>R: Validate report objects
    alt Pass
        R-->>A: Approved
        A-->>U: Cited report
    else Blocking issue
        R-->>A: Findings / NeedsInput
        A-->>U: Incomplete report + explicit issues
    end
```

## 3. Research Run State Machine

```mermaid
stateDiagram-v2
    [*] --> Created
    Created --> Collecting
    Collecting --> Parsing
    Collecting --> BlockedBySource
    Parsing --> Analyzing
    Parsing --> Failed
    Analyzing --> Valuing
    Analyzing --> NeedsInput
    Valuing --> Reviewing
    Valuing --> NeedsInput
    Reviewing --> Completed
    Reviewing --> NeedsInput
    Reviewing --> Failed
    NeedsInput --> Analyzing
    NeedsInput --> Valuing
    BlockedBySource --> Collecting
    Completed --> [*]
    Failed --> [*]
```

## 4. Point-in-Time Gate

```mermaid
flowchart LR
    ALL[All known documents] --> FILTER{published_at <= as_of?}
    FILTER -->|Yes| SNAP[Research Snapshot]
    FILTER -->|No| FUTURE[Excluded as future information]
    SNAP --> PARSE[Parse / Analyze]
    PARSE --> REPORT[Cited Report]
```

## 5. Fact → Research Claim Traceability

```mermaid
flowchart LR
    SOURCE[Primary Source] --> SPAN[Evidence Span / Table Fact]
    SPAN --> FACT[Normalized Fact / Finding]
    FACT --> CALC[Deterministic Calculation]
    FACT --> INF[Analyst/LLM Inference]
    CALC --> CLAIM[Report Claim]
    INF --> CLAIM
    CLAIM --> CITE[Citation / Provenance]
```

## 6. Valuation Flow

```mermaid
flowchart TB
    HIST[Historical Financials]
    ASSUMP[Explicit Scenario Assumptions]
    PEERS[Versioned Peer Set]
    MARKET[Market Snapshot]
    DCF[DCF Engine]
    MULT[Multiples Engine]
    SENS[Sensitivity]
    SYNTH[Valuation Synthesis]

    HIST --> DCF
    ASSUMP --> DCF
    PEERS --> MULT
    MARKET --> MULT
    DCF --> SENS
    MULT --> SYNTH
    SENS --> SYNTH
```
