"""Research history: what changed between two point-in-time runs of the same company.

Each side is read strictly as of its own run (snapshot, facts available at `as_of`, the run's own
findings / valuations / report), so a diff never mixes information across dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from equitydesk.domain.documents import SourceDocument
from equitydesk.domain.metrics import MetricResult, MetricStatus
from equitydesk.domain.report import ResearchReport, SectionKey
from equitydesk.domain.research import FindingCategory, ResearchFinding, ResearchRun
from equitydesk.domain.valuation import ValuationMethod, ValuationRun

GUIDANCE_CATEGORIES = (
    FindingCategory.GUIDANCE_CHANGE,
    FindingCategory.STRATEGY_CHANGE,
    FindingCategory.MANAGEMENT_TONE,
)


@dataclass(frozen=True)
class RunState:
    run: ResearchRun
    documents: list[SourceDocument]
    metrics: list[MetricResult]
    findings: list[ResearchFinding]
    valuations: list[ValuationRun]
    report: ResearchReport | None


@dataclass(frozen=True)
class ValueChange:
    key: str
    old: float | None
    new: float | None
    old_period: str | None = None
    new_period: str | None = None

    @property
    def delta(self) -> float | None:
        return self.new - self.old if self.old is not None and self.new is not None else None

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "old": self.old, "new": self.new, "delta": self.delta,
                "old_period": self.old_period, "new_period": self.new_period}  # fmt: skip


@dataclass(frozen=True)
class SetChange:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


@dataclass(frozen=True)
class RunDiff:
    company_id: str
    old_run_id: str
    new_run_id: str
    old_as_of: datetime
    new_as_of: datetime
    sources: SetChange
    kpis: list[ValueChange]
    guidance: SetChange
    risks: SetChange
    catalysts: SetChange
    thesis: SetChange
    valuation: list[ValueChange]
    assumptions_changed: list[str]

    def as_dict(self) -> dict[str, Any]:
        sets = {k: {"added": v.added, "removed": v.removed} for k, v in
                (("sources", self.sources), ("guidance", self.guidance), ("risks", self.risks),
                 ("catalysts", self.catalysts), ("thesis", self.thesis))}  # fmt: skip
        return {
            "company_id": self.company_id, "old_run_id": self.old_run_id, "new_run_id": self.new_run_id,
            "old_as_of": self.old_as_of.isoformat(), "new_as_of": self.new_as_of.isoformat(),
            **sets,
            "kpis": [c.as_dict() for c in self.kpis],
            "valuation": [c.as_dict() for c in self.valuation],
            "assumptions_changed": self.assumptions_changed,
        }  # fmt: skip


def _set_change(old: set[str], new: set[str]) -> SetChange:
    return SetChange(sorted(new - old), sorted(old - new))


def _doc_label(d: SourceDocument) -> str:
    return f"{d.published_at.date().isoformat()} {d.document_type} {d.title or d.document_id} [{d.document_id}]"


def _latest_metrics(metrics: list[MetricResult]) -> dict[str, MetricResult]:
    latest: dict[str, MetricResult] = {}
    for m in metrics:
        if m.status is MetricStatus.OK and (
            m.name not in latest or m.period_end > latest[m.name].period_end
        ):
            latest[m.name] = m
    return latest


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _finding_keys(
    findings: list[ResearchFinding], categories: tuple[FindingCategory, ...]
) -> dict[str, str]:
    """normalized key -> display text; risks/catalysts keyed by title when present."""
    out: dict[str, str] = {}
    for f in findings:
        if f.category in categories:
            text = str(f.attributes.get("title") or f.statement)
            out[_norm(text)] = text
    return out


def _finding_change(old: list[ResearchFinding], new: list[ResearchFinding],
                    categories: tuple[FindingCategory, ...]) -> SetChange:  # fmt: skip
    a, b = _finding_keys(old, categories), _finding_keys(new, categories)
    return SetChange(
        sorted(b[k] for k in b.keys() - a.keys()), sorted(a[k] for k in a.keys() - b.keys())
    )


def valuation_points(valuations: list[ValuationRun]) -> dict[str, float]:
    points: dict[str, float] = {}
    for v in valuations:
        if v.method is ValuationMethod.MULTIPLES:
            for m, out in v.outputs.items():
                points[f"multiples:{m}:mid_per_share"] = out["mid"]
        elif v.method is ValuationMethod.DCF:
            points["dcf:value_per_share"] = v.outputs["value_per_share"]
        elif v.method is ValuationMethod.SCENARIO:
            for name in ("bull", "base", "bear"):
                if name in v.outputs:
                    vals = {x["name"]: x["value"] for x in v.outputs[name]["values"]}
                    points[f"scenario:{name}:value_per_share"] = vals["value_per_share"]
            if v.outputs.get("probability_weighted_value") is not None:
                points["scenario:probability_weighted"] = v.outputs["probability_weighted_value"]
    return points


def _assumption_hashes(valuations: list[ValuationRun]) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in valuations:
        if v.method is ValuationMethod.DCF:
            out["dcf"] = v.assumptions.get("assumptions_hash", "")
        elif v.method is ValuationMethod.SCENARIO:
            for name, a in v.assumptions.items():
                out[f"scenario:{name}"] = a.get("assumptions_hash", "")
    return out


def _thesis(report: ResearchReport | None) -> set[str]:
    if report is None:
        return set()
    return {c.text for c in report.section(SectionKey.INVESTMENT_THESIS).claims}


def diff_runs(a: RunState, b: RunState) -> RunDiff:
    """Diff two runs of one company; the earlier `as_of` is always the 'old' side."""
    if a.run.company_id != b.run.company_id:
        raise ValueError("can only diff runs of the same company")
    old, new = (
        (a, b) if (a.run.as_of, a.run.created_at) <= (b.run.as_of, b.run.created_at) else (b, a)
    )

    docs_old = {_doc_label(d) for d in old.documents}
    docs_new = {_doc_label(d) for d in new.documents}

    m_old, m_new = _latest_metrics(old.metrics), _latest_metrics(new.metrics)
    kpis = []
    for name in sorted(m_old.keys() | m_new.keys()):
        o, n = m_old.get(name), m_new.get(name)
        if o is not None and n is not None and o.period_end == n.period_end and o.value == n.value:
            continue
        kpis.append(ValueChange(name, o.value if o else None, n.value if n else None,
                                o.period_end.isoformat() if o else None, n.period_end.isoformat() if n else None))  # fmt: skip

    v_old, v_new = valuation_points(old.valuations), valuation_points(new.valuations)
    valuation = [ValueChange(k, v_old.get(k), v_new.get(k)) for k in sorted(v_old.keys() | v_new.keys())
                 if v_old.get(k) != v_new.get(k)]  # fmt: skip
    h_old, h_new = _assumption_hashes(old.valuations), _assumption_hashes(new.valuations)
    assumptions_changed = sorted(
        k for k in h_old.keys() | h_new.keys() if h_old.get(k) != h_new.get(k)
    )

    return RunDiff(
        company_id=new.run.company_id,
        old_run_id=old.run.research_run_id, new_run_id=new.run.research_run_id,
        old_as_of=old.run.as_of, new_as_of=new.run.as_of,
        sources=_set_change(docs_old, docs_new),
        kpis=kpis,
        guidance=_finding_change(old.findings, new.findings, GUIDANCE_CATEGORIES),
        risks=_finding_change(old.findings, new.findings, (FindingCategory.RISK,)),
        catalysts=_finding_change(old.findings, new.findings, (FindingCategory.CATALYST,)),
        thesis=_set_change(_thesis(old.report), _thesis(new.report)),
        valuation=valuation,
        assumptions_changed=assumptions_changed,
    )  # fmt: skip
