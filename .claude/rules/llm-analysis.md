# LLM Research Rules

## Structured Output Only

Application-facing model responses must validate against explicit schemas.

## Evidence-Backed Extraction

Material outputs should include evidence locators or source-span references.

Typical objects:

- BusinessModelFinding
- SegmentFinding
- StrategyChange
- GuidanceFinding
- RiskFinding
- CatalystFinding
- ManagementToneFinding
- OpenQuestion

## Model Responsibilities

The model may:

- classify and summarize sourced text
- compare management language
- connect explicitly cited facts into an inference
- identify contradictions or unresolved questions
- draft prose from validated research objects

The model may not:

- invent financial facts
- invent management guidance
- invent peer multiples
- invent consensus
- perform hidden valuation math
- cite a source it did not receive

## Failure Mode

When evidence is incomplete or conflicting, emit uncertainty and the competing evidence.

Never repair missing facts by relying on model memory.

## Prompt Versioning

Persist prompt/schema/model version for material research outputs so reports are reproducible.
