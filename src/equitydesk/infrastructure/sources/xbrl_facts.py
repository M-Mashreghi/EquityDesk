"""Parse SEC XBRL company-facts JSON into `RawReportedFact` objects."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from equitydesk.domain.financials import RawReportedFact
from equitydesk.infrastructure.sources.sec_edgar import availability_time


def parse_company_facts(
    data: Mapping[str, Any],
    company_id: str,
    accepted_at: Mapping[str, datetime] | None = None,
    taxonomies: tuple[str, ...] = ("us-gaap", "dei"),
) -> list[RawReportedFact]:
    """Flatten company facts.

    `accepted_at` maps accession -> conservative availability time (from the submissions feed).
    Without it, a fact is treated as available at the end of its filing day (New York).
    """
    accepted_at = accepted_at or {}
    facts: list[RawReportedFact] = []
    for taxonomy in taxonomies:
        for tag, body in (data.get("facts", {}).get(taxonomy) or {}).items():
            for unit, entries in (body.get("units") or {}).items():
                for e in entries:
                    filed = date.fromisoformat(e["filed"])
                    accn = e["accn"]
                    available = accepted_at.get(accn) or availability_time(None, filed)
                    facts.append(
                        RawReportedFact(
                            company_id=company_id,
                            taxonomy=taxonomy,
                            tag=tag,
                            unit=unit,
                            value=float(e["val"]),
                            period_start=date.fromisoformat(e["start"]) if e.get("start") else None,
                            period_end=date.fromisoformat(e["end"]),
                            accession=accn,
                            form=str(e.get("form", "")),
                            filed=filed,
                            available_at=available,
                        )
                    )
    return facts
