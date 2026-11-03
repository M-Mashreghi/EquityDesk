"""Validated research objects for report composer / reviewer tests (built with the real engines)."""

from __future__ import annotations

from datetime import date
from typing import Any

import factories as f
from equitydesk.analytics.dcf import DcfBase, dcf_with_sensitivity
from equitydesk.analytics.multiples import CompanyFundamentals, multiples_valuation
from equitydesk.analytics.scenarios import build_scenarios
from equitydesk.domain.market import MarketObservation
from equitydesk.domain.metrics import MetricResult, MetricStatus
from equitydesk.domain.research import ClaimType, EvidenceSpan, FindingCategory, SupportStatus
from equitydesk.domain.valuation import DcfAssumptions, ScenarioCase, ScenarioName
from equitydesk.reporting.composer import ThesisPoint

AS_OF = f.dt(2024, 6, 1)
END = date(2023, 9, 30)
DOC = f.document()
Q2 = f.document(
    document_id="doc_10q_2024q2", document_type="10-Q", title="Apple 10-Q Q2 FY2024",
    official_id="0000320193-24-000069", published_at=f.dt(2024, 5, 3, 10), period_end=date(2024, 3, 30),
    source_url="https://www.sec.gov/x",
)  # fmt: skip


def span(quote: str, doc: str = "doc_10k_2023", chunk: str = "doc_10k_2023:s1:p1") -> EvidenceSpan:
    return EvidenceSpan(document_id=doc, chunk_id=chunk, quote=quote, section="Item 1")


def findings() -> list:
    return [
        f.finding(finding_id="fnd_bm", category=FindingCategory.BUSINESS_MODEL,
                  statement="The company designs and sells smartphones, computers and services.",
                  evidence=(span("designs, manufactures and markets smartphones"),)),
        f.finding(finding_id="fnd_seg_unknown", category=FindingCategory.SEGMENT,
                  claim_type=ClaimType.UNKNOWN, statement="segments: not supported by the provided sources",
                  evidence=(), confidence=0.0, support_status=SupportStatus.NOT_SUPPORTED),
        f.finding(finding_id="fnd_risk", category=FindingCategory.RISK, claim_type=ClaimType.INFERENCE,
                  attributes={"title": "Supply chain concentration"}),
        f.finding(finding_id="fnd_cat", category=FindingCategory.CATALYST, claim_type=ClaimType.INFERENCE,
                  statement="New services could expand recurring revenue.",
                  evidence=(span("services net sales increased", "doc_10q_2024q2", "doc_10q_2024q2:s2:p4"),),
                  attributes={"title": "Services growth"}),
        f.finding(finding_id="fnd_guid", category=FindingCategory.GUIDANCE_CHANGE, claim_type=ClaimType.INFERENCE,
                  statement="Management maintained its gross margin outlook.",
                  evidence=(span("we expect gross margin to be", "doc_10q_2024q2", "doc_10q_2024q2:s3:p1"),),
                  confidence=0.4, support_status=SupportStatus.UNCERTAIN),
        f.finding(finding_id="fnd_q", category=FindingCategory.OPEN_QUESTION, claim_type=ClaimType.INFERENCE,
                  statement="How durable is services growth?", support_status=SupportStatus.UNCERTAIN),
    ]  # fmt: skip


def metric(
    name: str, value: float | None, unit: str = "ratio", period: date = END, **kw: Any
) -> MetricResult:
    status = MetricStatus.OK if value is not None else MetricStatus.MISSING_INPUT
    return MetricResult(
        metric_id=f"m:co_apple:{name}:{period.isoformat()}", company_id="co_apple", name=name,
        period_end=period, value=value, unit=unit, formula=kw.pop("formula", f"{name} formula"),
        status=status, missing=kw.pop("missing", () if value is not None else ("x",)), calc_version="1.0.0", **kw,
    )  # fmt: skip


def metrics() -> list[MetricResult]:
    return [
        metric("revenue_growth", -0.028, formula="revenue / prior revenue - 1"),
        metric("revenue_growth", 0.078, period=date(2022, 9, 24)),
        metric("gross_margin", 0.441, formula="gross_profit / revenue"),
        metric("net_debt", 49_000_000_000.0, unit="USD"),
        metric("roic", None, missing=("invested_capital",)),
    ]


SCEN_BASE = DcfBase("co_apple", END, 1000.0, 300.0, 100.0, 100.0,
                    sources={"base_revenue": "m:co_apple:revenue:2023-09-30"})  # fmt: skip
RATIONALE = {
    "revenue_growth": "r",
    "ebit_margin": "r",
    "discount_rate": "r",
    "terminal_growth": "r",
}


def assumptions(name: str, g: float, m: float) -> DcfAssumptions:
    return DcfAssumptions(
        assumption_set_id=f"co_apple:{name}", version=1, source=f"scenario:{name}",
        revenue_growth=(g, g), ebit_margin=(m, m), tax_rate=0.25, da_pct_revenue=0.05,
        capex_pct_revenue=0.05, nwc_pct_incremental_revenue=0.10, discount_rate=0.10,
        terminal_growth=0.02, rationale=RATIONALE,
    )  # fmt: skip


def valuations(run_id: str = "run_1") -> list:
    price = MarketObservation(security_id="sec_aapl_xnas", price=16.0, currency="USD",
                              observed_at=f.dt(2024, 5, 31, 20), price_source="manual:analyst",
                              shares_outstanding=100.0, shares_source="10-Q cover", shares_as_of=date(2024, 4, 19))  # fmt: skip
    peers = [
        CompanyFundamentals("a", "sec_a", END, 1000.0, 200.0, 100.0, 80.0, 200.0, 200.0),
        CompanyFundamentals("b", "sec_b", END, 1000.0, 250.0, 150.0, 120.0, 600.0, 100.0),
    ]
    obs = [price,
           price.model_copy(update={"security_id": "sec_a", "price": 20.0}),
           price.model_copy(update={"security_id": "sec_b", "price": 30.0})]  # fmt: skip
    target = CompanyFundamentals(
        "co_apple", "sec_aapl_xnas", END, 2000.0, 500.0, 250.0, 300.0, 1000.0, 500.0, 100.0
    )
    mult = multiples_valuation(target, peers, obs, AS_OF, "tech:v1", multiples=("ev_sales",))
    dcf = dcf_with_sensitivity(SCEN_BASE, assumptions("base", 0.10, 0.20), (0.09, 0.10), (0.02,))
    cases = [
        ScenarioCase(name=ScenarioName.BULL, assumptions=assumptions("bull", 0.15, 0.25), narrative="services re-accelerate", probability=0.25),
        ScenarioCase(name=ScenarioName.BASE, assumptions=assumptions("base", 0.10, 0.20), narrative="steady growth", probability=0.5),
        ScenarioCase(name=ScenarioName.BEAR, assumptions=assumptions("bear", 0.0, 0.15), narrative="hardware decline", probability=0.25),
    ]  # fmt: skip
    scen = build_scenarios(SCEN_BASE, cases, price)
    return [
        mult.to_valuation_run(run_id, f.T0),
        dcf.to_valuation_run(run_id, AS_OF, f.T0),
        scen.to_valuation_run(run_id, AS_OF, f.T0),
    ]


def thesis(vals: list) -> list[ThesisPoint]:
    return [
        ThesisPoint(statement="Services growth can offset hardware cyclicality.", stance="for",
                    finding_ids=("fnd_cat",), metric_ids=("m:co_apple:gross_margin:2023-09-30",)),
        ThesisPoint(statement="Supply chain concentration is a material risk.", stance="against",
                    finding_ids=("fnd_risk",)),
        ThesisPoint(statement="The base-case DCF sits above the observed price.", stance="for",
                    valuation_ids=(vals[1].valuation_id,)),
        ThesisPoint(statement="Market share will double.", stance="for", finding_ids=("fnd_missing",)),
    ]  # fmt: skip


def inputs() -> dict[str, Any]:
    vals = valuations()
    return {
        "run": f.run(as_of=AS_OF, snapshot_id="snap_1"),
        "company": f.company(),
        "documents": [DOC, Q2],
        "findings": findings(),
        "metrics": metrics(),
        "valuations": vals,
        "thesis": thesis(vals),
    }
