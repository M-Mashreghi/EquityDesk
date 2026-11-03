"""Composition root: wire settings, persistence and adapters into application services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from equitydesk.application.llm import LLMProvider, StructuredLLMClient
from equitydesk.application.research_service import ResearchService
from equitydesk.application.resolver import SecurityResolver
from equitydesk.application.telemetry import TokenPrices
from equitydesk.application.watchlist import AlertSink, WatchlistService
from equitydesk.application.workflow import MarketDataSource, ResearchWorkflow
from equitydesk.config import (
    CONFIG_SCHEMA_VERSION,
    ConfigError,
    LLMProviderName,
    Settings,
    load_settings,
)
from equitydesk.infrastructure.db.raw_store import FilesystemRawStore
from equitydesk.infrastructure.db.sqlite import SCHEMA_VERSION, Repositories, open_database
from equitydesk.infrastructure.jobs.alerts import WebhookAlertSink
from equitydesk.infrastructure.llm.fake import UnavailableLLMProvider
from equitydesk.infrastructure.market_data.static import StaticMarketData
from equitydesk.infrastructure.parsers.registry import ParserRegistry
from equitydesk.infrastructure.sources.http import HttpFetcher
from equitydesk.infrastructure.sources.research_sources import SecFilingSource, SecReportedFacts
from equitydesk.infrastructure.sources.sec_edgar import SecEdgarAdapter
from equitydesk.infrastructure.sources.security_master import JsonSecurityMaster


@dataclass
class Container:
    settings: Settings
    repos: Repositories
    master: JsonSecurityMaster
    research: ResearchService
    watchlist: WatchlistService


def build_container(
    settings: Settings | None = None, db_path: Path | str | None = None
) -> Container:
    settings = settings or load_settings(env_file=Path(".env"))
    repos = open_database(db_path if db_path is not None else settings.db_path)
    master = JsonSecurityMaster()
    service = ResearchService(
        companies=repos.companies,
        runs=repos.runs,
        documents=repos.documents,
        facts=repos.facts,
        valuations=repos.valuations,
        reports=repos.reports,
        snapshots=repos.snapshots,
        resolver=SecurityResolver(master),
        findings=repos.findings,
        config_version=CONFIG_SCHEMA_VERSION,
    )
    sink: AlertSink | None = None
    if settings.alert_webhook_url is not None:
        sink = WebhookAlertSink(settings.alert_webhook_url, settings.alert_webhook_token)
    watchlist = WatchlistService(repos.watchlist, service, sink)
    return Container(settings, repos, master, service, watchlist)


def readiness(container: Container) -> dict[str, Any]:
    """Readiness: the database answers with the expected schema and the data dir is writable."""
    checks: dict[str, dict[str, Any]] = {}
    try:
        container.repos.db.conn.execute("SELECT 1").fetchone()
        version = container.repos.db.schema_version()
        checks["database"] = {"ok": version == SCHEMA_VERSION, "schema_version": version,
                              "expected": SCHEMA_VERSION}  # fmt: skip
    except Exception as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}
    data_dir = container.settings.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".ready-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["data_dir"] = {"ok": True}
    except OSError as exc:
        checks["data_dir"] = {"ok": False, "error": type(exc).__name__}
    return {"ready": all(c["ok"] for c in checks.values()), "checks": checks}


def diagnostics(container: Container) -> dict[str, Any]:
    """Component diagnostics without secret values (only whether they are configured)."""
    s = container.settings
    return {
        "env": s.env.value,
        "schema_version": container.repos.db.schema_version(),
        "security_master_version": container.master.version,
        "llm": {"provider": s.llm_provider.value, "model": s.llm_model,
                "api_key_configured": s.llm_api_key is not None,
                "cost_tracking": s.llm_cost_input_per_mtok is not None and s.llm_cost_output_per_mtok is not None},
        "sec": {"user_agent_configured": bool(s.sec_user_agent), "max_rps": s.sec_max_requests_per_second},
        "alerts": {"webhook_configured": s.alert_webhook_url is not None},
        "watchlist_entries": len(container.repos.watchlist.list_entries()),
    }  # fmt: skip


def llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider is LLMProviderName.ANTHROPIC:
        from equitydesk.infrastructure.llm.anthropic_provider import AnthropicProvider

        assert settings.llm_api_key is not None and settings.llm_model
        return AnthropicProvider(settings.llm_api_key.get_secret_value(), settings.llm_model)
    return UnavailableLLMProvider()


def build_workflow(
    container: Container,
    market_data: MarketDataSource | None = None,
    sec: SecEdgarAdapter | None = None,
    provider: LLMProvider | None = None,
) -> ResearchWorkflow:
    """Production wiring: SEC EDGAR filings and XBRL facts, filesystem raw store, configured LLM.

    Prices come from `<data_dir>/market/observations.json` when present (analyst-maintained).
    """
    settings = container.settings
    if sec is None:
        if not settings.sec_user_agent:
            raise ConfigError(["SEC_USER_AGENT is required to collect filings"])
        sec = SecEdgarAdapter(
            HttpFetcher(settings.sec_user_agent, settings.sec_max_requests_per_second)
        )
    if market_data is None:
        prices = settings.data_dir / "market" / "observations.json"
        market_data = StaticMarketData.from_json(prices) if prices.exists() else StaticMarketData()
    llm = StructuredLLMClient(
        provider or llm_provider(settings),
        max_retries=settings.llm_max_retries,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    return ResearchWorkflow(
        service=container.research,
        snapshots=container.repos.snapshots,
        normalized=container.repos.normalized,
        raw_store=FilesystemRawStore(settings.data_dir / "raw"),
        filings=SecFilingSource(sec),
        reported_facts=SecReportedFacts(sec),
        market_data=market_data,
        parser=ParserRegistry(),
        llm=llm,
        hook=container.watchlist,
        telemetry_store=container.repos.telemetry,
        token_prices=TokenPrices(
            settings.llm_cost_input_per_mtok, settings.llm_cost_output_per_mtok
        ),
    )
