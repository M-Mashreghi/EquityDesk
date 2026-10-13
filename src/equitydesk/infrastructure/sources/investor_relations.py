"""Issuer investor-relations adapter: official earnings releases / investor materials.

IR websites rarely expose machine-readable publication timestamps, so documents enter through
explicit registration (URL + publication time supplied by a user or a curated list). Only URLs
on the issuer's approved official domains are accepted as primary sources by default; anything
else is rejected, or - when explicitly allowed - downgraded to a `secondary` source so that it
can never silently override primary material.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from equitydesk.domain.common import UtcDateTime, utc_now
from equitydesk.domain.documents import SourceDocument, SourceType
from equitydesk.domain.provenance import compute_content_hash
from equitydesk.infrastructure.sources.http import HttpFetcher

DEFAULT_IR_CONFIG_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "fixtures" / "ir_sources.json"
)


class UnapprovedSourceError(ValueError):
    pass


class IRUrlPattern(BaseModel):
    model_config = ConfigDict(frozen=True)

    regex: str
    document_type: str
    source_type: SourceType = SourceType.ISSUER_IR


class IssuerIRConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    company_id: str
    approved_domains: tuple[str, ...] = Field(min_length=1)
    patterns: tuple[IRUrlPattern, ...] = ()


class ManualSourceRegistration(BaseModel):
    model_config = ConfigDict(frozen=True)

    company_id: str
    url: str
    published_at: UtcDateTime
    document_type: str | None = None
    title: str = ""


def load_ir_configs(path: Path = DEFAULT_IR_CONFIG_PATH) -> dict[str, IssuerIRConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    configs = [IssuerIRConfig.model_validate(item) for item in raw["issuers"]]
    return {c.company_id: c for c in configs}


def host_is_approved(host: str, approved_domains: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in approved_domains)


class IssuerIRAdapter:
    def __init__(
        self,
        fetcher: HttpFetcher,
        configs: dict[str, IssuerIRConfig],
        clock: Callable[[], datetime] = utc_now,
        allow_unapproved: bool = False,
    ) -> None:
        self.fetcher = fetcher
        self.configs = configs
        self.clock = clock
        self.allow_unapproved = allow_unapproved

    def classify(
        self, company_id: str, url: str
    ) -> tuple[bool, IRUrlPattern | None, re.Match[str] | None]:
        """Return (is_on_approved_domain, matching pattern, regex match)."""
        config = self.configs.get(company_id)
        parts = urlsplit(url)
        if (
            config is None
            or parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or not parts.hostname
        ):
            return False, None, None
        if not host_is_approved(parts.hostname, config.approved_domains):
            return False, None, None
        for pattern in config.patterns:
            match = re.match(pattern.regex, url)
            if match:
                return True, pattern, match
        return True, None, None

    def register(self, registration: ManualSourceRegistration) -> tuple[SourceDocument, bytes]:
        approved, pattern, match = self.classify(registration.company_id, registration.url)
        if not approved and not self.allow_unapproved:
            raise UnapprovedSourceError(
                f"{registration.url} is not on an approved official domain for "
                f"{registration.company_id}"
            )
        document_type = registration.document_type or (pattern.document_type if pattern else None)
        if document_type is None:
            raise ValueError("document_type is required when the URL matches no known pattern")
        if match is not None:
            _check_date_consistency(match, registration.published_at)

        result = self.fetcher.get(registration.url)
        final_host = urlsplit(result.url).hostname or ""
        config = self.configs.get(registration.company_id)
        if approved and config and not host_is_approved(final_host, config.approved_domains):
            raise UnapprovedSourceError(f"redirected off approved domains to {final_host}")

        source_type = (
            (pattern.source_type if pattern else SourceType.ISSUER_IR)
            if approved
            else SourceType.SECONDARY
        )
        url_key = hashlib.sha256(registration.url.encode()).hexdigest()[:16]
        content_hash = compute_content_hash(result.content)
        doc = SourceDocument(
            document_id=f"ir_{url_key}_{content_hash[:12]}",
            company_id=registration.company_id,
            source_type=source_type,
            document_type=document_type,
            title=registration.title,
            source_url=registration.url,
            published_at=registration.published_at,
            retrieved_at=max(self.clock(), registration.published_at),
            content_hash=content_hash,
            mime_type=(result.content_type or "text/html").split(";")[0].strip(),
        )
        return doc, result.content


def _check_date_consistency(match: re.Match[str], published_at: datetime) -> None:
    """A URL dated YYYY/MM cannot have been published before that month began."""
    groups = match.groupdict()
    if "year" not in groups or groups["year"] is None:
        return
    year = int(groups["year"])
    month = int(groups.get("month") or 1)
    # Fiscal-year folders (e.g. fy2024-q4) may be published in the following calendar year;
    # only a publication *before* the folder's start (minus one year for fiscal folders) is
    # inconsistent.
    earliest = datetime(year if "month" in groups else year - 1, month, 1, tzinfo=UTC)
    if published_at < earliest:
        raise ValueError(
            f"published_at {published_at.isoformat()} precedes the date encoded in the URL"
        )
