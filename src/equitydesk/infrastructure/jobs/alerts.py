"""Alert sinks: an HTTP webhook (credentials from settings) and an in-memory sink for tests."""

from __future__ import annotations

import httpx
from pydantic import SecretStr

from equitydesk.application.watchlist import AlertDeliveryError
from equitydesk.domain.watchlist import Alert


class WebhookAlertSink:
    """POSTs the alert JSON to a webhook. URL and token are secrets and never logged/echoed."""

    def __init__(
        self, url: SecretStr, token: SecretStr | None = None, client: httpx.Client | None = None
    ) -> None:
        self._url = url
        self._token = token
        self._client = client or httpx.Client(timeout=10.0)

    def send(self, alert: Alert) -> None:
        headers = {"Content-Type": "application/json"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        try:
            resp = self._client.post(
                self._url.get_secret_value(), content=alert.model_dump_json(), headers=headers
            )
        except httpx.HTTPError as exc:
            raise AlertDeliveryError(f"webhook transport error: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            raise AlertDeliveryError(f"webhook responded with HTTP {resp.status_code}")


class MemoryAlertSink:
    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.sent.append(alert)
