"""Watchlist entries and the alerts they produce."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import Field

from equitydesk.domain.common import DomainModel, UtcDateTime


class WatchlistEntry(DomainModel):
    company_id: str = Field(min_length=1)
    refresh_interval_hours: int = Field(default=24, ge=1, le=24 * 90)
    alert_on_new_filing: bool = True
    alert_on_run_finished: bool = True
    active: bool = True
    created_at: UtcDateTime
    last_refresh_at: UtcDateTime | None = None
    last_refresh_run_id: str | None = None
    filings_seen_until: UtcDateTime | None = Field(
        default=None, description="newest document publication already alerted on"
    )

    def is_due(self, now: datetime) -> bool:
        if not self.active:
            return False
        if self.last_refresh_at is None:
            return True
        return now - self.last_refresh_at >= timedelta(hours=self.refresh_interval_hours)

    def slot(self, now: datetime) -> str:
        """Refresh slot id: one scheduled refresh per company per interval window."""
        window = int(now.timestamp() // 3600) // self.refresh_interval_hours
        return f"{self.company_id}:{self.refresh_interval_hours}h:{window}"


class AlertKind(StrEnum):
    NEW_FILING = "new_filing"
    RUN_COMPLETED = "run_completed"
    RUN_NEEDS_INPUT = "run_needs_input"
    REFRESH_STARTED = "refresh_started"


class AlertSource(DomainModel):
    document_id: str
    document_type: str
    official_id: str | None = None
    source_url: str | None = None
    published_at: UtcDateTime


class Alert(DomainModel):
    alert_id: str = Field(min_length=1)
    kind: AlertKind
    company_id: str = Field(min_length=1)
    research_run_id: str | None = None
    report_id: str | None = None
    sources: tuple[AlertSource, ...] = ()
    message: str = Field(min_length=1)
    created_at: UtcDateTime
    delivered: bool = False
    delivery_error: str | None = None
