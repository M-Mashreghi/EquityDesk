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
        if not self.source.startswith(ASSUMPTION_SOURCES):
            raise ValueError("assumption source must be user:, rule: or scenario: (never a model)")
        return self


ASSUMPTION_SOURCES = ("user:", "rule:", "scenario:")


class TerminalMethod(StrEnum):
    GORDON = "gordon"
    EXIT_MULTIPLE = "exit_multiple"


class DcfAssumptions(DomainModel):
    """Versioned, serializable DCF assumption set (all ratios as decimals, per forecast year).

    Every value is an explicit analyst/rule/scenario input; `rationale` documents each one.
    """

    assumption_set_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    source: str = Field(min_length=1, description="user:<id> | rule:<name> | scenario:<name>")
    revenue_growth: tuple[float, ...] = Field(min_length=1)
    ebit_margin: tuple[float, ...] = Field(min_length=1)
    tax_rate: float = Field(ge=0.0, lt=1.0)
    da_pct_revenue: float = Field(ge=0.0)
    capex_pct_revenue: float = Field(ge=0.0)
    nwc_pct_incremental_revenue: float
    discount_rate: float = Field(gt=0.0, lt=1.0)
    terminal_method: TerminalMethod = TerminalMethod.GORDON
    terminal_growth: float | None = None
    exit_ev_ebitda: float | None = Field(default=None, gt=0.0)
    mid_year: bool = False
    rationale: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> DcfAssumptions:
        if not self.source.startswith(ASSUMPTION_SOURCES):
            raise ValueError("assumption source must be user:, rule: or scenario: (never a model)")
        if len(self.revenue_growth) != len(self.ebit_margin):
            raise ValueError("revenue_growth and ebit_margin must cover the same forecast years")
        if any(g <= -1.0 for g in self.revenue_growth):
            raise ValueError("revenue growth must exceed -100%")
        if self.terminal_method is TerminalMethod.GORDON:
            if self.terminal_growth is None:
                raise ValueError("gordon terminal value requires terminal_growth")
            if self.terminal_growth >= self.discount_rate:
                raise ValueError("terminal_growth must be below the discount rate")
        elif self.exit_ev_ebitda is None:
            raise ValueError("exit-multiple terminal value requires exit_ev_ebitda")
        return self

    @property
    def horizon_years(self) -> int:
        return len(self.revenue_growth)
