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


class DiagnosticResult(DomainModel):
    """An explainable quality-of-earnings / trend check: formula, components and threshold."""

    diagnostic_id: str = Field(min_length=1)
    company_id: str
    name: str
    period_end: date
    value: float | None
    formula: str
    components: dict[str, float | None]
    threshold: float | None = None
    comparison: str | None = Field(default=None, description="'>' or '<' against threshold")
    triggered: bool | None = Field(default=None, description="None when not computable")
    status: MetricStatus
    explanation: str
    calc_version: str


class TrendSummary(DomainModel):
    metric: str
    unit: str
    points: tuple[tuple[date, float], ...]
    first: float | None
    last: float | None
    change: float | None = Field(default=None, description="last - first (bps for ratios)")
    change_unit: str | None = None
    consecutive_declines: int = 0
    consecutive_increases: int = 0
    direction: str = Field(description="up | down | flat | insufficient_data")
