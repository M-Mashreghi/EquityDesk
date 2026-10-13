"""SEC EDGAR regulatory-filing adapter (initial US-equity scope).

Endpoints (public, no key; SEC fair-access policy requires a descriptive User-Agent and
<= 10 requests/second):
- submissions: https://data.sec.gov/submissions/CIK##########.json
- archives:    https://www.sec.gov/Archives/edgar/data/{cik}/{accession-no-dashes}/{document}

Availability time: the submissions feed's `acceptanceDateTime` carries a `Z` suffix but is
widely observed to be US/Eastern wall-clock time. We interpret it as America/New_York, which
is never earlier than the true time under either reading, and never earlier than the start of
the official filing date. This is the conservative choice for point-in-time research.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from equitydesk.domain.common import UtcDateTime, utc_now
from equitydesk.domain.documents import SourceDocument, SourceType
from equitydesk.domain.provenance import compute_content_hash
from equitydesk.infrastructure.sources.http import HttpFetcher

NEW_YORK = ZoneInfo("America/New_York")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{document}"

# Forms collected by default for fundamental research.
DEFAULT_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "20-F", "40-F"})


class FilingRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: str
    accession_number: str
    form: str
    filing_date: date
    accepted_at: UtcDateTime
    report_date: date | None
    primary_document: str
    description: str = ""

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(
            cik_int=int(self.cik),
            acc_nodash=self.accession_number.replace("-", ""),
            document=self.primary_document,
        )

    @property
    def is_amendment(self) -> bool:
        return self.form.endswith("/A")

    @property
    def base_form(self) -> str:
        return self.form.removesuffix("/A")


def normalize_cik(cik: str | int) -> str:
    digits = str(cik).strip().upper().removeprefix("CIK").lstrip("0") or "0"
    if not digits.isdigit() or len(digits) > 10:
        raise ValueError(f"invalid CIK {cik!r}")
    return digits.zfill(10)


def availability_time(acceptance: str | None, filing_date: date) -> datetime:
    """Conservative public-availability timestamp (UTC) for an EDGAR filing."""
    start_of_filing_day = datetime.combine(filing_date, time(0, 0), tzinfo=NEW_YORK)
    if not acceptance:
        # Without an acceptance time assume end of the filing day.
        return datetime.combine(filing_date, time(23, 59, 59), tzinfo=NEW_YORK).astimezone(UTC)
    wall = datetime.fromisoformat(acceptance.replace("Z", "")).replace(tzinfo=None)
    accepted = wall.replace(tzinfo=NEW_YORK)
    return max(accepted, start_of_filing_day).astimezone(UTC)


def parse_filing_table(cik: str, table: dict[str, list[object]]) -> list[FilingRef]:
    n = len(table.get("accessionNumber", []))
    refs: list[FilingRef] = []
    for i in range(n):

        def col(name: str, i: int = i) -> str:
            values = table.get(name) or []
            value = values[i] if i < len(values) else ""
            return str(value) if value is not None else ""

        filing_date = date.fromisoformat(col("filingDate"))
        report = col("reportDate")
        refs.append(
            FilingRef(
                cik=cik,
                accession_number=col("accessionNumber"),
                form=col("form"),
                filing_date=filing_date,
                accepted_at=availability_time(col("acceptanceDateTime") or None, filing_date),
                report_date=date.fromisoformat(report) if report else None,
                primary_document=col("primaryDocument"),
                description=col("primaryDocDescription"),
            )
        )
    return refs


class SecEdgarAdapter:
    def __init__(self, fetcher: HttpFetcher, clock: Callable[[], datetime] = utc_now) -> None:
        self.fetcher = fetcher
        self.clock = clock

    def _json(self, url: str) -> dict[str, object]:
        data = json.loads(self.fetcher.get(url).content)
        if not isinstance(data, dict):
            raise ValueError(f"unexpected JSON payload from {url}")
        return data

    def list_filings(
        self,
        cik: str | int,
        forms: Iterable[str] | None = DEFAULT_FORMS,
        as_of: datetime | None = None,
        include_history: bool = False,
    ) -> list[FilingRef]:
        """Enumerate filings by type, excluding anything not public by `as_of`."""
        if as_of is not None and as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        norm = normalize_cik(cik)
        data = self._json(SUBMISSIONS_URL.format(cik=norm))
        filings = data.get("filings")
        if not isinstance(filings, dict):
            raise ValueError("submissions payload has no filings section")
        refs = parse_filing_table(norm, filings.get("recent") or {})
        if include_history:
            for page in filings.get("files") or []:
                page_data = self._json(SUBMISSIONS_PAGE_URL.format(name=page["name"]))
                refs.extend(parse_filing_table(norm, page_data))  # type: ignore[arg-type]
        wanted = set(forms) if forms is not None else None
        out = [
            r
            for r in refs
            if (wanted is None or r.form in wanted) and (as_of is None or r.accepted_at <= as_of)
        ]
        unique = {r.accession_number: r for r in out}
        return sorted(unique.values(), key=lambda r: (r.accepted_at, r.accession_number))

    def fetch_filing(self, ref: FilingRef, company_id: str) -> tuple[SourceDocument, bytes]:
        """Download the primary document and build its provenance record."""
        result = self.fetcher.get(ref.url)
        doc = SourceDocument(
            document_id=f"sec_{ref.accession_number}",
            company_id=company_id,
            source_type=SourceType.REGULATORY_FILING,
            document_type=ref.form,
            title=ref.description or f"{ref.form} {ref.report_date or ref.filing_date}",
            source_url=ref.url,
            official_id=ref.accession_number,
            published_at=ref.accepted_at,
            retrieved_at=max(self.clock(), ref.accepted_at),
            period_end=ref.report_date,
            content_hash=compute_content_hash(result.content),
            mime_type=(result.content_type or "text/html").split(";")[0].strip(),
        )
        return doc, result.content

    def company_facts(self, cik: str | int) -> dict[str, object]:
        """XBRL company facts (structured financial data) for the issuer."""
        return self._json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{normalize_cik(cik)}.json"
        )
