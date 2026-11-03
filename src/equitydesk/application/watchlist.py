"""Watchlist, scheduled refresh and alert hooks.

- A scheduled refresh claims a per-company time slot first; a second claim of the same slot (two
  scheduler processes, a retry, a double click) is refused, so refreshes are never duplicated.
- New-filing alerts reference the exact documents (id, type, official id, URL, publication time);
  run alerts reference the run and its report.
- Alerts are persisted before delivery; delivery goes through an `AlertSink` adapter whose
  credentials live in configuration, never in the alert payload.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from equitydesk.application.ports import NotFoundError
from equitydesk.application.research_service import InvalidRequest, NotFound, ResearchService
from equitydesk.domain.common import utc_now
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.research import ResearchStatus
from equitydesk.domain.watchlist import Alert, AlertKind, AlertSource, WatchlistEntry


class WatchlistRepository(Protocol):
    def save_entry(self, entry: WatchlistEntry) -> None: ...
    def get_entry(self, company_id: str) -> WatchlistEntry: ...
    def remove_entry(self, company_id: str) -> bool: ...
    def list_entries(self) -> list[WatchlistEntry]: ...
    def claim_slot(self, slot: str, company_id: str, claimed_at: datetime) -> bool: ...
    def attach_run(self, slot: str, research_run_id: str) -> None: ...
    def add_alert(self, alert: Alert) -> bool: ...
    def mark_delivery(self, alert_id: str, delivered: bool, error: str | None) -> None: ...
    def alerts(self, company_id: str | None = None, limit: int = 100) -> list[Alert]: ...


class AlertSink(Protocol):
    def send(self, alert: Alert) -> None: ...


class AlertDeliveryError(Exception):
    """Raised by sinks; the message must never contain credentials."""


@dataclass
class RefreshOutcome:
    company_id: str
    action: str  # started | skipped_not_due | skipped_duplicate | skipped_in_progress | failed
    research_run_id: str | None = None
    detail: str = ""


@dataclass
class TickResult:
    refreshes: list[RefreshOutcome] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


def _alert_id(*parts: str) -> str:
    return "alr_" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _source(d: SourceDocument) -> AlertSource:
    return AlertSource(document_id=d.document_id, document_type=d.document_type,
                       official_id=d.official_id, source_url=d.source_url, published_at=d.published_at)  # fmt: skip


@dataclass
class WatchlistService:
    repo: WatchlistRepository
    research: ResearchService
    sink: AlertSink | None = None
    clock: Callable[[], datetime] = field(default=utc_now)

    # --- entries ---------------------------------------------------------------------------

    def watch(self, company_id: str, refresh_interval_hours: int = 24,
              alert_on_new_filing: bool = True, alert_on_run_finished: bool = True) -> WatchlistEntry:  # fmt: skip
        self.research.get_company(company_id)
        try:
            current: WatchlistEntry | None = self.repo.get_entry(company_id)
        except NotFoundError:
            current = None
        try:
            entry = WatchlistEntry(
                company_id=company_id, refresh_interval_hours=refresh_interval_hours,
                alert_on_new_filing=alert_on_new_filing, alert_on_run_finished=alert_on_run_finished,
                created_at=current.created_at if current else self.clock(),
                last_refresh_at=current.last_refresh_at if current else None,
                last_refresh_run_id=current.last_refresh_run_id if current else None,
                filings_seen_until=current.filings_seen_until if current else self.clock(),
            )  # fmt: skip
        except ValueError as exc:
            raise InvalidRequest(str(exc)) from exc
        self.repo.save_entry(entry)
        return entry

    def unwatch(self, company_id: str) -> None:
        if not self.repo.remove_entry(company_id):
            raise NotFound(f"{company_id} is not on the watchlist")

    def entries(self) -> list[WatchlistEntry]:
        return self.repo.list_entries()

    def alerts(self, company_id: str | None = None, limit: int = 100) -> list[Alert]:
        return self.repo.alerts(company_id, limit)

    # --- scheduled work --------------------------------------------------------------------

    def tick(self) -> TickResult:
        """One scheduler pass: new-filing alerts, then due refreshes (each slot claimed once)."""
        now = self.clock()
        result = TickResult()
        for entry in self.repo.list_entries():
            if not entry.active:
                continue
            entry, alert = self._check_filings(entry, now)
            if alert is not None:
                result.alerts.append(alert)
            outcome, alert = self._refresh(entry, now)
            result.refreshes.append(outcome)
            if alert is not None:
                result.alerts.append(alert)
        return result

    def _check_filings(
        self, entry: WatchlistEntry, now: datetime
    ) -> tuple[WatchlistEntry, Alert | None]:
        docs = [d for d in self.research.documents.documents_for_company(entry.company_id, now)
                if entry.filings_seen_until is None or d.published_at > entry.filings_seen_until]  # fmt: skip
        if not docs:
            return entry, None
        entry = entry.model_copy(update={"filings_seen_until": max(d.published_at for d in docs)})
        self.repo.save_entry(entry)
        if not entry.alert_on_new_filing:
            return entry, None
        ids = sorted(d.document_id for d in docs)
        kinds = ", ".join(sorted({d.document_type for d in docs}))
        alert = Alert(
            alert_id=_alert_id("new_filing", entry.company_id, *ids), kind=AlertKind.NEW_FILING,
            company_id=entry.company_id, sources=tuple(_source(d) for d in sorted(docs, key=lambda d: d.published_at)),
            message=f"{len(docs)} new document(s) for {entry.company_id}: {kinds}", created_at=now,
        )  # fmt: skip
        return entry, self._emit(alert)

    def _refresh(self, entry: WatchlistEntry, now: datetime) -> tuple[RefreshOutcome, Alert | None]:
        if not entry.is_due(now):
            return RefreshOutcome(entry.company_id, "skipped_not_due"), None
        open_runs = [r for r in self.research.runs.runs_for_company(entry.company_id)
                     if not r.is_terminal and r.status is not ResearchStatus.NEEDS_INPUT
                     and r.status is not ResearchStatus.CREATED]  # fmt: skip
        if open_runs:
            return RefreshOutcome(
                entry.company_id, "skipped_in_progress", open_runs[-1].research_run_id
            ), None
        slot = entry.slot(now)
        if not self.repo.claim_slot(slot, entry.company_id, now):
            return RefreshOutcome(entry.company_id, "skipped_duplicate", detail=slot), None
        try:
            run = self.research.start_run_for_company(entry.company_id, as_of=now)
        except Exception as exc:  # recorded, the scheduler keeps going for other companies
            return RefreshOutcome(
                entry.company_id, "failed", detail=type(exc).__name__ + ": " + str(exc)
            ), None
        self.repo.attach_run(slot, run.research_run_id)
        self.repo.save_entry(
            entry.model_copy(
                update={"last_refresh_at": now, "last_refresh_run_id": run.research_run_id}
            )
        )
        alert = Alert(
            alert_id=_alert_id("refresh_started", run.research_run_id), kind=AlertKind.REFRESH_STARTED,
            company_id=entry.company_id, research_run_id=run.research_run_id,
            message=f"scheduled refresh started for {entry.company_id} as of {run.as_of.isoformat()}"
            + (f" (refreshes {run.refreshed_from})" if run.refreshed_from else ""),
            created_at=now,
        )  # fmt: skip
        return RefreshOutcome(entry.company_id, "started", run.research_run_id, slot), self._emit(
            alert
        )

    # --- run hooks -------------------------------------------------------------------------

    def run_finished(self, research_run_id: str) -> Alert | None:
        """Alert hook for a run that reached Completed or NeedsInput."""
        run = self.research.get_run(research_run_id)
        if run.status not in (ResearchStatus.COMPLETED, ResearchStatus.NEEDS_INPUT):
            return None
        try:
            entry = self.repo.get_entry(run.company_id)
        except NotFoundError:
            return None
        if not entry.alert_on_run_finished:
            return None
        reports = self.research.reports.reports_for_run(run.research_run_id)
        report = reports[-1] if reports else None
        cited = {c.document_id for c in report.citations} if report else set()
        docs = [
            d for d in self.research.run_documents(run.research_run_id) if d.document_id in cited
        ]
        kind = (
            AlertKind.RUN_COMPLETED
            if run.status is ResearchStatus.COMPLETED
            else AlertKind.RUN_NEEDS_INPUT
        )
        reason = run.status_history[-1].reason if run.status_history else None
        alert = Alert(
            alert_id=_alert_id(kind.value, run.research_run_id), kind=kind, company_id=run.company_id,
            research_run_id=run.research_run_id, report_id=report.report_id if report else None,
            sources=tuple(_source(d) for d in docs),
            message=f"research run {run.research_run_id} ({run.company_id}, as of {run.as_of.date().isoformat()}) "
            f"is {run.status.value}" + (f": {reason}" if reason else ""),
            created_at=self.clock(),
        )  # fmt: skip
        return self._emit(alert)

    def _emit(self, alert: Alert) -> Alert | None:
        if not self.repo.add_alert(alert):
            return None  # already raised (idempotent hooks)
        if self.sink is not None:
            try:
                self.sink.send(alert)
                self.repo.mark_delivery(alert.alert_id, True, None)
                return alert.model_copy(update={"delivered": True})
            except AlertDeliveryError as exc:
                self.repo.mark_delivery(alert.alert_id, False, str(exc))
                return alert.model_copy(update={"delivery_error": str(exc)})
        return alert


def deliver_pending(service: WatchlistService, alerts: Sequence[Alert]) -> int:
    """Retry delivery of undelivered alerts; returns the number delivered."""
    delivered = 0
    for alert in alerts:
        if alert.delivered or service.sink is None:
            continue
        try:
            service.sink.send(alert)
        except AlertDeliveryError as exc:
            service.repo.mark_delivery(alert.alert_id, False, str(exc))
            continue
        service.repo.mark_delivery(alert.alert_id, True, None)
        delivered += 1
    return delivered
