"""Deterministic metric results with formula and input provenance."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from equitydesk.domain.common import DomainModel


class MetricStatus(StrEnum):
    OK = "ok"
    MISSING_INPUT = "missing_input"  # a required input fact is not available
    UNDEFINED = "undefined"  # inputs exist but the formula is not meaningful (e.g. x / 0)


class MetricResult(DomainModel):
    metric_id: str = Field(min_length=1)
    company_id: str
    name: str
    period_end: date
    value: float | None
    unit: str = Field(description="ratio | USD | days | x | shares ...")
    formula: str
    inputs: dict[str, float | None] = Field(default_factory=dict)
    input_fact_ids: tuple[str, ...] = ()
    status: MetricStatus
    missing: tuple[str, ...] = ()
    note: str | None = None
    calc_version: str

    @model_validator(mode="after")
    def _value_matches_status(self) -> MetricResult:
        if (self.status is MetricStatus.OK) != (self.value is not None):
            raise ValueError("value must be present iff status is ok")
        if self.status is MetricStatus.MISSING_INPUT and not self.missing:
            raise ValueError("missing_input results must name the missing inputs")
        return self
