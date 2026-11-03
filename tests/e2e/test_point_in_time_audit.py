"""Point-in-time leakage audit and frozen-input reproducibility suite.

The leakage matrix plants information that becomes public *after* `as_of` at every layer that
reads time-dependent data and asserts it can never reach a research run. The reproducibility
check replays a frozen run (fixtures in tests/fixtures/reproducibility) and compares it with
the recorded golden values.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import factories as f
import report_fixtures as rf
from e2e_support import INPUTS, PRICES, sec
from equitydesk.analytics.accounting import map_reported_facts
from equitydesk.analytics.multiples import CompanyFundamentals, multiples_valuation
from equitydesk.api.app import create_app
from equitydesk.application.research.quality_gate import EvidenceQualityGate
from equitydesk.application.snapshot import SnapshotBuilder
from equitydesk.bootstrap import build_container, build_workflow
from equitydesk.config import Settings
from equitydesk.domain.financials import RawReportedFact
from equitydesk.domain.market import MarketObservation, observation_as_of
from equitydesk.domain.peers import PeerMember, PeerSelectionMethod, PeerSet
from equitydesk.domain.provenance import FutureLeakageError
from equitydesk.domain.report import Citation
from equitydesk.domain.research import ResearchStatus
from equitydesk.domain.snapshot import ResearchSnapshot
from equitydesk.infrastructure.db.sqlite import open_database
from equitydesk.infrastructure.llm.fake import ScriptedLLMProvider
from equitydesk.infrastructure.market_data.static import StaticMarketData
from equitydesk.reporting.composer import compose_report
from equitydesk.reporting.reviewer import review_report

pytestmark = pytest.mark.e2e

AS_OF = f.dt(2024, 1, 15)
FUTURE = f.dt(2024, 2, 1)
GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "reproducibility" / "frozen_run.json"


# --- leakage vectors ------------------------------------------------------------------------


def doc_published_after_as_of(tmp: Path) -> None:
    repos = open_database(tmp / "a.db")
    repos.companies.save_company(f.company())
    repos.documents.add_document(f.document())
    repos.documents.add_document(f.document(document_id="future", official_id="x", published_at=FUTURE,
                                            retrieved_at=FUTURE, content_hash="b" * 64))  # fmt: skip
    run = f.run(as_of=AS_OF, created_at=f.dt(2024, 6, 1))
    repos.runs.add_run(run)
    snap = SnapshotBuilder(repos.documents, repos.snapshots).build(run)
    assert snap.document_ids == ("doc_10k_2023",) and snap.excluded_future_ids == ("future",)


def corrected_version_published_later(tmp: Path) -> None:
    repos = open_database(tmp / "b.db")
    repos.companies.save_company(f.company())
    v1 = f.document()
    v2 = f.document(document_id="doc_v2", official_id="v2", version=2, supersedes_document_id=v1.document_id,
                    published_at=FUTURE, retrieved_at=FUTURE, content_hash="c" * 64)  # fmt: skip
    repos.documents.add_document(v1)
    repos.documents.add_document(v2)
    run = f.run(as_of=AS_OF, created_at=f.dt(2024, 6, 1))
    repos.runs.add_run(run)
    assert SnapshotBuilder(repos.documents, repos.snapshots).build(run).document_ids == (
        "doc_10k_2023",
    )


def restated_fact_filed_later(_: Path) -> None:
    def raw(value: float, accn: str, avail) -> RawReportedFact:
        return RawReportedFact(company_id="co_x", taxonomy="us-gaap", tag="RevenueFromContractWithCustomerExcludingAssessedTax",
                               unit="USD", value=value, period_start=date(2022, 10, 1), period_end=date(2023, 9, 30),
                               accession=accn, form="10-K", filed=avail.date(), available_at=avail)  # fmt: skip

    facts = map_reported_facts(
        "co_x", [raw(100.0, "a1", f.dt(2023, 11, 3)), raw(90.0, "a2", FUTURE)], AS_OF
    ).facts
    assert [x.value for x in facts if x.concept == "Revenue"] == [100.0]


def facts_api_after_as_of(tmp: Path) -> None:
    container = build_container(Settings(), db_path=tmp / "c.db")
    container.research.clock = lambda: f.dt(2024, 6, 1)
    client = TestClient(create_app(container.research))
    rid = client.post("/research-runs", json={"query": "AAPL", "as_of": AS_OF.isoformat()}).json()[
        "research_run_id"
    ]
    container.repos.facts.add_facts([f.fact(fact_id="now"), f.fact(fact_id="later", period_end=date(2024, 9, 28),
                                     period_start=date(2023, 10, 1), fiscal_year=2024, available_at=FUTURE)])  # fmt: skip
    assert [
        x["fact_id"] for x in client.get(f"/research-runs/{rid}/financials").json()["facts"]
    ] == ["now"]


def later_price(_: Path) -> None:
    obs = [PRICES[0], PRICES[1]]
    assert observation_as_of(obs, "sec_aapl_xnas", AS_OF).price == 185.0
    with pytest.raises(FutureLeakageError):
        observation_as_of([PRICES[1]], "sec_aapl_xnas", AS_OF)


def later_peer_price(_: Path) -> None:
    end = date(2023, 9, 30)
    target = CompanyFundamentals("t", "sec_t", end, 100.0, 20.0, 10.0, 8.0, 0.0, 0.0, 10.0)
    peers = [
        CompanyFundamentals(c, f"sec_{c}", end, 100.0, 20.0, 10.0, 8.0, 0.0, 0.0)
        for c in ("a", "b")
    ]

    def ob(sec_id: str, price: float, when) -> MarketObservation:
        return MarketObservation(security_id=sec_id, price=price, currency="USD", observed_at=when,
                                 price_source="manual", shares_outstanding=10.0, shares_source="cover",
                                 shares_as_of=date(2023, 10, 1))  # fmt: skip

    obs = [
        ob("sec_t", 10.0, AS_OF),
        ob("sec_a", 10.0, AS_OF),
        ob("sec_b", 10.0, AS_OF),
        ob("sec_a", 1000.0, FUTURE),
    ]
    result = multiples_valuation(target, peers, obs, AS_OF, "x")
    assert {r.company_id: r.observation.price for r in result.peers} == {"a": 10.0, "b": 10.0}
    with pytest.raises(FutureLeakageError):
        multiples_valuation(
            target,
            peers,
            [ob("sec_t", 10.0, AS_OF), ob("sec_a", 10.0, FUTURE), ob("sec_b", 1.0, AS_OF)],
            AS_OF,
            "x",
        )


def peer_set_version_effective_later(tmp: Path) -> None:
    repos = open_database(tmp / "d.db")
    for cid in ("co_asml", "co_amat", "co_lrcx", "co_klac"):
        repos.companies.save_company(f.company(company_id=cid, name=cid, cik=None))

    def ps(version: int, effective, members: tuple[str, ...]) -> PeerSet:
        return PeerSet(name="semicap", version=version, target_company_id="co_asml",
                       members=tuple(PeerMember(company_id=m, rationale="wfe") for m in members),
                       selection_method=PeerSelectionMethod.USER_DEFINED, created_by="user:a",
                       created_at=effective, effective_from=effective)  # fmt: skip

    repos.peer_sets.add_peer_set(ps(1, f.dt(2023, 1, 1), ("co_amat", "co_lrcx")))
    repos.peer_sets.add_peer_set(ps(2, FUTURE, ("co_amat", "co_lrcx", "co_klac")))
    current = repos.peer_sets.effective_as_of("semicap", AS_OF)
    assert current is not None and current.version == 1


def finding_citing_future_document(_: Path) -> None:
    future_doc = rf.Q2.model_copy(update={"published_at": FUTURE, "retrieved_at": FUTURE})
    finding = rf.findings()[3]  # cites doc_10q_2024q2
    snap = ResearchSnapshot(snapshot_id="s", research_run_id="run_1", company_id="co_apple", as_of=AS_OF,
                            document_ids=("doc_10k_2023", "doc_10q_2024q2"))  # fmt: skip
    gate = EvidenceQualityGate().validate(
        "run_1", AS_OF, snap, [finding], {d.document_id: d for d in (rf.DOC, future_doc)}, {}
    )
    assert gate.accepted == [] and finding.finding_id in {x.finding_id for x in gate.rejected}


def report_composed_with_future_document(_: Path) -> None:
    data = rf.inputs() | {
        "documents": [rf.DOC, rf.Q2.model_copy(update={"published_at": f.dt(2024, 7, 1)})]
    }
    with pytest.raises(FutureLeakageError):
        compose_report(**data)


def report_citation_after_as_of(_: Path) -> None:
    report = compose_report(**rf.inputs())
    cit = report.citations[0]
    bad = Citation(
        **(cit.model_dump() | {"published_at": f.dt(2024, 7, 1), "retrieved_at": f.dt(2024, 7, 2)})
    )
    with pytest.raises(ValueError, match="after as_of"):
        report.model_copy(update={"citations": (bad, *report.citations[1:])}).model_validate(
            report.model_copy(update={"citations": (bad, *report.citations[1:])}).model_dump()
        )


def reviewer_flags_stored_document_after_as_of(_: Path) -> None:
    data = rf.inputs()
    report = compose_report(**data)
    moved = [rf.DOC, rf.Q2.model_copy(update={"published_at": f.dt(2024, 7, 1)})]
    result = review_report(report, moved, data["metrics"], data["valuations"])
    assert "future_source" in {x.code for x in result.blocking}


def run_as_of_after_creation(_: Path) -> None:
    with pytest.raises(ValueError, match="as_of cannot be later"):
        f.run(as_of=f.dt(2024, 7, 1), created_at=f.dt(2024, 6, 1))


def sec_filing_index_after_as_of(_: Path) -> None:
    refs = sec().list_filings(320193, forms={"10-K", "10-K/A", "10-Q"}, as_of=AS_OF)
    assert refs and all(r.accepted_at <= AS_OF for r in refs)
    assert "0000320193-24-000050" not in {r.accession_number for r in refs}  # 10-K/A filed 2024-03


def collected_document_after_as_of(tmp: Path) -> None:
    from equitydesk.application.workflow import Collected

    container = build_container(Settings(data_dir=tmp / "data"), db_path=tmp / "e.db")
    container.research.clock = lambda: f.dt(2024, 1, 16)
    wf = build_workflow(container, StaticMarketData(PRICES), sec(), ScriptedLLMProvider())

    class Leaky:
        def collect(self, company, as_of):
            return Collected(
                documents=[
                    (f.document(document_id="leak", published_at=FUTURE, retrieved_at=FUTURE), b"x")
                ]
            )

    wf.filings = Leaky()
    run = container.research.create_run("AAPL", AS_OF)
    assert wf.execute(run.research_run_id, INPUTS).run.status is ResearchStatus.FAILED
    assert container.repos.documents.documents_for_company("co_apple") == []


def earlier_run_unchanged_by_later_data(tmp: Path) -> None:
    container = build_container(Settings(), db_path=tmp / "f.db")
    container.research.clock = lambda: f.dt(2024, 6, 1)
    container.repos.companies.save_company(f.company())
    container.repos.documents.add_document(f.document())
    run = container.research.create_run("AAPL", AS_OF)
    before = container.research.run_state(run.research_run_id)
    container.repos.documents.add_document(f.document(document_id="new", official_id="n", published_at=FUTURE,
                                                      retrieved_at=FUTURE, content_hash="d" * 64))  # fmt: skip
    container.repos.facts.add_facts([f.fact(fact_id="late", available_at=FUTURE)])
    after = container.research.run_state(run.research_run_id)
    assert [d.document_id for d in after.documents] == [d.document_id for d in before.documents]
    assert after.metrics == before.metrics


LEAKAGE_MATRIX: dict[str, Callable[[Path], None]] = {
    "documents/published-after-as_of": doc_published_after_as_of,
    "documents/correction-published-later": corrected_version_published_later,
    "documents/sec-index-after-as_of": sec_filing_index_after_as_of,
    "documents/source-returns-future-document": collected_document_after_as_of,
    "financials/restatement-filed-later": restated_fact_filed_later,
    "financials/api-fact-available-later": facts_api_after_as_of,
    "market/later-price": later_price,
    "peers/later-peer-price": later_peer_price,
    "peers/peer-set-version-effective-later": peer_set_version_effective_later,
    "findings/evidence-from-future-document": finding_citing_future_document,
    "report/compose-with-future-document": report_composed_with_future_document,
    "report/citation-after-as_of": report_citation_after_as_of,
    "review/stored-document-moved-after-as_of": reviewer_flags_stored_document_after_as_of,
    "runs/as_of-after-creation": run_as_of_after_creation,
    "history/earlier-run-unchanged-by-later-data": earlier_run_unchanged_by_later_data,
}


@pytest.mark.parametrize("vector", sorted(LEAKAGE_MATRIX))
def test_leakage_matrix(vector: str, tmp_path: Path) -> None:
    LEAKAGE_MATRIX[vector](tmp_path)


# --- frozen-input reproducibility -----------------------------------------------------------


def frozen_run(env) -> dict[str, Any]:
    """Execute the frozen run and reduce it to run-id-independent, comparable values."""
    container, make = env
    workflow, _ = make()
    run = container.research.create_run("AAPL", AS_OF)
    outcome = workflow.execute(run.research_run_id, INPUTS)
    rid = run.research_run_id
    report = container.repos.reports.reports_for_run(rid)[-1]
    report_json = report.model_dump_json(exclude={"report_id", "research_run_id", "generated_at"})
    for v in container.repos.valuations.valuations_for_run(rid):
        report_json = report_json.replace(v.valuation_id, f"<{v.method.value}>")
    # finding ids are derived from the (random) run id; replace them by their content
    for x in container.repos.findings.findings_for_run(rid):
        report_json = report_json.replace(x.finding_id, f"<{x.category.value}:{x.statement}>")
    vals = {v.method.value: v.outputs for v in container.repos.valuations.valuations_for_run(rid)}
    return {
        "status": outcome.run.status.value,
        "snapshot_id": outcome.run.snapshot_id,
        "documents": list(container.repos.snapshots.member_ids(rid)),
        "fact_ids_sha256": hashlib.sha256(
            "\n".join(
                sorted(x.fact_id for x in container.repos.facts.facts_for_company("co_apple"))
            ).encode()
        ).hexdigest(),
        "dcf_value_per_share": round(vals["dcf"]["value_per_share"], 6),
        "scenario_probability_weighted": round(vals["scenario"]["probability_weighted_value"], 6),
        "report_sha256": hashlib.sha256(report_json.encode()).hexdigest(),
        "review_passed": container.repos.reports.latest_review(rid).passed,
    }


def test_frozen_inputs_reproduce_golden_values(env) -> None:
    actual = frozen_run(env)
    if os.environ.get("EQUITYDESK_REGENERATE_GOLDEN") == "1":
        payload = {"description": "Golden values of the frozen AAPL run (as_of 2024-01-15); see README.md.",
                   "expected": actual}  # fmt: skip
        GOLDEN.write_text(json.dumps(payload, indent=2, sort_keys=True) + chr(10), encoding="utf-8")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == golden["expected"], (
        "frozen run drifted from tests/fixtures/reproducibility/frozen_run.json; if the change is "
        "intended, regenerate per tests/fixtures/reproducibility/README.md"
    )
