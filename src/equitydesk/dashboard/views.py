"""Presentation helpers: reshape API JSON into rows/series for display.

Display formatting only. Every number shown here was computed server-side; nothing is derived.
"""

from __future__ import annotations

from typing import Any

PIPELINE = ("Created", "Collecting", "Parsing", "Analyzing", "Valuing", "Reviewing", "Completed")
ATTENTION = {"NeedsInput", "BlockedBySource", "Failed"}


def run_progress(run: dict[str, Any]) -> tuple[float, str]:
    """(fraction of pipeline reached, label) for a run's lifecycle status."""
    status = run["status"]
    if status in PIPELINE:
        return PIPELINE.index(status) / (len(PIPELINE) - 1), status
    reached = [h["status"] for h in run.get("status_history", []) if h["status"] in PIPELINE]
    fraction = PIPELINE.index(reached[-1]) / (len(PIPELINE) - 1) if reached else 0.0
    reason = next(
        (h.get("reason") for h in reversed(run.get("status_history", [])) if h["status"] == status),
        None,
    )
    return fraction, f"{status}: {reason}" if reason else status


def document_rows(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "published": d["published_at"][:10],
            "type": d["document_type"],
            "source": d["source_type"],
            "title": d.get("title") or "",
            "official id": d.get("official_id") or "",
            "url": d.get("source_url") or "",
            "sha256": (d.get("content_hash") or "")[:12],
        }
        for d in sorted(documents, key=lambda d: d["published_at"], reverse=True)
    ]


def metric_series(
    financials: dict[str, Any], names: tuple[str, ...]
) -> dict[str, dict[str, float]]:
    """{metric: {period_end: value}} for metrics with status ok."""
    out: dict[str, dict[str, float]] = {n: {} for n in names}
    for m in financials.get("metrics", []):
        if m["name"] in out and m["status"] == "ok":
            out[m["name"]][m["period_end"]] = m["value"]
    return {k: dict(sorted(v.items())) for k, v in out.items() if v}


def metric_rows(financials: dict[str, Any]) -> list[dict[str, Any]]:
    """Latest-period metric table with formula, status and missing inputs."""
    latest: dict[str, dict[str, Any]] = {}
    for m in financials.get("metrics", []):
        if m["name"] not in latest or m["period_end"] > latest[m["name"]]["period_end"]:
            latest[m["name"]] = m
    return [
        {"metric": n, "period": m["period_end"], "value": m["value"], "unit": m["unit"],
         "status": m["status"], "formula": m["formula"], "missing": ", ".join(m.get("missing", []))}
        for n, m in sorted(latest.items())
    ]  # fmt: skip


def valuation_rows(valuations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for v in valuations:
        if v["method"] == "multiples":
            for multiple, out in sorted(v["outputs"].items()):
                rows.append({"method": f"multiples:{multiple}", "low": out["low"], "mid": out["mid"],
                             "high": out["high"], "detail": f"{out['peer_n']} peers", "valuation_id": v["valuation_id"]})  # fmt: skip
        elif v["method"] == "dcf":
            o = v["outputs"]
            rows.append({"method": "dcf", "low": None, "mid": o["value_per_share"], "high": None,
                         "detail": f"TV {o['terminal_share_of_ev']:.0%} of EV", "valuation_id": v["valuation_id"]})  # fmt: skip
    return rows


def scenario_rows(valuations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for v in valuations:
        if v["method"] != "scenario":
            continue
        for name in ("bull", "base", "bear"):
            if name not in v["outputs"]:
                continue
            values = {x["name"]: x for x in v["outputs"][name]["values"]}
            rows.append({
                "scenario": name,
                "probability": v["assumptions"][name].get("probability"),
                "narrative": v["assumptions"][name].get("narrative"),
                "revenue CAGR": values["revenue_cagr"]["value"],
                "value / share": values["value_per_share"]["value"],
                "upside": v["outputs"][name].get("upside"),
            })  # fmt: skip
    return rows


def scenario_assumptions(valuation: dict[str, Any], scenario: str) -> list[dict[str, Any]]:
    """Every labeled value of one scenario (fact / assumption / calculation) with provenance."""
    return [
        {
            "name": x["name"],
            "value": x["value"],
            "kind": x["kind"],
            "unit": x["unit"],
            "provenance": x["provenance"],
        }
        for x in valuation["outputs"][scenario]["values"]
    ]


def review_summary(review: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not review:
        return []
    order = {"blocking": 0, "warning": 1, "info": 2}
    return [
        {
            "severity": f["severity"],
            "code": f["code"],
            "message": f["message"],
            "origin": f["origin"],
        }
        for f in sorted(review["findings"], key=lambda f: order.get(f["severity"], 3))
    ]
