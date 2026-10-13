"""Financial facts with accounting provenance."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from equitydesk.domain.common import DomainModel, UtcDateTime


class PeriodType(StrEnum):
    DURATION = "duration"  # flows: revenue, cash flow
    INSTANT = "instant"  # stocks: balance-sheet items, shares outstanding


class FactOrigin(StrEnum):
    REPORTED = "reported"
    DERIVED = "derived"
    NORMALIZED = "normalized"
    ADJUSTED = "adjusted"


class FinancialFact(DomainModel):
    fact_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    concept: str = Field(min_length=1, description="standardized concept, e.g. Revenue")
    value: float
    unit: str = Field(min_length=1, description="e.g. USD, shares, USD/shares")
    period_type: PeriodType
    period_start: date | None = None
    period_end: date
    fiscal_year: int | None = None
    fiscal_period: str | None = Field(default=None, pattern=r"^(FY|Q[1-4]|H[12])$")
    source_document_id: str | None = None
    source_accession: str | None = None
    source_locator: str | None = Field(default=None, description="raw concept / table locator")
    origin: FactOrigin = FactOrigin.REPORTED
    # When the fact became public: point-in-time gate for historical research.
    available_at: UtcDateTime
    derived_from: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _period_and_provenance(self) -> FinancialFact:
        if self.period_type is PeriodType.DURATION:
            if self.period_start is None or self.period_start > self.period_end:
                raise ValueError("duration facts need period_start <= period_end")
        elif self.period_start is not None:
            raise ValueError("instant facts must not have period_start")
        if self.origin is FactOrigin.REPORTED and not (
            self.source_document_id or self.source_accession
        ):
            raise ValueError("reported facts need a source document or accession")
        if self.origin is not FactOrigin.REPORTED and not self.derived_from:
            raise ValueError("derived/normalized/adjusted facts must list derived_from")
        return self


class RawReportedFact(DomainModel):
    """A fact exactly as reported in a filing's structured data (before standardization)."""

    company_id: str
    taxonomy: str = Field(description="e.g. us-gaap, dei")
    tag: str
    unit: str
    value: float
    period_start: date | None = None
    period_end: date
    accession: str
    form: str
    filed: date
    available_at: UtcDateTime

    @property
    def locator(self) -> str:
        return f"{self.taxonomy}:{self.tag}"
