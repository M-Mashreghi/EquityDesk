"""Standardize reported XBRL facts into income-statement, balance-sheet and cash-flow concepts.

Rules (documented in docs/ARCHITECTURE.md "Financial statement mapping"):
- Only facts available at or before `as_of` are considered (point-in-time).
- Durations are classified as annual (FY, 330-380 days) or quarterly (Q, 80-100 days);
  year-to-date and other durations are skipped, never silently treated as annual.
- A fact must carry the unit kind its concept expects (monetary / shares / per-share);
  otherwise it is skipped with an issue. Different currencies are never merged.
- Per (concept, period, unit) the highest-priority tag wins; within that tag the most recently
  filed value (as of `as_of`) wins, so restatements apply only once they were public.
  Disagreeing lower-priority tags and superseded restated values are reported as issues.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from equitydesk.domain.financials import FactOrigin, FinancialFact, PeriodType, RawReportedFact

MAPPER_VERSION = "1.0.0"


class Statement(StrEnum):
    INCOME = "income_statement"
    BALANCE = "balance_sheet"
    CASH_FLOW = "cash_flow"


class UnitKind(StrEnum):
    MONETARY = "monetary"
    SHARES = "shares"
    PER_SHARE = "per_share"


@dataclass(frozen=True)
class ConceptSpec:
    statement: Statement
    period_type: PeriodType
    unit_kind: UnitKind
    tags: tuple[str, ...]  # priority order, "taxonomy:Tag"


def _g(*tags: str) -> tuple[str, ...]:
    return tuple(t if ":" in t else f"us-gaap:{t}" for t in tags)


D, I = PeriodType.DURATION, PeriodType.INSTANT  # noqa: E741
INC, BAL, CF = Statement.INCOME, Statement.BALANCE, Statement.CASH_FLOW
MON, SHR, PS = UnitKind.MONETARY, UnitKind.SHARES, UnitKind.PER_SHARE

CONCEPTS: dict[str, ConceptSpec] = {
    "Revenue": ConceptSpec(INC, D, MON, _g(
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax")),
    "CostOfRevenue": ConceptSpec(INC, D, MON, _g(
        "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold")),
    "GrossProfit": ConceptSpec(INC, D, MON, _g("GrossProfit")),
    "ResearchAndDevelopment": ConceptSpec(INC, D, MON, _g("ResearchAndDevelopmentExpense")),
    "SellingGeneralAdministrative": ConceptSpec(INC, D, MON, _g(
        "SellingGeneralAndAdministrativeExpense")),
    "OperatingIncome": ConceptSpec(INC, D, MON, _g("OperatingIncomeLoss")),
    "InterestExpense": ConceptSpec(INC, D, MON, _g("InterestExpense", "InterestExpenseNonoperating")),
    "PretaxIncome": ConceptSpec(INC, D, MON, _g(
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments")),
    "IncomeTaxExpense": ConceptSpec(INC, D, MON, _g("IncomeTaxExpenseBenefit")),
    "NetIncome": ConceptSpec(INC, D, MON, _g("NetIncomeLoss")),
    "EPSBasic": ConceptSpec(INC, D, PS, _g("EarningsPerShareBasic")),
    "EPSDiluted": ConceptSpec(INC, D, PS, _g("EarningsPerShareDiluted")),
    "WeightedSharesBasic": ConceptSpec(INC, D, SHR, _g("WeightedAverageNumberOfSharesOutstandingBasic")),
    "WeightedSharesDiluted": ConceptSpec(INC, D, SHR, _g(
        "WeightedAverageNumberOfDilutedSharesOutstanding")),
    "OperatingCashFlow": ConceptSpec(CF, D, MON, _g("NetCashProvidedByUsedInOperatingActivities")),
    "CapitalExpenditure": ConceptSpec(CF, D, MON, _g("PaymentsToAcquirePropertyPlantAndEquipment")),
    "DepreciationAmortization": ConceptSpec(CF, D, MON, _g(
        "DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet")),
    "ShareRepurchases": ConceptSpec(CF, D, MON, _g("PaymentsForRepurchaseOfCommonStock")),
    "DividendsPaid": ConceptSpec(CF, D, MON, _g("PaymentsOfDividends", "PaymentsOfDividendsCommonStock")),
    "StockBasedCompensation": ConceptSpec(CF, D, MON, _g("ShareBasedCompensation")),
    "CashAndEquivalents": ConceptSpec(BAL, I, MON, _g("CashAndCashEquivalentsAtCarryingValue")),
    "ShortTermInvestments": ConceptSpec(BAL, I, MON, _g("MarketableSecuritiesCurrent", "ShortTermInvestments")),
    "AccountsReceivable": ConceptSpec(BAL, I, MON, _g("AccountsReceivableNetCurrent")),
    "Inventory": ConceptSpec(BAL, I, MON, _g("InventoryNet")),
    "CurrentAssets": ConceptSpec(BAL, I, MON, _g("AssetsCurrent")),
    "TotalAssets": ConceptSpec(BAL, I, MON, _g("Assets")),
    "AccountsPayable": ConceptSpec(BAL, I, MON, _g("AccountsPayableCurrent")),
    "CurrentLiabilities": ConceptSpec(BAL, I, MON, _g("LiabilitiesCurrent")),
    "CommercialPaper": ConceptSpec(BAL, I, MON, _g("CommercialPaper")),
    "CurrentDebt": ConceptSpec(BAL, I, MON, _g("LongTermDebtCurrent", "DebtCurrent")),
    "LongTermDebt": ConceptSpec(BAL, I, MON, _g("LongTermDebtNoncurrent")),
    "TotalLiabilities": ConceptSpec(BAL, I, MON, _g("Liabilities")),
    "StockholdersEquity": ConceptSpec(BAL, I, MON, _g("StockholdersEquity")),
    "SharesOutstanding": ConceptSpec(BAL, I, SHR, _g(
        "CommonStockSharesOutstanding", "dei:EntityCommonStockSharesOutstanding")),
}  # fmt: skip

_TAG_INDEX: dict[str, tuple[str, int]] = {
    tag: (concept, rank) for concept, spec in CONCEPTS.items() for rank, tag in enumerate(spec.tags)
}
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_PER_SHARE = re.compile(r"^[A-Z]{3}/shares$")


class IssueCode(StrEnum):
    UNIT_MISMATCH = "unit_mismatch"
    UNSUPPORTED_DURATION = "unsupported_duration"
    MISSING_START = "missing_start"
    TAG_CONFLICT = "tag_conflict"
    RESTATED = "restated"
    MULTIPLE_UNITS = "multiple_units"


@dataclass(frozen=True)
class MappingIssue:
    code: IssueCode
    concept: str
    detail: str


@dataclass
class MappingResult:
    facts: list[FinancialFact]
    issues: list[MappingIssue] = field(default_factory=list)
    skipped: int = 0


def unit_kind_of(unit: str) -> UnitKind | None:
    if unit == "shares":
        return UnitKind.SHARES
    if _PER_SHARE.match(unit):
        return UnitKind.PER_SHARE
    if _CURRENCY.match(unit):
        return UnitKind.MONETARY
    return None


def classify_duration(start: date, end: date) -> str | None:
    days = (end - start).days + 1
    if 330 <= days <= 380:
        return "FY"
    if 80 <= days <= 100:
        return "Q"
    return None


def _fact_id(
    company_id: str, concept: str, start: date | None, end: date, unit: str, accn: str
) -> str:
    key = f"{company_id}|{concept}|{start}|{end}|{unit}|{accn}|{MAPPER_VERSION}"
    return "fct_" + hashlib.sha256(key.encode()).hexdigest()[:24]


def map_reported_facts(
    company_id: str, raw: Iterable[RawReportedFact], as_of: datetime
) -> MappingResult:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    issues: list[MappingIssue] = []
    skipped = 0
    groups: dict[tuple[str, date | None, date, str], list[tuple[int, RawReportedFact]]] = (
        defaultdict(list)
    )

    for r in raw:
        if r.company_id != company_id or r.available_at > as_of:
            continue
        hit = _TAG_INDEX.get(r.locator)
        if hit is None:
            continue
        concept, rank = hit
        spec = CONCEPTS[concept]
        if unit_kind_of(r.unit) is not spec.unit_kind:
            issues.append(
                MappingIssue(IssueCode.UNIT_MISMATCH, concept, f"{r.locator} in {r.unit}")
            )
            skipped += 1
            continue
        start = r.period_start
        if spec.period_type is PeriodType.DURATION:
            if start is None:
                issues.append(MappingIssue(IssueCode.MISSING_START, concept, r.locator))
                skipped += 1
                continue
            if classify_duration(start, r.period_end) is None:
                skipped += 1  # YTD / other durations are expected in 10-Qs; not an error
                continue
        else:
            start = None
        groups[(concept, start, r.period_end, r.unit)].append((rank, r))

    facts: list[FinancialFact] = []
    for (concept, start, end, unit), members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        best_rank = min(rank for rank, _ in members)
        top = [r for rank, r in members if rank == best_rank]
        chosen = max(top, key=lambda r: (r.available_at, r.accession))
        for r in top:
            if r.value != chosen.value and r.accession != chosen.accession:
                issues.append(
                    MappingIssue(
                        IssueCode.RESTATED,
                        concept,
                        f"{end}: {r.value} ({r.accession}) superseded by {chosen.value} "
                        f"({chosen.accession})",
                    )
                )
        for rank, r in members:
            if rank != best_rank and r.value != chosen.value:
                issues.append(
                    MappingIssue(
                        IssueCode.TAG_CONFLICT,
                        concept,
                        f"{end}: {r.locator}={r.value} ignored; {chosen.locator}={chosen.value} used",
                    )
                )
        spec = CONCEPTS[concept]
        kind = classify_duration(start, end) if start else None
        facts.append(
            FinancialFact(
                fact_id=_fact_id(company_id, concept, start, end, unit, chosen.accession),
                company_id=company_id,
                concept=concept,
                value=chosen.value,
                unit=unit,
                period_type=spec.period_type,
                period_start=start,
                period_end=end,
                fiscal_year=end.year if kind == "FY" else None,
                fiscal_period="FY" if kind == "FY" else None,
                source_accession=chosen.accession,
                source_locator=chosen.locator,
                origin=FactOrigin.REPORTED,
                available_at=chosen.available_at,
            )
        )

    # Several currencies for one concept/period are kept apart and flagged, never merged.
    seen: dict[tuple[str, date | None, date], set[str]] = defaultdict(set)
    for fct in facts:
        seen[(fct.concept, fct.period_start, fct.period_end)].add(fct.unit)
    for (concept, _start, end), units in sorted(seen.items(), key=str):
        if len(units) > 1:
            issues.append(
                MappingIssue(IssueCode.MULTIPLE_UNITS, concept, f"{end}: {sorted(units)}")
            )

    facts.extend(_derive_gross_profit(facts))
    return MappingResult(facts=facts, issues=issues, skipped=skipped)


def _derive_gross_profit(facts: list[FinancialFact]) -> list[FinancialFact]:
    """GrossProfit = Revenue - CostOfRevenue, only where not reported and units/periods match."""
    index = {(f.concept, f.period_start, f.period_end, f.unit): f for f in facts}
    derived: list[FinancialFact] = []
    for (concept, start, end, unit), rev in index.items():
        if concept != "Revenue" or ("GrossProfit", start, end, unit) in index:
            continue
        cost = index.get(("CostOfRevenue", start, end, unit))
        if cost is None:
            continue
        derived.append(
            FinancialFact(
                fact_id=_fact_id(rev.company_id, "GrossProfit", start, end, unit, "derived"),
                company_id=rev.company_id,
                concept="GrossProfit",
                value=rev.value - cost.value,
                unit=unit,
                period_type=PeriodType.DURATION,
                period_start=start,
                period_end=end,
                fiscal_year=rev.fiscal_year,
                fiscal_period=rev.fiscal_period,
                source_locator="derived: Revenue - CostOfRevenue",
                origin=FactOrigin.DERIVED,
                available_at=max(rev.available_at, cost.available_at),
                derived_from=(rev.fact_id, cost.fact_id),
            )
        )
    return derived


@dataclass(frozen=True)
class StatementPeriod:
    period_start: date | None
    period_end: date
    facts: dict[str, FinancialFact]

    def value(self, concept: str) -> float | None:
        f = self.facts.get(concept)
        return f.value if f else None


def annual_statements(
    facts: Iterable[FinancialFact], currency: str = "USD"
) -> dict[date, StatementPeriod]:
    """Fiscal-year view keyed by period end: FY durations plus balance-sheet instants at FY end.

    Monetary facts in another currency are excluded rather than mixed.
    """
    facts = list(facts)
    periods: dict[date, StatementPeriod] = {}
    for f in facts:
        if f.fiscal_period == "FY" and f.period_type is PeriodType.DURATION:
            periods.setdefault(f.period_end, StatementPeriod(f.period_start, f.period_end, {}))
    for f in facts:
        p = periods.get(f.period_end)
        if p is None:
            continue
        kind = unit_kind_of(f.unit)
        if kind is UnitKind.MONETARY and f.unit != currency:
            continue
        if kind is UnitKind.PER_SHARE and f.unit != f"{currency}/shares":
            continue
        if f.period_type is PeriodType.DURATION and f.period_start != p.period_start:
            continue
        p.facts[f.concept] = f
    return dict(sorted(periods.items()))
