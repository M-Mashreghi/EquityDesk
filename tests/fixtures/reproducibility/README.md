# Frozen-input reproducibility fixture

`frozen_run.json` records the golden values of one fully offline research run:

- company: Apple (`AAPL`, `co_apple`), `as_of` = 2024-01-15T00:00:00Z, run clock 2024-01-16;
- filings: `tests/fixtures/sec/` served through `httpx.MockTransport` (`tests/sec_mocks.py`);
- reported financial data: `tests/fixtures/sec/companyfacts_CIK0000320193.json`;
- LLM: `ScriptedLLMProvider` routed by prompt (`tests/e2e_support.py::llm_router`);
- prices: `tests/e2e_support.py::PRICES` (one observation before `as_of`, one after it that must be ignored);
- analyst inputs: `tests/e2e_support.py::INPUTS` (bull / base / bear scenarios).

Compared values: final status, `snapshot_id`, snapshot document ids, a SHA-256 over the stored fact ids,
DCF value per share, probability-weighted scenario value, a SHA-256 of the report content (run-specific
ids and timestamps removed) and the review verdict. Any change to parsing, mapping, KPI formulas,
valuation math, composition or review logic that alters a research output makes
`tests/e2e/test_point_in_time_audit.py::test_frozen_inputs_reproduce_golden_values` fail.

If the change is intended, regenerate and review the diff of `frozen_run.json` in the same commit:

```bash
EQUITYDESK_REGENERATE_GOLDEN=1 pytest tests/e2e/test_point_in_time_audit.py -k golden
```
