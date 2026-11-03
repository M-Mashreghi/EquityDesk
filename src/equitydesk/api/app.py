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
from equitydesk.domain.common import DomainError
from equitydesk.domain.companies import Company
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.financials import FinancialFact
from equitydesk.domain.metrics import MetricResult
from equitydesk.domain.research import ResearchRun
from equitydesk.domain.valuation import ValuationRun

STATUS_BY_KIND = {"invalid_request": 422, "not_found": 404, "ambiguous": 409}


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200, description="ticker, CIK, ISIN or name")
    exchange: str | None = Field(default=None, max_length=8, description="MIC, e.g. XNAS")
    as_of: datetime | None = Field(default=None, description="timezone-aware; defaults to now")


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


# Module-level alias so FastAPI can resolve the (postponed) annotation.
Svc = Annotated[ResearchService, Depends(_service)]


def create_app(service: ResearchService) -> FastAPI:
    app = FastAPI(title="EquityDesk Research API", version=__version__)
    app.state.service = service

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

    return app


def default_app() -> FastAPI:  # pragma: no cover - process entry point
    from equitydesk.bootstrap import build_container

    return create_app(build_container().research)
