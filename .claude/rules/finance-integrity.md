# Finance & Research Integrity Rules

## Point-in-Time Is Mandatory

Every research run has `as_of`.

The run may only consume documents and data available by `as_of`.

Test this explicitly.

## Deterministic Finance

Code, not the LLM, computes accounting ratios, valuation metrics, scenarios, sensitivity tables, and returns.

LLM output may explain validated numbers but must not become the source of numerical truth.

## Accounting Provenance

Every standardized financial fact should retain enough metadata to identify:

- issuer
- concept / metric
- period
- unit / currency
- source document
- source locator when available
- whether the value was reported, derived, normalized, or adjusted

Do not silently merge incompatible periods/units.

## Valuation Integrity

- Show formulas and assumptions.
- Timestamp market observations.
- Keep peer sets explicit.
- Do not mix future peer values into historical snapshots.
- DCF assumptions must be visible and versioned.
- A scenario valuation is not a guaranteed target price.

## Estimates / Guidance

Do not invent consensus estimates.

If estimates are unavailable, represent them as missing or accept explicit user-provided inputs.

Management guidance must come from a cited source.

## Facts vs Interpretation

Label facts, inferences, assumptions, and unknowns separately.

A confident narrative cannot override missing evidence.
