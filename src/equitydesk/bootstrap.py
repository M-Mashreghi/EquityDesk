"""Composition root: wire settings, persistence and adapters into application services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from equitydesk.application.research_service import ResearchService
from equitydesk.application.resolver import SecurityResolver
from equitydesk.config import CONFIG_SCHEMA_VERSION, Settings, load_settings
from equitydesk.infrastructure.db.sqlite import Repositories, open_database
from equitydesk.infrastructure.sources.security_master import JsonSecurityMaster


@dataclass
class Container:
    settings: Settings
    repos: Repositories
    master: JsonSecurityMaster
    research: ResearchService


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
        config_version=CONFIG_SCHEMA_VERSION,
    )
    return Container(settings, repos, master, service)
