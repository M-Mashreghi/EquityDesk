"""Market snapshot contract: timestamped price / share-count observations with provenance."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from pydantic import Field, model_validator

from equitydesk.domain.common import DomainModel, UtcDateTime
from equitydesk.domain.provenance import FutureLeakageError


class MarketObservation(DomainModel):
    security_id: str = Field(min_length=1)
    price: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    observed_at: UtcDateTime = Field(description="time the price was observed (e.g. close)")
    price_source: str = Field(min_length=1, description="provider / exchange / manual:<user>")
    shares_outstanding: float | None = Field(default=None, gt=0)
    shares_source: str | None = Field(default=None, description="e.g. 10-Q cover page accession")
    shares_as_of: date | None = None

    @model_validator(mode="after")
    def _shares_need_source(self) -> MarketObservation:
        if self.shares_outstanding is not None and not (self.shares_source and self.shares_as_of):
            raise ValueError("shares_outstanding requires shares_source and shares_as_of")
        if self.shares_as_of is not None and self.shares_as_of > self.observed_at.date():
            raise ValueError("share count cannot be dated after the price observation")
        return self


def observation_as_of(
    observations: Iterable[MarketObservation], security_id: str, as_of: datetime
) -> MarketObservation:
    """Latest observation for the security at or before `as_of`; never a later price."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    candidates = [o for o in observations if o.security_id == security_id]
    visible = [o for o in candidates if o.observed_at <= as_of]
    if not visible:
        future = [o.security_id + "@" + o.observed_at.isoformat() for o in candidates]
        if future:
            raise FutureLeakageError(as_of, future)
        raise LookupError(f"no market observation for {security_id}")
    return max(visible, key=lambda o: o.observed_at)
