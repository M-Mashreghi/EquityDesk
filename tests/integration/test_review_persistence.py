"""Reports and reviews persist (insert-only) and the store refuses to complete unreviewed runs."""

from __future__ import annotations

from pathlib import Path

import pytest

import factories as f
import report_fixtures as rf
from equitydesk.application.ports import ImmutableRecordError
from equitydesk.domain.report import SectionKey
from equitydesk.domain.research import ResearchStatus
from equitydesk.domain.review import ReviewGateError
from equitydesk.infrastructure.db.sqlite import open_database
from equitydesk.reporting.composer import compose_report
from equitydesk.reporting.reviewer import apply_review, review_report

pytestmark = pytest.mark.integration


@pytest.fixture
def setup(tmp_path: Path):
    repos = open_database(tmp_path / "db.sqlite")
    data = rf.inputs()
    repos.companies.save_company(data["company"])
    run = data["run"]
    repos.runs.add_run(run)
    for status in (ResearchStatus.COLLECTING, ResearchStatus.PARSING, ResearchStatus.ANALYZING,
                   ResearchStatus.VALUING, ResearchStatus.REVIEWING):  # fmt: skip
        run = run.transition(status, at=f.T0)
        repos.runs.update_run(run)
    report = compose_report(**data)
    repos.reports.add_report(report)
    return repos, data, run, report


def _review(report, data):
    return review_report(report, data["documents"], data["metrics"], data["valuations"])


def test_report_and_review_round_trip(setup) -> None:
    repos, data, run, report = setup
    assert repos.reports.get_report(report.report_id) == report
    repos.reports.add_report(report)  # identical re-insert is a no-op
    result = _review(report, data)
    repos.reports.add_review(result)
    assert repos.reports.latest_review(run.research_run_id) == result
    stored = repos.reports.review_findings(result.review_id)
    assert {x.review_finding_id for x in stored} == {x.review_finding_id for x in result.findings}
    with pytest.raises(ImmutableRecordError):
        repos.reports.add_review(result.model_copy(update={"reviewer_version": "other"}))


def test_store_refuses_completion_without_passing_review(setup) -> None:
    repos, data, run, report = setup
    with pytest.raises(ReviewGateError):  # no review at all
        repos.runs.update_run(run.transition(ResearchStatus.COMPLETED, at=f.T0))

    broken = report.model_copy(update={"sections": tuple(
        s.model_copy(update={"claims": ()}) if s.key is SectionKey.VALUATION else s for s in report.sections
    ), "report_id": "rpt_broken"})  # fmt: skip
    repos.reports.add_report(broken)
    blocked = _review(broken, data)
    repos.reports.add_review(blocked)
    assert not blocked.passed
    with pytest.raises(ReviewGateError):  # bypassing apply_review is still refused
        repos.runs.update_run(run.transition(ResearchStatus.COMPLETED, at=f.T0))
    needs_input = apply_review(run, blocked, at=f.T0)
    repos.runs.update_run(needs_input)
    assert repos.runs.get_run(run.research_run_id).status is ResearchStatus.NEEDS_INPUT


def test_passing_review_allows_completion(setup) -> None:
    repos, data, run, report = setup
    result = _review(report, data)
    repos.reports.add_review(result)
    repos.runs.update_run(apply_review(run, result, at=f.T0))
    assert repos.runs.get_run(run.research_run_id).status is ResearchStatus.COMPLETED
