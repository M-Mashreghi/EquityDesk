from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

import factories as f
from equitydesk.analytics.peers import comparable_table, percentile, summary_stats
from equitydesk.application.ports import ImmutableRecordError
from equitydesk.domain.metrics import MetricResult, MetricStatus
from equitydesk.domain.peers import PeerMember, PeerSelectionMethod, PeerSet
from equitydesk.infrastructure.db.sqlite import open_database
from equitydesk.infrastructure.sources.security_master import JsonSecurityMaster

PEERS = ("co_amat", "co_lrcx", "co_klac")


def peer_set(**kw) -> PeerSet:
    data = dict(
        name="asml_semicap",
        version=1,
        target_company_id="co_asml",
        members=tuple(PeerMember(company_id=c, rationale="wafer-fab equipment") for c in PEERS),
        selection_method=PeerSelectionMethod.USER_DEFINED,
        created_by="user:analyst",
        created_at=f.dt(2024, 1, 10),
        effective_from=f.dt(2024, 1, 10),
    )
    data.update(kw)
    return PeerSet(**data)


def test_peer_set_validation() -> None:
    ps = peer_set()
    assert ps.peer_set_id == "asml_semicap:v1" and ps.member_ids == PEERS
    with pytest.raises(ValidationError, match="own peer"):
        peer_set(members=(PeerMember(company_id="co_asml", rationale="x"),))
    with pytest.raises(ValidationError, match="duplicate"):
        peer_set(members=(PeerMember(company_id="co_amat", rationale="x"),) * 2)
    with pytest.raises(ValidationError, match="model-selected"):
        peer_set(created_by="llm:auto-peers")
    with pytest.raises(ValidationError):
        peer_set(members=())


def test_versions_are_persisted_and_resolved_point_in_time() -> None:
    repos = open_database()
    JsonSecurityMaster().seed(repos.companies)
    v1 = peer_set()
    repos.peer_sets.add_peer_set(v1)
    repos.peer_sets.add_peer_set(v1)  # idempotent
    v2 = v1.next_version(
        (*v1.members, PeerMember(company_id="co_nvidia", rationale="AI capex exposure")),
        created_by="user:analyst",
        created_at=f.dt(2024, 7, 1),
    )
    repos.peer_sets.add_peer_set(v2)
    assert repos.peer_sets.effective_as_of("asml_semicap", f.dt(2024, 3, 1)) == v1
    assert repos.peer_sets.effective_as_of("asml_semicap", f.dt(2024, 8, 1)) == v2
    assert repos.peer_sets.effective_as_of("asml_semicap", f.dt(2023, 1, 1)) is None
    assert [p.version for p in repos.peer_sets.history("asml_semicap")] == [1, 2]
    with pytest.raises(ImmutableRecordError):
        repos.peer_sets.add_peer_set(peer_set(version=5))
    with pytest.raises(ImmutableRecordError):
        repos.peer_sets.add_peer_set(peer_set(notes="rewritten v1"))


def test_percentile_linear_interpolation() -> None:
    data = [1.0, 2.0, 3.0, 4.0]
    assert percentile(data, 0.5) == 2.5
    assert percentile(data, 0.25) == 1.75
    assert percentile(data, 0.75) == 3.25
    assert percentile([5.0], 0.25) == 5.0
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_summary_stats_exclude_missing_explicitly() -> None:
    s = summary_stats(
        "gross_margin", {"a": 0.40, "b": 0.50, "c": None, "d": 0.47, "e": float("nan")}
    )
    assert s.n == 3 and s.median == 0.47
    assert s.mean == pytest.approx((0.40 + 0.50 + 0.47) / 3)
    assert (s.minimum, s.maximum) == (0.40, 0.50)
    assert s.p25 == pytest.approx(0.435) and s.p75 == pytest.approx(0.485)
    assert s.excluded_missing == ("c", "e") and s.included == ("a", "b", "d")
    empty = summary_stats("x", {"a": None})
    assert empty.n == 0 and empty.median is None


def _m(company: str, name: str, value: float | None, end: date) -> MetricResult:
    status = MetricStatus.OK if value is not None else MetricStatus.MISSING_INPUT
    return MetricResult(
        metric_id=f"m:{company}:{name}:{end}", company_id=company, name=name, period_end=end,
        value=value, unit="ratio", formula="x", status=status,
        missing=() if value is not None else ("x",), calc_version="1",
    )  # fmt: skip


def test_comparable_table_target_excluded_from_stats() -> None:
    end = date(2023, 12, 31)
    results = {
        "co_asml": [
            _m("co_asml", "gross_margin", 0.51, end),
            _m("co_asml", "gross_margin", 0.50, date(2022, 12, 31)),
        ],
        "co_amat": [_m("co_amat", "gross_margin", 0.47, date(2023, 10, 29))],
        "co_lrcx": [_m("co_lrcx", "gross_margin", 0.46, date(2023, 6, 25))],
        "co_klac": [_m("co_klac", "gross_margin", None, date(2023, 6, 30))],
    }  # fmt: skip
    table = comparable_table(peer_set(), results, metrics=("gross_margin",))
    assert table.rows["co_asml"]["gross_margin"].value == 0.51  # latest year chosen
    stats = table.stats["gross_margin"]
    assert stats.included == ("co_amat", "co_lrcx") and stats.excluded_missing == ("co_klac",)
    assert stats.median == pytest.approx(0.465)
    assert table.target_vs_median("gross_margin") == pytest.approx(0.045)
    assert table.peer_set_id == "asml_semicap:v1"
    # LRCX fiscal year ends ~6 months before the target's: surfaced, not hidden
    assert any("co_lrcx" in n for n in table.notes)


def test_company_without_metrics_is_noted() -> None:
    table = comparable_table(peer_set(), {"co_asml": []}, metrics=("gross_margin",))
    assert table.stats["gross_margin"].n == 0
    assert any("co_amat: no deterministic metrics" in n for n in table.notes)
