"""Resolve free-form user input (ticker, CIK, ISIN, company name) to a canonical company/security.

Rules:
- A ticker is never assumed globally unique: several listings sharing a symbol are AMBIGUOUS
  unless an exchange (MIC) narrows them to one. `TICKER:MIC` / `MIC:TICKER` input is accepted.
- An issuer-level identifier (CIK, exact company name) resolves the company; if the company has
  several securities, the single flagged primary listing is chosen and the choice is recorded.
- Partial name matches are never auto-resolved; they are returned as candidates to confirm.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from equitydesk.domain.companies import Company, Security


class SecurityMaster(Protocol):
    def companies(self) -> list[Company]: ...
    def securities(self) -> list[Security]: ...


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class Candidate:
    company: Company
    security: Security | None
    matched_on: str


@dataclass(frozen=True)
class ResolutionResult:
    status: ResolutionStatus
    query: str
    company: Company | None = None
    security: Security | None = None
    matched_on: str | None = None
    candidates: tuple[Candidate, ...] = ()
    suggestions: tuple[str, ...] = ()
    message: str = ""
    notes: tuple[str, ...] = field(default=())


_CIK = re.compile(r"^(?:CIK)?0*(\d{1,10})$", re.IGNORECASE)
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_MIC = re.compile(r"^X[A-Z]{3}$")
_SUFFIXES = re.compile(
    r"\b(incorporated|inc|corporation|corp|co|company|ltd|limited|plc|n\.?v|s\.?a|ag|se|"
    r"holdings?|group)\b\.?",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    text = _SUFFIXES.sub(" ", name.casefold())
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


class SecurityResolver:
    def __init__(self, master: SecurityMaster) -> None:
        self._companies = {c.company_id: c for c in master.companies()}
        self._securities = master.securities()

    def resolve(
        self, query: str, exchange: str | None = None, country: str | None = None
    ) -> ResolutionResult:
        text = (query or "").strip()
        if not text or len(text) > 200:
            return ResolutionResult(
                ResolutionStatus.INVALID_INPUT, query, message="query must be 1-200 characters"
            )
        exchange = exchange.strip().upper() if exchange else None
        if exchange and not _MIC.match(exchange):
            return ResolutionResult(
                ResolutionStatus.INVALID_INPUT, query, message=f"exchange {exchange!r} is not a MIC"
            )
        upper = text.upper()

        if ":" in upper:
            left, _, right = upper.partition(":")
            if _MIC.match(left) and _TICKER.match(right):
                upper, exchange = right, exchange or left
            elif _MIC.match(right) and _TICKER.match(left):
                upper, exchange = left, exchange or right

        cik = _CIK.match(upper.replace(" ", ""))
        if cik and (upper.startswith("CIK") or upper.isdigit()):
            padded = cik.group(1).zfill(10)
            companies = [c for c in self._companies.values() if c.cik == padded]
            return self._from_companies(query, companies, "cik", exchange, country)

        if _ISIN.match(upper):
            secs = [s for s in self._securities if s.isin == upper]
            if secs:
                return self._from_securities(query, secs, "isin", exchange, country)

        if _TICKER.match(upper):
            secs = [s for s in self._securities if s.ticker == upper]
            if secs:
                return self._from_securities(query, secs, "ticker", exchange, country)

        norm = normalize_name(text)
        if norm:
            exact = [
                c
                for c in self._companies.values()
                if norm in {normalize_name(n) for n in (c.name, *c.aliases)}
            ]
            if exact:
                return self._from_companies(query, exact, "name", exchange, country)
            partial = [
                c
                for c in self._companies.values()
                if any(norm in normalize_name(n) for n in (c.name, *c.aliases))
            ]
            if partial:
                cands = tuple(Candidate(c, None, "name_partial") for c in self._sorted(partial))
                return ResolutionResult(
                    ResolutionStatus.AMBIGUOUS,
                    query,
                    candidates=cands,
                    message="partial name match; confirm one candidate",
                )
        return self._not_found(query)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _sorted(companies: list[Company]) -> list[Company]:
        return sorted(companies, key=lambda c: c.company_id)

    def _filter(
        self, secs: list[Security], exchange: str | None, country: str | None
    ) -> list[Security]:
        if exchange:
            secs = [s for s in secs if s.exchange == exchange]
        if country:
            secs = [s for s in secs if self._companies[s.company_id].country == country.upper()]
        return secs

    def _from_securities(
        self,
        query: str,
        secs: list[Security],
        matched_on: str,
        exchange: str | None,
        country: str | None,
    ) -> ResolutionResult:
        filtered = self._filter(secs, exchange, country)
        if not filtered:
            return ResolutionResult(
                ResolutionStatus.NOT_FOUND,
                query,
                candidates=tuple(
                    Candidate(self._companies[s.company_id], s, matched_on) for s in secs
                ),
                message="identifier matched, but not on the requested exchange/country",
            )
        if len(filtered) == 1:
            s = filtered[0]
            return ResolutionResult(
                ResolutionStatus.RESOLVED,
                query,
                company=self._companies[s.company_id],
                security=s,
                matched_on=matched_on if not exchange else f"{matched_on}+exchange",
            )
        return ResolutionResult(
            ResolutionStatus.AMBIGUOUS,
            query,
            candidates=tuple(
                Candidate(self._companies[s.company_id], s, matched_on)
                for s in sorted(filtered, key=lambda s: s.security_id)
            ),
            message=f"{matched_on} matches {len(filtered)} listings; specify an exchange (MIC)",
        )

    def _from_companies(
        self,
        query: str,
        companies: list[Company],
        matched_on: str,
        exchange: str | None,
        country: str | None,
    ) -> ResolutionResult:
        if country:
            companies = [c for c in companies if c.country == country.upper()]
        if not companies:
            return self._not_found(query)
        if len(companies) > 1:
            return ResolutionResult(
                ResolutionStatus.AMBIGUOUS,
                query,
                candidates=tuple(Candidate(c, None, matched_on) for c in self._sorted(companies)),
                message=f"{matched_on} matches {len(companies)} companies",
            )
        company = companies[0]
        secs = [s for s in self._securities if s.company_id == company.company_id]
        if exchange:
            secs = [s for s in secs if s.exchange == exchange]
        if len(secs) == 1:
            return ResolutionResult(
                ResolutionStatus.RESOLVED, query, company, secs[0], matched_on=matched_on
            )
        primaries = [s for s in secs if s.is_primary_listing]
        if len(primaries) == 1:
            return ResolutionResult(
                ResolutionStatus.RESOLVED,
                query,
                company,
                primaries[0],
                matched_on=f"{matched_on}+primary_listing",
                notes=(f"selected primary listing {primaries[0].ticker}:{primaries[0].exchange}",),
            )
        if not secs:
            return ResolutionResult(
                ResolutionStatus.RESOLVED,
                query,
                company,
                None,
                matched_on=matched_on,
                notes=("company resolved; no listed security matches the request",),
            )
        return ResolutionResult(
            ResolutionStatus.AMBIGUOUS,
            query,
            candidates=tuple(Candidate(company, s, matched_on) for s in secs),
            message="company has several listings; specify an exchange (MIC)",
        )

    def _not_found(self, query: str) -> ResolutionResult:
        vocab = sorted(
            {s.ticker for s in self._securities} | {c.name for c in self._companies.values()}
        )
        suggestions = difflib.get_close_matches(query.strip(), vocab, n=3, cutoff=0.6)
        upper_matches = difflib.get_close_matches(query.strip().upper(), vocab, n=3, cutoff=0.6)
        merged = tuple(dict.fromkeys([*suggestions, *upper_matches]))
        return ResolutionResult(
            ResolutionStatus.NOT_FOUND,
            query,
            suggestions=merged,
            message="no company or security matches the query",
        )
