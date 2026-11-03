"""Shared offline fixtures for end-to-end research runs (SEC, prices, scripted LLM)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

import factories as f
from equitydesk.application.llm import LLMRequest, StructuredLLMClient
from equitydesk.application.research.grounding import collect_chunks
from equitydesk.application.workflow import ResearchInputs
from equitydesk.bootstrap import build_container, build_workflow
from equitydesk.config import Settings
from equitydesk.domain.market import MarketObservation
from equitydesk.domain.valuation import DcfAssumptions, ScenarioCase, ScenarioName
from equitydesk.infrastructure.llm.fake import ScriptedLLMProvider
from equitydesk.infrastructure.market_data.static import StaticMarketData
from equitydesk.infrastructure.parsers.html import HtmlDocumentParser
from equitydesk.infrastructure.sources.http import HttpFetcher
from equitydesk.infrastructure.sources.sec_edgar import SecEdgarAdapter
from sec_mocks import FIXTURES, sec_transport

AS_OF = "2024-01-15T00:00:00Z"
TENK = "sec_0000320193-23-000106"
CHUNKS = collect_chunks(
    [HtmlDocumentParser().parse(TENK, (FIXTURES / "aapl-20230930.htm").read_bytes())]
)


def cid(needle: str) -> str:
    return next(c.chunk.chunk_id for c in CHUNKS if needle in c.chunk.text)


def ev(needle: str, quote: str) -> list[dict[str, str]]:
    return [{"chunk_id": cid(needle), "quote": quote}]


BUSINESS = {
    "business_model": {"statement": "Apple designs, manufactures and markets smartphones, computers, tablets and wearables and sells related services.",
                       "claim_type": "fact", "evidence": ev("designs, manufactures", "designs, manufactures and markets smartphones, personal computers, tablets"),
                       "confidence": 0.95},
    "segments": [{"name": "Greater China", "statement": "Greater China is one of the geographic reportable segments.", "claim_type": "fact",
                  "evidence": ev("reportable segments", "reportable segments consist of the Americas, Europe, Greater China, Japan and Rest of Asia Pacific"),
                  "confidence": 0.9}],
    "revenue_drivers": [{"statement": "Services such as advertising, AppleCare and cloud services contribute revenue.", "claim_type": "fact",
                         "evidence": ev("Services include", "Services include advertising, AppleCare, cloud services"), "confidence": 0.85}],
    "customers": [],
    "geography": [],
    "not_found": ["customers", "geography"],
}  # fmt: skip
RISKS = {
    "risks": [
        {"title": "Supply chain concentration", "statement": "Manufacturing depends on outsourcing partners concentrated in Asia.",
         "category": "supply_chain", "horizon": "medium", "claim_type": "inference",
         "evidence": ev("outsourcing partners", "manufacturing is performed in whole or in part by outsourcing partners"), "confidence": 0.85},
        {"title": "Price competition", "statement": "Markets are characterized by aggressive price competition.",
         "category": "competition", "horizon": "near", "claim_type": "fact",
         "evidence": ev("substantial competition", "aggressive price competition and rapid technological change"), "confidence": 0.8},
        {"title": "Invented risk", "statement": "A regulator will ban the iPhone.", "category": "regulatory", "horizon": "near",
         "claim_type": "fact", "evidence": [{"chunk_id": cid("outsourcing partners"), "quote": "a regulator will ban the iPhone next year"}],
         "confidence": 0.9},
    ],
    "catalysts": [
        {"title": "Services growth", "statement": "Services revenue grew while iPhone revenue declined.", "category": "product",
         "horizon": "near", "claim_type": "inference",
         "evidence": ev("Services", "Services include advertising, AppleCare, cloud services"), "confidence": 0.7},
    ],
    "open_questions": [
        {"question": "How durable is Services growth?", "why_it_matters": "Services carry higher margins.",
         "evidence": ev("Services include", "Services include advertising, AppleCare, cloud services")},
    ],
}  # fmt: skip


def llm_router(request: LLMRequest) -> Any:
    if "makes money" in request.system:
        return BUSINESS
    if "investment risks" in request.system:
        return RISKS
    if "contradict" in request.system:
        return {"contradictions": []}
    raise AssertionError(f"unexpected prompt: {request.system[:60]}")


def assumptions(name: str, g: float, m: float) -> DcfAssumptions:
    return DcfAssumptions(
        assumption_set_id=f"co_apple:{name}", version=1, source=f"scenario:{name}",
        revenue_growth=(g,) * 5, ebit_margin=(m,) * 5, tax_rate=0.16, da_pct_revenue=0.03,
        capex_pct_revenue=0.03, nwc_pct_incremental_revenue=0.0, discount_rate=0.09, terminal_growth=0.03,
        rationale={"revenue_growth": "analyst", "ebit_margin": "analyst", "discount_rate": "CAPM", "terminal_growth": "nominal GDP"},
    )  # fmt: skip


INPUTS = ResearchInputs(scenarios=(
    ScenarioCase(name=ScenarioName.BULL, assumptions=assumptions("bull", 0.08, 0.33), narrative="Services mix lifts margins", probability=0.25),
    ScenarioCase(name=ScenarioName.BASE, assumptions=assumptions("base", 0.04, 0.30), narrative="Steady growth", probability=0.5),
    ScenarioCase(name=ScenarioName.BEAR, assumptions=assumptions("bear", -0.02, 0.26), narrative="Hardware decline", probability=0.25),
))  # fmt: skip
PRICES = [
    MarketObservation(security_id="sec_aapl_xnas", price=185.0, currency="USD", observed_at=f.dt(2024, 1, 12, 21),
                      price_source="manual:analyst", shares_outstanding=15_552_752_000.0,
                      shares_source="10-K cover 0000320193-23-000106", shares_as_of=date(2023, 10, 20)),
    MarketObservation(security_id="sec_aapl_xnas", price=99.0, currency="USD", observed_at=f.dt(2024, 3, 1, 21),
                      price_source="manual:analyst"),  # after as_of: must never be used
]  # fmt: skip


def sec(transport: httpx.BaseTransport | None = None) -> SecEdgarAdapter:
    fetcher = HttpFetcher("EquityDesk tests research@example.com", 1000, max_retries=1,
                          backoff_seconds=0, transport=transport or sec_transport(), sleep=lambda _s: None)  # fmt: skip
    return SecEdgarAdapter(fetcher, clock=lambda: f.dt(2024, 1, 16))


@pytest.fixture
def env(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data")
    container = build_container(settings, db_path=tmp_path / "e2e.db")
    container.research.clock = lambda: f.dt(2024, 1, 16)
    container.watchlist.clock = lambda: f.dt(2024, 1, 16)

    def make(
        script: Any = None, transport: httpx.BaseTransport | None = None, llm_review: bool = True
    ):
        provider = ScriptedLLMProvider([llm_router] * 10 if script is None else script)
        wf = build_workflow(container, StaticMarketData(PRICES), sec(transport), provider)
        wf.llm = StructuredLLMClient(provider, max_retries=0, sleep=lambda _s: None)
        wf.llm_review = llm_review
        wf.clock = lambda: f.dt(2024, 1, 16)
        return wf, provider

    return container, make
