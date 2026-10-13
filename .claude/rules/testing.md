# Testing Rules

Every task requires tests proportional to its risk.

## Required Test Families

- domain validation
- company/security resolution
- source adapter fixtures
- publication/retrieval timestamp handling
- document parsing/versioning
- financial statement normalization
- numerical KPI and ratio fixtures
- LLM structured-output validation
- evidence/citation validation
- point-in-time / future-leakage prevention
- peer/valuation numerical fixtures
- scenario sensitivity
- report reviewer/contradiction checks
- API/integration tests
- E2E research-run tests

## Numerical Tests

Use hand-checkable fixtures with expected values and tolerances.

Do not validate finance math by asking an LLM.

## LLM Tests

Mock the provider in CI. Cover malformed JSON, missing evidence, low confidence, contradictory evidence, timeout/retry, and schema version changes.

## Completion Gate

A task cannot be marked complete while mandatory tests fail.
