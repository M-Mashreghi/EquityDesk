"""Company / security master entities.

A ticker is *not* globally unique: the same symbol can trade on several exchanges or be reused
by different issuers. Securities are therefore identified by (ticker, exchange MIC) at minimum.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator

from equitydesk.domain.common import DomainModel


class IdentifierType(StrEnum):
    TICKER = "ticker"
    CIK = "cik"
    ISIN = "isin"
    FIGI = "figi"
    LEI = "lei"


class Identifier(DomainModel):
    type: IdentifierType
    value: str = Field(min_length=1)
    # Exchange MIC qualifies tickers; None for global identifiers such as CIK/ISIN.
    exchange: str | None = None


class Company(DomainModel):
    company_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2")
    cik: str | None = Field(default=None, pattern=r"^\d{10}$")
    lei: str | None = None
    aliases: tuple[str, ...] = ()
    sector: str | None = None

    @field_validator("country")
    @classmethod
    def _upper_country(cls, v: str) -> str:
        return v.upper()


class SecurityType(StrEnum):
    COMMON_STOCK = "common_stock"
    ADR = "adr"
    PREFERRED = "preferred"


class Security(DomainModel):
    security_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    exchange: str = Field(min_length=4, max_length=4, description="ISO 10383 MIC, e.g. XNAS")
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 trading currency")
    security_type: SecurityType = SecurityType.COMMON_STOCK
    share_class: str | None = None
    isin: str | None = Field(default=None, pattern=r"^[A-Z]{2}[A-Z0-9]{9}\d$")
    figi: str | None = None
    is_primary_listing: bool = False

    @field_validator("ticker", "exchange", "currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    def identifiers(self) -> list[Identifier]:
        ids = [Identifier(type=IdentifierType.TICKER, value=self.ticker, exchange=self.exchange)]
        if self.isin:
            ids.append(Identifier(type=IdentifierType.ISIN, value=self.isin))
        if self.figi:
            ids.append(Identifier(type=IdentifierType.FIGI, value=self.figi))
        return ids
