"""Persisted valuation runs: method, formula, inputs, assumptions and outputs together."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from equitydesk.domain.common import DomainModel, UtcDateTime


class ValuationMethod(StrEnum):
    MULTIPLES = "multiples"
    DCF = "dcf"
    SCENARIO = "scenario"


class ValuationRun(DomainModel):
    valuation_id: str = Field(min_length=1)
    research_run_id: str = Field(min_length=1)
    method: ValuationMethod
    valuation_date: UtcDateTime
    formula: str = Field(min_length=1)
    inputs: dict[str, Any]
    assumptions: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any]
    version: str = Field(min_length=1)
    created_at: UtcDateTime

    @model_validator(mode="after")
    def _traceable(self) -> ValuationRun:
        if self.outputs and not self.inputs:
            raise ValueError("valuation outputs require traceable inputs")
        return self


class Assumption(DomainModel):
    """An explicit valuation/scenario input chosen by a person or a documented rule."""

    name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1, description="ratio | USD | x | years ...")
    rationale: str = Field(min_length=1)
    source: str = Field(min_length=1, description="user:<id> | rule:<name> | scenario:<name>")

    @model_validator(mode="after")
    def _not_model_generated(self) -> Assumption:
        if not self.source.startswith(("user:", "rule:", "scenario:")):
            raise ValueError("assumption source must be user:, rule: or scenario: (never a model)")
        return self
