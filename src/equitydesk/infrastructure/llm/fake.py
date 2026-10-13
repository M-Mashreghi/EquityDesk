"""Deterministic, offline LLM provider for tests, CI and local development."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from equitydesk.application.llm import LLMRequest, LLMResponse

Script = str | dict[str, Any] | Exception | LLMResponse | Callable[[LLMRequest], Any]


class ScriptedLLMProvider:
    """Returns scripted outputs in order; dicts are serialized as JSON, exceptions are raised.

    A callable entry receives the request (for routing by prompt content).
    """

    name = "fake"

    def __init__(self, script: Iterable[Script] = (), model: str = "fake-model-1") -> None:
        self.script = list(script)
        self.model = model
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("ScriptedLLMProvider ran out of scripted responses")
        item = self.script.pop(0)
        if callable(item) and not isinstance(item, Exception):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResponse):
            return item
        text = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=len(request.system + request.user) // 4,
            output_tokens=len(text) // 4,
            stop_reason="end_turn",
        )
