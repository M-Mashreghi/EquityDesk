"""FastAPI research API. Thin transport layer over `ResearchService`; no research logic here."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from equitydesk import __version__
from equitydesk.application.research_service import ResearchService, ServiceError
from equitydesk.application.resolver import ResolutionResult
from equitydesk.application.watchlist import WatchlistService
from equitydesk.application.workflow import ResearchInputs, ResearchWorkflow
from equitydesk.domain.common import DomainError
from equitydesk.domain.companies import Company
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.financials import FinancialFact
from equitydesk.domain.metrics import MetricResult
from equitydesk.domain.research import ResearchRun
from equitydesk.domain.valuation import ValuationRun
from equitydesk.domain.watchlist import Alert, WatchlistEntry

STATUS_BY_KIND = {"invalid_request": 422, "not_found": 404, "ambiguous": 409}


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200, description="ticker, CIK, ISIN or name")
    exchange: str | None = Field(default=None, max_length=8, description="MIC, e.g. XNAS")
    as_of: datetime | None = Field(default=None, description="timezone-aware; defaults to now")


class WatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_interval_hours: int = Field(default=24, ge=1, le=24 * 90)
    alert_on_new_filing: bool = True
    alert_on_run_finished: bool = True


class RefreshRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime | None = Field(
        default=None, description="later than the refreshed run; defaults to now"
    )


class ResolutionResponse(BaseModel):
    status: str
    query: str
    company: Company | None
    security_id: str | None
    matched_on: str | None
    candidates: list[dict[str, Any]]
    suggestions: list[str]
    message: str
    notes: list[str]

    @classmethod
    def of(cls, r: ResolutionResult) -> ResolutionResponse:
        return cls(
            status=r.status.value, query=r.query, company=r.company,
            security_id=r.security.security_id if r.security else None, matched_on=r.matched_on,
            candidates=[{"company_id": c.company.company_id, "name": c.company.name,
                         "security_id": c.security.security_id if c.security else None,
                         "matched_on": c.matched_on} for c in r.candidates],
            suggestions=list(r.suggestions), message=r.message, notes=list(r.notes),
        )  # fmt: skip


class FinancialsResponse(BaseModel):
    company_id: str
    as_of: datetime
    facts: list[FinancialFact]
    metrics: list[MetricResult]


class ReportResponse(BaseModel):
    report: dict[str, Any]
    review: dict[str, Any] | None
    review_passed: bool | None


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def _service(request: Request) -> ResearchService:
    service: ResearchService = request.app.state.service
    return service


def _watchlist(request: Request) -> WatchlistService:
    watchlist: WatchlistService | None = request.app.state.watchlist
    if watchlist is None:
        raise ServiceError("watchlist is not configured")
    return watchlist


# Module-level aliases so FastAPI can resolve the (postponed) annotations.
Svc = Annotated[ResearchService, Depends(_service)]
Watch = Annotated[WatchlistService, Depends(_watchlist)]


def _workflow(request: Request) -> ResearchWorkflow:
    workflow: ResearchWorkflow | None = request.app.state.workflow
    if workflow is None:
        raise ServiceError("research workflow is not configured")
    return workflow


Flow = Annotated[ResearchWorkflow, Depends(_workflow)]


def create_app(
    service: ResearchService,
    watchlist: WatchlistService | None = None,
    workflow: ResearchWorkflow | None = None,
) -> FastAPI:
    app = FastAPI(title="EquityDesk Research API", version=__version__)
    app.state.service = service
    app.state.watchlist = watchlist
    app.state.workflow = workflow

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return _error(STATUS_BY_KIND.get(exc.kind, 400), exc.kind, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "loc": [str(p) for p in e.get("loc", ())],
                "msg": e.get("msg", ""),
                "type": e.get("type", ""),
            }
            for e in exc.errors()
        ]
        return _error(422, "validation_error", "request validation failed", {"errors": details})

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return _error(422, "domain_rule_violation", str(exc))

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/companies/resolve", response_model=ResolutionResponse)
    def resolve(
        s: Svc,
        q: Annotated[str, Query(min_length=1, max_length=200)],
        exchange: Annotated[str | None, Query(max_length=8)] = None,
    ) -> ResolutionResponse:
        return ResolutionResponse.of(s.resolve(q, exchange))

    @app.get("/companies/{company_id}", response_model=Company)
    def company(s: Svc, company_id: str) -> Company:
        return s.get_company(company_id)

    @app.get("/companies/{company_id}/runs", response_model=list[ResearchRun])
    def company_runs(s: Svc, company_id: str) -> list[ResearchRun]:
        return s.list_runs(company_id)

    @app.post("/research-runs", response_model=ResearchRun, status_code=201)
    def create_run(s: Svc, body: CreateRunRequest) -> ResearchRun:
        return s.create_run(body.query, body.as_of, body.exchange)

    @app.get("/research-runs/{run_id}", response_model=ResearchRun)
    def get_run(s: Svc, run_id: str) -> ResearchRun:
        return s.get_run(run_id)

    @app.post("/research-runs/{run_id}/refresh", response_model=ResearchRun, status_code=201)
    def refresh_run(s: Svc, run_id: str, body: RefreshRunRequest) -> ResearchRun:
        return s.refresh_run(run_id, body.as_of)

    @app.post("/research-runs/{run_id}/execute")
    def execute(s: Svc, flow: Flow, run_id: str, body: ResearchInputs) -> dict[str, Any]:
        run = s.get_run(run_id)
        if run.status.value != "Created":
            raise ServiceError(f"run is {run.status.value}; only Created runs can be executed")
        outcome = flow.execute(run_id, body)
        return {
            "run": outcome.run.model_dump(mode="json"),
            "report_id": outcome.report_id,
            "review_passed": outcome.review_passed,
            "notes": outcome.notes,
        }

    @app.get("/research-runs/{run_id}/diff")
    def diff(s: Svc, run_id: str, against: Annotated[str, Query(min_length=1)]) -> dict[str, Any]:
        return s.diff(against, run_id).as_dict()

    @app.get("/research-runs/{run_id}/documents", response_model=list[SourceDocument])
    def documents(s: Svc, run_id: str) -> list[SourceDocument]:
        return s.run_documents(run_id)

    @app.get("/research-runs/{run_id}/financials", response_model=FinancialsResponse)
    def financials(s: Svc, run_id: str) -> FinancialsResponse:
        v = s.run_financials(run_id)
        return FinancialsResponse(
            company_id=v.company_id, as_of=v.as_of, facts=v.facts, metrics=v.metrics
        )

    @app.get("/research-runs/{run_id}/valuations", response_model=list[ValuationRun])
    def valuations(s: Svc, run_id: str) -> list[ValuationRun]:
        return s.run_valuations(run_id)

    @app.get("/research-runs/{run_id}/report", response_model=None)
    def report(
        s: Svc,
        run_id: str,
        fmt: Annotated[str, Query(alias="format", pattern="^(json|markdown)$")] = "json",
    ) -> ReportResponse | PlainTextResponse:
        v = s.run_report(run_id)
        if fmt == "markdown":
            return PlainTextResponse(v.markdown, media_type="text/markdown")
        return ReportResponse(
            report=v.report.model_dump(mode="json"),
            review=v.review.model_dump(mode="json") if v.review else None,
            review_passed=v.review.passed if v.review else None,
        )

    @app.get("/watchlist", response_model=list[WatchlistEntry])
    def watchlist_entries(w: Watch) -> list[WatchlistEntry]:
        return w.entries()

    @app.put("/watchlist/{company_id}", response_model=WatchlistEntry)
    def watch(w: Watch, company_id: str, body: WatchRequest) -> WatchlistEntry:
        return w.watch(company_id, body.refresh_interval_hours, body.alert_on_new_filing,
                       body.alert_on_run_finished)  # fmt: skip

    @app.delete("/watchlist/{company_id}", status_code=204)
    def unwatch(w: Watch, company_id: str) -> None:
        w.unwatch(company_id)

    @app.post("/watchlist/tick")
    def tick(w: Watch) -> dict[str, Any]:
        result = w.tick()
        return {
            "refreshes": [vars(r) for r in result.refreshes],
            "alerts": [a.model_dump(mode="json") for a in result.alerts],
        }

    @app.get("/alerts", response_model=list[Alert])
    def alerts(
        w: Watch, company_id: str | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 100
    ) -> list[Alert]:
        return w.alerts(company_id, limit)

    return app


def default_app() -> FastAPI:  # pragma: no cover - process entry point
    from equitydesk.bootstrap import build_container, build_workflow

    container = build_container()
    try:
        workflow: ResearchWorkflow | None = build_workflow(container)
    except ValueError:  # e.g. SEC_USER_AGENT missing: read-only API still starts
        workflow = None
    return create_app(container.research, container.watchlist, workflow)
