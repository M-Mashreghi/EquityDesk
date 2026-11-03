"""Markdown rendering of a `report.v1` report with numbered citations and calculation refs."""

from __future__ import annotations

from equitydesk.domain.report import ReportClaim, ResearchReport
from equitydesk.domain.research import ClaimType, SupportStatus

LABELS = {
    ClaimType.FACT: "Fact",
    ClaimType.INFERENCE: "Inference",
    ClaimType.ASSUMPTION: "Assumption",
    ClaimType.UNKNOWN: "Unknown",
}


def _refs(claim: ReportClaim, numbers: dict[str, int]) -> str:
    parts = [f"[{numbers[c]}]" for c in claim.citation_ids]
    parts += [f"`{m}`" for m in claim.metric_ids]
    parts += [f"`{v}`" for v in claim.valuation_ids]
    return " " + " ".join(parts) if parts else ""


def render_markdown(report: ResearchReport) -> str:
    numbers = {c.citation_id: i for i, c in enumerate(report.citations, 1)}
    lines = [
        f"# {report.company_name} — Research Report",
        "",
        f"- As of: {report.as_of.isoformat()}",
        f"- Research run: `{report.research_run_id}` · snapshot: `{report.snapshot_id or '-'}`",
        f"- Report: `{report.report_id}` ({report.schema_version}, composer {report.composer_version})",
        "",
        "Labels: **Fact** (source or deterministic calculation), **Inference** (interpretation of "
        "cited facts), **Assumption** (explicit scenario input), **Unknown** (not supported).",
        "",
    ]
    for section in report.sections:
        lines += [f"## {section.title}", ""]
        for claim in section.claims:
            flag = (
                ""
                if claim.support_status is SupportStatus.SUPPORTED
                else f" _({claim.support_status.value})_"
            )
            lines.append(
                f"- **{LABELS[claim.claim_type]}**{flag}: {claim.text}{_refs(claim, numbers)}"
            )
        for note in section.notes:
            lines.append(f"- _Note: {note}_")
        lines.append("")
    lines += ["## Sources", ""]
    for c in report.citations:
        locator = c.official_id or c.source_url or c.document_id
        where = ", ".join(
            x for x in (c.section, f"p. {c.page}" if c.page else None, c.chunk_id) if x
        )
        lines.append(
            f"{numbers[c.citation_id]}. {c.title or c.document_type} ({c.source_type.value}, "
            f'published {c.published_at.date().isoformat()}, {locator}; {where}): "{c.quote}"'
        )
    return "\n".join(lines).rstrip() + "\n"
