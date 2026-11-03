"""Deterministic research report composer.

The composer only assembles already-validated objects (quality-gated findings, metric results,
persisted valuation runs, explicit thesis points) into the `report.v1` schema. It never computes
financial numbers itself: every number in a claim is copied from a metric or valuation output and
linked by id. Evidence spans become citations carrying full document provenance.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from equitydesk.domain.common import DomainModel
from equitydesk.domain.companies import Company
from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.metrics import MetricResult, MetricStatus
from equitydesk.domain.provenance import FutureLeakageError
from equitydesk.domain.report import (
    SECTION_ORDER,
    SECTION_TITLES,
    Citation,
    ReportClaim,
    ReportSection,
    ResearchReport,
    SectionKey,
)
from equitydesk.domain.research import (
    ClaimType,
    EvidenceSpan,
    FindingCategory,
    ResearchFinding,
    ResearchRun,
    SupportStatus,
)
from equitydesk.domain.valuation import ValuationMethod, ValuationRun

COMPOSER_VERSION = "1.0.0"

FINDING_SECTIONS: dict[FindingCategory, SectionKey] = {
    FindingCategory.BUSINESS_MODEL: SectionKey.COMPANY_OVERVIEW,
    FindingCategory.SEGMENT: SectionKey.COMPANY_OVERVIEW,
    FindingCategory.REVENUE_DRIVER: SectionKey.COMPANY_OVERVIEW,
    FindingCategory.CUSTOMER: SectionKey.COMPANY_OVERVIEW,
    FindingCategory.GEOGRAPHY: SectionKey.COMPANY_OVERVIEW,
    FindingCategory.STRATEGY_CHANGE: SectionKey.GUIDANCE_AND_STRATEGY,
    FindingCategory.GUIDANCE_CHANGE: SectionKey.GUIDANCE_AND_STRATEGY,
    FindingCategory.MANAGEMENT_TONE: SectionKey.GUIDANCE_AND_STRATEGY,
    FindingCategory.RISK: SectionKey.RISKS,
    FindingCategory.CATALYST: SectionKey.CATALYSTS,
    FindingCategory.OPEN_QUESTION: SectionKey.OPEN_QUESTIONS,
}

# Metrics shown in the financial-analysis section, in display order.
REPORT_METRICS = (
    "revenue_growth", "gross_margin", "operating_margin", "net_margin", "fcf_margin",
    "free_cash_flow", "roic", "roe", "net_debt", "net_debt_to_ebitda", "current_ratio",
    "diluted_share_change", "cash_conversion",
)  # fmt: skip
METRIC_LABELS = {name: name.replace("_", " ").replace("fcf", "FCF").replace("roic", "ROIC")
                 .replace("roe", "ROE").replace("ebitda", "EBITDA") for name in REPORT_METRICS}  # fmt: skip


class ThesisPoint(DomainModel):
    """An analyst (or model-drafted) thesis statement that must rest on validated objects."""

    statement: str
    stance: str  # "for" | "against"
    finding_ids: tuple[str, ...] = ()
    metric_ids: tuple[str, ...] = ()
    valuation_ids: tuple[str, ...] = ()
    author: str = "user:analyst"


def format_value(value: float, unit: str) -> str:
    """Display formatting only; values are never recomputed here."""
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if unit == "x":
        return f"{value:.2f}x"
    if unit == "days":
        return f"{value:.0f} days"
    if unit in ("USD", "currency"):
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _claim_id(section: SectionKey, text: str) -> str:
    return "cl_" + hashlib.sha256(f"{section.value}|{text}".encode()).hexdigest()[:16]


def citation_id(span: EvidenceSpan) -> str:
    key = f"{span.document_id}|{span.chunk_id}|{span.quote}"
    return "cit_" + hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass
class _Builder:
    run: ResearchRun
    documents: dict[str, SourceDocument]
    citations: dict[str, Citation]

    def cite(self, spans: Iterable[EvidenceSpan]) -> tuple[str, ...]:
        ids: list[str] = []
        for span in spans:
            doc = self.documents.get(span.document_id)
            if doc is None:
                continue  # unknown document: the reviewer flags the uncited claim
            if doc.published_at > self.run.as_of:
                raise FutureLeakageError(self.run.as_of, [doc.document_id])
            cid = citation_id(span)
            self.citations.setdefault(cid, Citation(
                citation_id=cid, document_id=doc.document_id, source_type=doc.source_type,
                document_type=doc.document_type, title=doc.title, source_url=doc.source_url,
                official_id=doc.official_id, published_at=doc.published_at,
                retrieved_at=doc.retrieved_at, content_hash=doc.content_hash,
                chunk_id=span.chunk_id, section=span.section, page=span.page, quote=span.quote,
            ))  # fmt: skip
            if cid not in ids:
                ids.append(cid)
        return tuple(ids)


def _finding_claim(b: _Builder, f: ResearchFinding, section: SectionKey) -> ReportClaim:
    cites = b.cite(f.evidence)
    support = f.support_status
    if (
        support is SupportStatus.SUPPORTED
        and not cites
        and f.claim_type is not ClaimType.ASSUMPTION
    ):
        support = SupportStatus.UNCERTAIN
    text = f.statement
    if f.category in (FindingCategory.RISK, FindingCategory.CATALYST) and f.attributes.get("title"):
        text = f"{f.attributes['title']}: {f.statement}"
    return ReportClaim(
        claim_id=_claim_id(section, text), section=section, text=text, claim_type=f.claim_type,
        support_status=support, citation_ids=cites, finding_ids=(f.finding_id,),
        assumption_source=f.attributes.get("assumption_source") if f.claim_type is ClaimType.ASSUMPTION else None,
    )  # fmt: skip


def _metric_claims(metrics: Sequence[MetricResult]) -> tuple[list[ReportClaim], list[str]]:
    latest: dict[str, MetricResult] = {}
    for r in metrics:
        if r.name in REPORT_METRICS and (
            r.name not in latest or r.period_end > latest[r.name].period_end
        ):
            latest[r.name] = r
    claims: list[ReportClaim] = []
    notes: list[str] = []
    section = SectionKey.FINANCIAL_ANALYSIS
    for name in REPORT_METRICS:
        m = latest.get(name)
        if m is None:
            continue
        label = METRIC_LABELS[name][0].upper() + METRIC_LABELS[name][1:]
        if m.status is MetricStatus.OK and m.value is not None:
            text = f"{label} for the period ended {m.period_end.isoformat()}: {format_value(m.value, m.unit)} ({m.formula})."
            claims.append(ReportClaim(
                claim_id=_claim_id(section, text), section=section, text=text,
                claim_type=ClaimType.FACT, support_status=SupportStatus.SUPPORTED,
                metric_ids=(m.metric_id,), values={name: m.value},
            ))  # fmt: skip
        else:
            reason = (
                f"missing inputs: {', '.join(m.missing)}"
                if m.missing
                else (m.note or m.status.value)
            )
            text = f"{label} for the period ended {m.period_end.isoformat()} is not available ({reason})."
            claims.append(ReportClaim(
                claim_id=_claim_id(section, text), section=section, text=text,
                claim_type=ClaimType.UNKNOWN, support_status=SupportStatus.NOT_SUPPORTED,
                metric_ids=(m.metric_id,),
            ))  # fmt: skip
    if not latest:
        notes.append("no deterministic metrics were available for this run")
    return claims, notes


def _valuation_claims(
    valuations: Sequence[ValuationRun],
) -> tuple[list[ReportClaim], list[ReportClaim], list[str]]:
    val: list[ReportClaim] = []
    scn: list[ReportClaim] = []
    notes: list[str] = []
    for v in valuations:
        if v.method is ValuationMethod.MULTIPLES:
            val.extend(_multiples_claims(v))
        elif v.method is ValuationMethod.DCF:
            val.extend(_dcf_claims(v))
        elif v.method is ValuationMethod.SCENARIO:
            claims, scn_notes = _scenario_claims(v)
            scn.extend(claims)
            notes.extend(scn_notes)
    if not valuations:
        notes.append("no valuation was produced for this run")
    return val, scn, notes


def _calc_claim(
    section: SectionKey, text: str, valuation_id: str, values: dict[str, float | None]
) -> ReportClaim:
    return ReportClaim(
        claim_id=_claim_id(section, text),
        section=section,
        text=text,
        claim_type=ClaimType.FACT,
        support_status=SupportStatus.SUPPORTED,
        valuation_ids=(valuation_id,),
        values=values,
    )


def _multiples_claims(v: ValuationRun) -> list[ReportClaim]:
    claims = []
    peer_set = v.inputs.get("peer_set_id")
    for multiple, out in sorted(v.outputs.items()):
        text = (
            f"Peer {multiple} (peer set {peer_set}, {out['peer_n']} peers: {', '.join(out['peers_used'])}, "
            f"valuation date {v.valuation_date.date().isoformat()}) implies {out['mid']:.2f} per share "
            f"at the peer median (p25-p75 range {out['low']:.2f} to {out['high']:.2f})."
        )
        claims.append(_calc_claim(SectionKey.VALUATION, text, v.valuation_id,
                                  {"low": out["low"], "mid": out["mid"], "high": out["high"]}))  # fmt: skip
    return claims


def _dcf_claims(v: ValuationRun) -> list[ReportClaim]:
    a = v.assumptions
    o = v.outputs
    terminal = (f"terminal growth {a['terminal_growth'] * 100:.1f}%" if a.get("terminal_method") == "gordon"
                else f"exit EV/EBITDA {a.get('exit_ev_ebitda')}x")  # fmt: skip
    text = (
        f"DCF ({len(a['revenue_growth'])}-year forecast, discount rate {a['discount_rate'] * 100:.1f}%, {terminal}) "
        f"gives an enterprise value of {o['enterprise_value']:,.0f} and {o['value_per_share']:.2f} per share; "
        f"the terminal value is {o['terminal_share_of_ev'] * 100:.0f}% of EV. "
        f"This is an assumption-driven estimate, not a price target."
    )
    return [_calc_claim(SectionKey.VALUATION, text, v.valuation_id,
                        {"enterprise_value": o["enterprise_value"], "value_per_share": o["value_per_share"],
                         "terminal_share_of_ev": o["terminal_share_of_ev"]})]  # fmt: skip


def _scenario_claims(v: ValuationRun) -> tuple[list[ReportClaim], list[str]]:
    section = SectionKey.SCENARIOS
    claims: list[ReportClaim] = []
    for name in ("bull", "base", "bear"):
        if name not in v.outputs:
            continue
        a = v.assumptions[name]
        values = {x["name"]: x["value"] for x in v.outputs[name]["values"]}
        text = f"{name.capitalize()} case assumption: {a['narrative']}"
        claims.append(ReportClaim(
            claim_id=_claim_id(section, text), section=section, text=text,
            claim_type=ClaimType.ASSUMPTION, support_status=SupportStatus.UNCERTAIN,
            valuation_ids=(v.valuation_id,), assumption_source=a["source"],
        ))  # fmt: skip
        upside = v.outputs[name].get("upside")
        text = (
            f"{name.capitalize()} case: revenue CAGR {values['revenue_cagr'] * 100:.1f}%, "
            f"final-year EBIT margin {a['ebit_margin'][-1] * 100:.1f}%, value {values['value_per_share']:.2f} per share"
            + (f" ({upside * 100:+.1f}% vs. the observed price)." if upside is not None else ".")
        )
        claims.append(_calc_claim(section, text, v.valuation_id,
                                  {"revenue_cagr": values["revenue_cagr"],
                                   "value_per_share": values["value_per_share"], "upside": upside}))  # fmt: skip
    weighted = v.outputs.get("probability_weighted_value")
    if weighted is not None:
        text = f"Probability-weighted scenario value: {weighted:.2f} per share."
        claims.append(
            _calc_claim(section, text, v.valuation_id, {"probability_weighted_value": weighted})
        )
    notes = [f"scenario check {i['code']}{' (blocking)' if i['blocking'] else ''}: {i['message']}"
             for i in v.outputs.get("issues", [])]  # fmt: skip
    return claims, notes


def _thesis_claims(
    b: _Builder,
    points: Sequence[ThesisPoint],
    findings: dict[str, ResearchFinding],
    metric_ids: set[str],
    valuation_ids: set[str],
) -> tuple[list[ReportClaim], list[ReportClaim], list[str]]:
    section = SectionKey.INVESTMENT_THESIS
    thesis: list[ReportClaim] = []
    unsupported: list[ReportClaim] = []
    notes: list[str] = []
    for p in points:
        fids = tuple(f for f in p.finding_ids if f in findings)
        mids = tuple(m for m in p.metric_ids if m in metric_ids)
        vids = tuple(v for v in p.valuation_ids if v in valuation_ids)
        dropped = (
            (set(p.finding_ids) - set(fids))
            | (set(p.metric_ids) - set(mids))
            | (set(p.valuation_ids) - set(vids))
        )
        if dropped:
            notes.append(f"thesis point references unknown or rejected objects: {sorted(dropped)}")
        cites = b.cite(span for fid in fids for span in findings[fid].evidence)
        prefix = "Evidence for" if p.stance == "for" else "Evidence against"
        if not (cites or mids or vids):
            text = f"Unsupported thesis point (needs evidence): {p.statement}"
            unsupported.append(ReportClaim(
                claim_id=_claim_id(SectionKey.OPEN_QUESTIONS, text), section=SectionKey.OPEN_QUESTIONS,
                text=text, claim_type=ClaimType.UNKNOWN, support_status=SupportStatus.NOT_SUPPORTED,
            ))  # fmt: skip
            continue
        uncertain = any(findings[f].support_status is not SupportStatus.SUPPORTED for f in fids)
        text = f"{prefix}: {p.statement}"
        thesis.append(ReportClaim(
            claim_id=_claim_id(section, text), section=section, text=text,
            claim_type=ClaimType.INFERENCE,
            support_status=SupportStatus.UNCERTAIN if uncertain else SupportStatus.SUPPORTED,
            citation_ids=cites, metric_ids=mids, valuation_ids=vids, finding_ids=fids,
        ))  # fmt: skip
    if not points:
        notes.append("no thesis points were provided")
    return thesis, unsupported, notes


def compose_report(
    run: ResearchRun,
    company: Company,
    documents: Sequence[SourceDocument],
    findings: Sequence[ResearchFinding],
    metrics: Sequence[MetricResult] = (),
    valuations: Sequence[ValuationRun] = (),
    thesis: Sequence[ThesisPoint] = (),
    generated_at: datetime | None = None,
) -> ResearchReport:
    """Assemble a `report.v1` report from validated research objects of one run."""
    b = _Builder(run, {d.document_id: d for d in documents}, {})
    sections: dict[SectionKey, list[ReportClaim]] = {k: [] for k in SECTION_ORDER}
    notes: dict[SectionKey, list[str]] = {k: [] for k in SECTION_ORDER}

    run_findings = [f for f in findings if f.research_run_id == run.research_run_id]
    for f in run_findings:
        key = FINDING_SECTIONS[f.category]
        sections[key].append(_finding_claim(b, f, key))

    metric_claims, metric_notes = _metric_claims(
        [m for m in metrics if m.company_id == run.company_id]
    )
    sections[SectionKey.FINANCIAL_ANALYSIS].extend(metric_claims)
    notes[SectionKey.FINANCIAL_ANALYSIS].extend(metric_notes)

    run_valuations = [v for v in valuations if v.research_run_id == run.research_run_id]
    val_claims, scn_claims, val_notes = _valuation_claims(run_valuations)
    sections[SectionKey.VALUATION].extend(val_claims)
    sections[SectionKey.SCENARIOS].extend(scn_claims)
    for n in val_notes:
        notes[SectionKey.SCENARIOS if n.startswith("scenario") else SectionKey.VALUATION].append(n)

    thesis_claims, unsupported, thesis_notes = _thesis_claims(
        b, thesis, {f.finding_id: f for f in run_findings},
        {m.metric_id for m in metrics}, {v.valuation_id for v in run_valuations},
    )  # fmt: skip
    sections[SectionKey.INVESTMENT_THESIS].extend(thesis_claims)
    sections[SectionKey.OPEN_QUESTIONS].extend(unsupported)
    notes[SectionKey.INVESTMENT_THESIS].extend(thesis_notes)

    for key, claims in sections.items():
        if not claims:
            notes[key].append("no validated content for this section")

    built = tuple(
        ReportSection(
            key=k,
            title=SECTION_TITLES[k],
            claims=tuple(_dedupe(sections[k])),
            notes=tuple(notes[k]),
        )
        for k in SECTION_ORDER
    )
    citations = tuple(
        sorted(b.citations.values(), key=lambda c: (c.published_at, c.document_id, c.chunk_id))
    )
    digest_src: list[Any] = [
        run.research_run_id,
        run.as_of.isoformat(),
        [s.model_dump(mode="json") for s in built],
    ]
    report_id = (
        "rpt_" + hashlib.sha256(json.dumps(digest_src, sort_keys=True).encode()).hexdigest()[:20]
    )
    return ResearchReport(
        report_id=report_id, research_run_id=run.research_run_id, company_id=run.company_id,
        company_name=company.name, as_of=run.as_of, snapshot_id=run.snapshot_id,
        composer_version=COMPOSER_VERSION, sections=built, citations=citations,
        generated_at=generated_at or run.created_at,
    )  # fmt: skip


def _dedupe(claims: list[ReportClaim]) -> list[ReportClaim]:
    seen: set[str] = set()
    out = []
    for c in claims:
        if c.claim_id not in seen:
            seen.add(c.claim_id)
            out.append(c)
    return out
