"""End-to-end research run orchestration.

Created → Collecting → Parsing → Analyzing → Valuing → Reviewing → Completed / NeedsInput

Each stage persists its outputs before the run moves on, and every external system is reached
through a port (filings, reported financial data, market data, LLM), so the whole run can execute
offline against fixtures. Missing user inputs stop the run in NeedsInput; a source outage stops it
in BlockedBySource; unexpected errors mark it Failed with the reason.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from equitydesk.analytics.accounting import StatementPeriod, annual_statements, map_reported_facts
from equitydesk.analytics.dcf import DcfBase, dcf_base_from_statements, dcf_with_sensitivity
from equitydesk.analytics.kpis import Undefined, compute_kpis
from equitydesk.analytics.multiples import CompanyFundamentals, multiples_valuation
from equitydesk.analytics.scenarios import build_scenarios
from equitydesk.application.document_store import DocumentIngestionService
from equitydesk.application.llm import StructuredLLMClient
from equitydesk.application.ports import NormalizedDocumentRepository, RawContentStore
from equitydesk.application.research.business import BusinessModelExtractor
from equitydesk.application.research.grounding import collect_chunks
from equitydesk.application.research.quality_gate import EvidenceQualityGate
from equitydesk.application.research.risks import RiskCatalystExtractor
from equitydesk.application.research_service import ResearchService
from equitydesk.application.snapshot import SnapshotBuilder, SnapshotRepository
from equitydesk.domain.common import DomainModel, utc_now
from equitydesk.domain.companies import Company
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.financials import RawReportedFact
from equitydesk.domain.market import MarketObservation, observation_as_of
from equitydesk.domain.normalized import NormalizedDocument
from equitydesk.domain.provenance import FutureLeakageError
from equitydesk.domain.research import (
    FindingCategory,
    ResearchFinding,
    ResearchRun,
    ResearchStatus,
    SupportStatus,
)
from equitydesk.domain.valuation import DcfAssumptions, ScenarioCase, ScenarioName, ValuationRun
from equitydesk.reporting.composer import ThesisPoint, compose_report
from equitydesk.reporting.reviewer import LlmContradictionReviewer, apply_review, review_report

WORKFLOW_VERSION = "1.0.0"


# --- ports ------------------------------------------------------------------------------------


@dataclass
class Collected:
    documents: list[tuple[SourceDocument, bytes]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


class FilingSource(Protocol):
    def collect(self, company: Company, as_of: datetime) -> Collected: ...


class ReportedFactsSource(Protocol):
    def reported_facts(self, company: Company) -> list[RawReportedFact]: ...


class MarketDataSource(Protocol):
    def observations(
        self, security_ids: Sequence[str], as_of: datetime
    ) -> list[MarketObservation]: ...


class DocumentParser(Protocol):
    def parse(
        self, document_id: str, content: bytes, mime_type: str | None
    ) -> NormalizedDocument: ...


class RunHook(Protocol):
    def run_finished(self, research_run_id: str) -> object: ...


# --- user inputs ------------------------------------------------------------------------------


class PeerInput(DomainModel):
    """An explicit peer with its sourced trailing fundamentals (no model-selected peers)."""

    company_id: str
    security_id: str
    fiscal_period_end: str
    revenue: float | None = None
    ebitda: float | None = None
    net_income: float | None = None
    free_cash_flow: float | None = None
    total_debt: float | None = None
    cash_and_investments: float | None = None
    sources: dict[str, str] = {}


class ResearchInputs(DomainModel):
    """Analyst inputs for the valuation stage. Nothing here may come from a model."""

    scenarios: tuple[ScenarioCase, ...] = ()
    dcf: DcfAssumptions | None = None  # defaults to the base scenario's assumptions
    peer_set_id: str | None = None
    peers: tuple[PeerInput, ...] = ()
    thesis: tuple[ThesisPoint, ...] = ()
    sensitivity_discount_rates: tuple[float, ...] = (-0.01, 0.0, 0.01)  # offsets around the base
    sensitivity_terminal_growth: tuple[float, ...] = (-0.005, 0.0, 0.005)


@dataclass
class RunOutcome:
    run: ResearchRun
    notes: list[str] = field(default_factory=list)
    report_id: str | None = None
    review_passed: bool | None = None


class NeedsInput(Exception):
    pass


class BlockedBySource(Exception):
    pass


# --- workflow ---------------------------------------------------------------------------------


@dataclass
class ResearchWorkflow:
    service: ResearchService
    snapshots: SnapshotRepository
    normalized: NormalizedDocumentRepository
    raw_store: RawContentStore
    filings: FilingSource
    reported_facts: ReportedFactsSource
    market_data: MarketDataSource
    parser: DocumentParser
    llm: StructuredLLMClient
    llm_review: bool = True
    hook: RunHook | None = None
    clock: Callable[[], datetime] = field(default=utc_now)

    def execute(self, research_run_id: str, inputs: ResearchInputs | None = None) -> RunOutcome:
        inputs = inputs or ResearchInputs()
        run = self.service.get_run(research_run_id)
        if run.status is not ResearchStatus.CREATED:
            raise ValueError(f"run {research_run_id} is {run.status}; only Created runs execute")
        outcome = RunOutcome(run)
        try:
            self._pipeline(outcome, inputs)
        except BlockedBySource as exc:
            self._move(outcome, ResearchStatus.BLOCKED_BY_SOURCE, str(exc))
        except NeedsInput as exc:
            self._move(outcome, ResearchStatus.NEEDS_INPUT, str(exc))
        except Exception as exc:  # any unexpected failure is recorded on the run, never hidden
            self._move(outcome, ResearchStatus.FAILED, f"{type(exc).__name__}: {exc}")
        if self.hook is not None and outcome.run.status in (
            ResearchStatus.COMPLETED,
            ResearchStatus.NEEDS_INPUT,
        ):
            self.hook.run_finished(outcome.run.research_run_id)
        return outcome

    def _move(self, outcome: RunOutcome, status: ResearchStatus, reason: str | None = None,
              **updates: object) -> None:  # fmt: skip
        run = outcome.run.transition(status, reason, self.clock())
        if updates:
            run = run.model_copy(update=updates)
        self.service.runs.update_run(run)
        outcome.run = run

    def _pipeline(self, outcome: RunOutcome, inputs: ResearchInputs) -> None:
        svc = self.service
        run = outcome.run
        company = svc.get_company(run.company_id)

        # 1. Collecting: official filings available at as_of only.
        self._move(outcome, ResearchStatus.COLLECTING)
        collected = self.filings.collect(company, run.as_of)
        outcome.notes += [f"collection: {x}" for x in collected.failures]
        leaked = [d.document_id for d, _ in collected.documents if d.published_at > run.as_of]
        if leaked:
            raise FutureLeakageError(run.as_of, leaked)
        ingest = DocumentIngestionService(svc.documents, self.raw_store)
        for doc, content in collected.documents:
            ingest.ingest(doc, content)
        if not collected.documents and not svc.documents.documents_for_company(
            run.company_id, run.as_of
        ):
            raise BlockedBySource(
                "no primary documents could be collected: " + "; ".join(collected.failures)
            )

        # 2. Parsing: point-in-time snapshot, normalized documents.
        self._move(outcome, ResearchStatus.PARSING)
        snapshot = SnapshotBuilder(svc.documents, self.snapshots).build(outcome.run)
        documents = [svc.documents.get_document(d) for d in snapshot.document_ids]
        normalized: dict[str, NormalizedDocument] = {}
        for doc in documents:
            try:
                normalized[doc.document_id] = self.normalized.get_normalized(doc.document_id)
                continue
            except LookupError:
                pass
            try:
                content = ingest.load_content(doc)
                parsed = self.parser.parse(doc.document_id, content, doc.mime_type)
            except Exception as exc:
                outcome.notes.append(
                    f"parse: {doc.document_id} skipped ({type(exc).__name__}: {exc})"
                )
                continue
            self.normalized.add_normalized(parsed)
            normalized[doc.document_id] = parsed
        if not normalized:
            raise RuntimeError("no snapshot document could be parsed")

        # 3. Analyzing: reported facts -> statements -> KPIs; LLM findings through the gate.
        self._move(outcome, ResearchStatus.ANALYZING, snapshot_id=snapshot.snapshot_id)
        raw = self.reported_facts.reported_facts(company)
        mapped = map_reported_facts(run.company_id, raw, run.as_of)
        svc.facts.add_facts(mapped.facts)
        statements = annual_statements(mapped.facts)
        metrics = compute_kpis(run.company_id, statements)

        chunks = collect_chunks(normalized.values())
        now = self.clock()
        business = BusinessModelExtractor(self.llm).extract(
            run.research_run_id, company.name, chunks, now
        )
        risks = RiskCatalystExtractor(self.llm).extract(
            run.research_run_id, company.name, chunks, now
        )
        for name, failure in (
            ("business model", business.failure),
            ("risks/catalysts", risks.failure),
        ):
            if failure is not None:
                outcome.notes.append(f"llm {name} extraction failed: {failure.code.value}")
        candidates = [*business.findings, *risks.findings]
        gate = EvidenceQualityGate().validate(
            run.research_run_id, run.as_of, snapshot, candidates,
            {d.document_id: d for d in documents}, normalized,
        )  # fmt: skip
        outcome.notes += [
            f"quality gate rejected {i.finding_id}: {i.code.value}" for i in gate.blocking
        ]
        svc.findings.add_findings(gate.accepted)
        models = {f.model_version for f in gate.accepted if f.model_version}
        prompts = {f.category.value: f.prompt_version for f in candidates if f.prompt_version}

        # 4. Valuing: explicit assumptions only; missing inputs stop the run for the analyst.
        self._move(outcome, ResearchStatus.VALUING, model_version=",".join(sorted(models)) or None,
                   prompt_versions={k: v for k, v in prompts.items() if v})  # fmt: skip
        valuations = self._value(outcome, inputs, statements)

        # 5. Reviewing: compose, review independently, gate completion.
        self._move(outcome, ResearchStatus.REVIEWING)
        thesis = list(inputs.thesis) or rule_based_thesis(gate.accepted, valuations)
        report = compose_report(outcome.run, company, documents, gate.accepted, metrics, valuations,
                                thesis, generated_at=self.clock())  # fmt: skip
        svc.reports.add_report(report)
        llm_reviewer = LlmContradictionReviewer(self.llm) if self.llm_review else None
        review = review_report(report, documents, metrics, valuations, snapshot.document_ids,
                               llm_reviewer, reviewed_at=self.clock())  # fmt: skip
        svc.reports.add_review(review)
        outcome.report_id, outcome.review_passed = report.report_id, review.passed
        final = apply_review(outcome.run, review, self.clock())
        svc.runs.update_run(final)
        outcome.run = final

    def _value(
        self, outcome: RunOutcome, inputs: ResearchInputs, statements: dict[date, StatementPeriod]
    ) -> list[ValuationRun]:
        run = outcome.run
        if not inputs.scenarios:
            raise NeedsInput("valuation needs explicit bull/base/bear scenario assumptions")
        try:
            base = dcf_base_from_statements(run.company_id, statements)
        except Undefined as exc:
            raise NeedsInput(f"valuation base unavailable: {exc}") from exc
        security_ids = [s for s in [run.security_id, *(p.security_id for p in inputs.peers)] if s]
        observations = self.market_data.observations(security_ids, run.as_of)
        price: MarketObservation | None = None
        if run.security_id:
            try:
                price = observation_as_of(observations, run.security_id, run.as_of)
            except (LookupError, FutureLeakageError):
                outcome.notes.append("no market price at as_of: upside not computed")
        created = self.clock()
        valuations: list[ValuationRun] = []

        scenarios = build_scenarios(base, inputs.scenarios, price)
        valuations.append(scenarios.to_valuation_run(run.research_run_id, run.as_of, created))
        if not scenarios.is_consistent:
            outcome.notes += [f"scenario: {i.code}" for i in scenarios.issues if i.blocking]

        dcf_assumptions = inputs.dcf or next(
            (c.assumptions for c in inputs.scenarios if c.name is ScenarioName.BASE), None
        )
        if dcf_assumptions is not None:
            r = dcf_assumptions.discount_rate
            rates = tuple(round(r + d, 6) for d in inputs.sensitivity_discount_rates)
            growth = (
                dcf_assumptions.terminal_growth
                if dcf_assumptions.terminal_growth is not None
                else 0.0
            )
            terminals = tuple(round(growth + d, 6) for d in inputs.sensitivity_terminal_growth)
            try:
                dcf = dcf_with_sensitivity(base, dcf_assumptions, rates, terminals)
                valuations.append(dcf.to_valuation_run(run.research_run_id, run.as_of, created))
            except Undefined as exc:
                outcome.notes.append(f"dcf not computable: {exc}")

        if inputs.peers and run.security_id:
            target = _target_fundamentals(run, base, statements)
            peers = [CompanyFundamentals(p.company_id, p.security_id, datetime.fromisoformat(p.fiscal_period_end).date(),
                                         p.revenue, p.ebitda, p.net_income, p.free_cash_flow, p.total_debt,
                                         p.cash_and_investments, sources=dict(p.sources)) for p in inputs.peers]  # fmt: skip
            try:
                mult = multiples_valuation(
                    target, peers, observations, run.as_of, inputs.peer_set_id or "adhoc"
                )
                valuations.append(mult.to_valuation_run(run.research_run_id, created))
                outcome.notes += [f"multiples: {n}" for n in mult.notes]
            except (LookupError, Undefined, FutureLeakageError) as exc:
                outcome.notes.append(f"multiples not computable: {exc}")

        for v in valuations:
            self.service.valuations.add_valuation(v)
        return valuations


def _target_fundamentals(
    run: ResearchRun, base: DcfBase, statements: dict[date, StatementPeriod]
) -> CompanyFundamentals:
    assert run.security_id is not None
    period = statements[max(statements)]
    ni = period.value("NetIncome")
    ocf, capex = period.value("OperatingCashFlow"), period.value("CapitalExpenditure")
    oi, da = period.value("OperatingIncome"), period.value("DepreciationAmortization")
    return CompanyFundamentals(
        run.company_id, run.security_id, base.base_period_end, base.base_revenue,
        oi + da if oi is not None and da is not None else None, ni,
        ocf - capex if ocf is not None and capex is not None else None,
        base.total_debt, base.cash_and_investments, base.diluted_shares, sources=dict(base.sources),
    )  # fmt: skip


def rule_based_thesis(
    findings: Sequence[ResearchFinding], valuations: Sequence[ValuationRun]
) -> list[ThesisPoint]:
    """Deterministic thesis skeleton (`rule:thesis_from_findings`): supported catalysts are
    evidence for, supported risks evidence against, and the base scenario versus the observed
    price. Statements carry no numbers; numbers stay in linked calculations."""
    points: list[ThesisPoint] = []
    for f in findings:
        if f.support_status is not SupportStatus.SUPPORTED:
            continue
        title = str(f.attributes.get("title") or f.statement)
        if f.category is FindingCategory.CATALYST:
            points.append(ThesisPoint(statement=f"Catalyst: {title}", stance="for", finding_ids=(f.finding_id,),
                                      author="rule:thesis_from_findings"))  # fmt: skip
        elif f.category is FindingCategory.RISK:
            points.append(ThesisPoint(statement=f"Risk: {title}", stance="against", finding_ids=(f.finding_id,),
                                      author="rule:thesis_from_findings"))  # fmt: skip
    scenario = next((v for v in valuations if v.method.value == "scenario"), None)
    if scenario is not None and scenario.outputs.get("base", {}).get("upside") is not None:
        upside = scenario.outputs["base"]["upside"]
        stance = "for" if upside > 0 else "against"
        side = "above" if upside > 0 else "at or below"
        points.append(ThesisPoint(statement=f"The base-case scenario value is {side} the observed price.",
                                  stance=stance, valuation_ids=(scenario.valuation_id,),
                                  author="rule:thesis_from_findings"))  # fmt: skip
    return points
