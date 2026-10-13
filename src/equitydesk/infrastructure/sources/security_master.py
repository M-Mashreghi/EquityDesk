"""Security-master adapters."""

from __future__ import annotations

import json
from pathlib import Path

from equitydesk.application.ports import CompanyRepository
from equitydesk.domain.companies import Company, Security

DEFAULT_MASTER_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "fixtures" / "security_master.json"
)


class JsonSecurityMaster:
    """Curated, versioned security master loaded from a JSON file."""

    def __init__(self, path: Path = DEFAULT_MASTER_PATH) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.version: str = raw["version"]
        self._companies = [Company.model_validate(c) for c in raw["companies"]]
        self._securities = [Security.model_validate(s) for s in raw["securities"]]
        known = {c.company_id for c in self._companies}
        orphans = [s.security_id for s in self._securities if s.company_id not in known]
        if orphans:
            raise ValueError(f"securities reference unknown companies: {orphans}")

    def companies(self) -> list[Company]:
        return list(self._companies)

    def securities(self) -> list[Security]:
        return list(self._securities)

    def seed(self, repo: CompanyRepository) -> None:
        for c in self._companies:
            repo.save_company(c)
        for s in self._securities:
            repo.save_security(s)
