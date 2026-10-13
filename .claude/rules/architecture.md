# Architecture Rules

## Canonical Research Pipeline

`Company → Source Collection → Snapshot/Provenance → Parse/Normalize → Financial Engine → LLM Research Extraction → Valuation/Peers → Scenario/Thesis → Reviewer → Cited Report`

## Required Boundaries

Keep these independently testable:

1. company/security identity
2. external source adapters
3. raw source/provenance store
4. parsers/normalizers
5. financial statement normalization
6. deterministic analytics
7. LLM extraction
8. valuation / peers
9. research thesis/scenarios
10. report/reviewer
11. persistence/API/UI

## Dependency Direction

- Domain must not import FastAPI, Streamlit, HTTP clients, or LLM SDKs.
- Application coordinates domain interfaces.
- Infrastructure implements external adapters.
- Dashboard/API consume application services; they do not own research logic.
- Analytics functions should be deterministic where practical.

## External Providers

Wrap regulator, issuer, LLM, estimates, market-data, and export services behind adapters.

Tests must be able to replace them with fixtures/fakes.

## MVP Discipline

Use a modular monolith. Do not introduce queues, distributed services, vector databases, or agent frameworks unless a concrete task demonstrates the need.
