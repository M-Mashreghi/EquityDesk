"""HTTP client for the research API. The dashboard talks to EquityDesk only through this."""

from __future__ import annotations

from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


class ApiClient:
    def __init__(
        self, base_url: str = "http://localhost:8000", http: httpx.Client | None = None
    ) -> None:
        self.http = http or httpx.Client(base_url=base_url, timeout=30.0)

    def _send(self, method: str, path: str, **kw: Any) -> httpx.Response:
        resp = self.http.request(method, path, **kw)
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            raise ApiError(
                resp.status_code,
                err.get("code", "http_error"),
                err.get("message", resp.text[:200]),
                err.get("details"),
            )
        return resp

    def _json(self, method: str, path: str, **kw: Any) -> Any:
        return self._send(method, path, **kw).json()

    def health(self) -> dict[str, Any]:
        result: dict[str, Any] = self._json("GET", "/healthz")
        return result

    def resolve(self, query: str, exchange: str | None = None) -> dict[str, Any]:
        params = {"q": query} | ({"exchange": exchange} if exchange else {})
        result: dict[str, Any] = self._json("GET", "/companies/resolve", params=params)
        return result

    def company_runs(self, company_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._json("GET", f"/companies/{company_id}/runs")
        return result

    def create_run(
        self, query: str, as_of: str | None = None, exchange: str | None = None
    ) -> dict[str, Any]:
        body = (
            {"query": query}
            | ({"as_of": as_of} if as_of else {})
            | ({"exchange": exchange} if exchange else {})
        )
        result: dict[str, Any] = self._json("POST", "/research-runs", json=body)
        return result

    def run(self, run_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json("GET", f"/research-runs/{run_id}")
        return result

    def documents(self, run_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._json("GET", f"/research-runs/{run_id}/documents")
        return result

    def financials(self, run_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json("GET", f"/research-runs/{run_id}/financials")
        return result

    def valuations(self, run_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._json("GET", f"/research-runs/{run_id}/valuations")
        return result

    def report(self, run_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json("GET", f"/research-runs/{run_id}/report")
        return result

    def report_markdown(self, run_id: str) -> str:
        return self._send(
            "GET", f"/research-runs/{run_id}/report", params={"format": "markdown"}
        ).text
