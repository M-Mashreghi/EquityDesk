from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, Field

from equitydesk.application.llm import (
    FailureCode,
    LLMPermanentError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMTransientError,
    PromptTemplate,
    StructuredLLMClient,
    extract_json,
    strict_json_schema,
)
from equitydesk.infrastructure.llm.fake import ScriptedLLMProvider

PROMPT = PromptTemplate("test.extract", "1.0.0", "You extract facts.", "Document:\n$document")


class Item(BaseModel):
    statement: str
    confidence: float = Field(ge=0, le=1)


class Out(BaseModel):
    items: list[Item]


VALID = {"items": [{"statement": "Revenue fell 3%", "confidence": 0.9}]}


def client(
    script: list[Any], retries: int = 2
) -> tuple[StructuredLLMClient, ScriptedLLMProvider, list[float]]:
    provider = ScriptedLLMProvider(script)
    sleeps: list[float] = []
    return StructuredLLMClient(provider, max_retries=retries, sleep=sleeps.append), provider, sleeps


def test_valid_output_is_parsed_with_versions() -> None:
    c, provider, _ = client([VALID])
    r = c.generate(PROMPT, {"document": "x"}, Out, "out.v1")
    assert r.ok and r.value == Out.model_validate(VALID)
    assert (r.prompt_ref, r.schema_version, r.model, r.attempts) == (
        "test.extract@1.0.0",
        "out.v1",
        "fake-model-1",
        1,
    )
    req = provider.requests[0]
    assert req.user == "Document:\nx" and req.json_schema is not None
    assert r.input_tokens > 0


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        ("not json at all", FailureCode.MALFORMED_JSON),
        ('{"items": [', FailureCode.MALFORMED_JSON),
        ({"items": [{"statement": "x", "confidence": 7}]}, FailureCode.SCHEMA_VIOLATION),
        ({"wrong": 1}, FailureCode.SCHEMA_VIOLATION),
    ],
)
def test_malformed_output_never_enters_domain(bad: Any, code: FailureCode) -> None:
    c, provider, sleeps = client([bad, bad, bad])
    r = c.generate(PROMPT, {"document": "x"}, Out, "out.v1")
    assert not r.ok and r.value is None
    assert r.failure is not None and r.failure.code is code
    assert r.attempts == 3 and len(provider.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_retry_recovers_after_malformed_then_valid() -> None:
    c, _, _ = client(["oops", "```json\n" + '{"items": []}' + "\n```"])
    r = c.generate(PROMPT, {"document": "x"}, Out, "out.v1")
    assert r.ok and r.attempts == 2 and r.errors[0].code is FailureCode.MALFORMED_JSON


def test_timeout_and_transient_are_retried() -> None:
    c, _, _ = client([LLMTimeoutError("t"), LLMTransientError("429"), VALID])
    r = c.generate(PROMPT, {"document": "x"}, Out, "out.v1")
    assert r.ok and r.attempts == 3
    assert [e.code for e in r.errors] == [FailureCode.TIMEOUT, FailureCode.PROVIDER_ERROR]


def test_timeout_exhaustion_is_structured_failure() -> None:
    c, _, _ = client([LLMTimeoutError("t")] * 3)
    r = c.generate(PROMPT, {"document": "x"}, Out, "out.v1")
    assert r.failure is not None and r.failure.code is FailureCode.TIMEOUT and r.attempts == 3


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (LLMPermanentError("401"), FailureCode.PROVIDER_ERROR),
        (LLMRefusalError("no"), FailureCode.REFUSED),
    ],
)
def test_permanent_errors_and_refusals_are_not_retried(exc: Exception, code: FailureCode) -> None:
    c, provider, _ = client([exc, VALID])
    r = c.generate(PROMPT, {"document": "x"}, Out, "out.v1")
    assert not r.ok and r.failure is not None and r.failure.code is code
    assert len(provider.requests) == 1


def test_truncated_and_refusal_stop_reasons() -> None:
    truncated = LLMResponse(text='{"items": [', model="m", stop_reason="max_tokens")
    refusal = LLMResponse(text="", model="m", stop_reason="refusal")
    c, _, _ = client([truncated, refusal])
    r = c.generate(PROMPT, {"document": "x"}, Out, "out.v1")
    assert [e.code for e in r.errors] == [FailureCode.TRUNCATED, FailureCode.REFUSED]


def test_prompt_missing_variable_fails_fast() -> None:
    c, provider, _ = client([VALID])
    with pytest.raises(KeyError, match="document"):
        c.generate(PROMPT, {}, Out, "out.v1")
    assert provider.requests == []


def test_extract_json_variants() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Here you go: {"a": 1} thanks') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("[1, 2]")


def test_strict_schema_closes_objects() -> None:
    schema = strict_json_schema(Out)
    assert schema["additionalProperties"] is False and schema["required"] == ["items"]
    item = schema["$defs"]["Item"]
    assert item["additionalProperties"] is False and item["required"] == ["confidence", "statement"]


# ------------------------------------------------------------------ Anthropic adapter (mocked)
anthropic = pytest.importorskip("anthropic")


class _FakeMessages:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _fake_client(outcome: Any) -> Any:
    messages = _FakeMessages(outcome)
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))


def _status_error(cls: type, status: int) -> Exception:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("err", response=httpx.Response(status, request=req), body=None)


def test_anthropic_adapter_request_shape_and_response() -> None:
    from equitydesk.infrastructure.llm.anthropic_provider import AnthropicProvider

    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"items": []}')],
        model="claude-opus-5-5",
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    fake = _fake_client(response)
    provider = AnthropicProvider(api_key="unused", client=fake)
    out = provider.complete(LLMRequest(system="s", user="u", json_schema={"type": "object"}))
    assert out.text == '{"items": []}' and out.input_tokens == 10
    call = fake.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]


@pytest.mark.parametrize(
    ("make_exc", "expected"),
    [
        (lambda: _status_error(anthropic.RateLimitError, 429), LLMTransientError),
        (lambda: _status_error(anthropic.InternalServerError, 500), LLMTransientError),
        (lambda: _status_error(anthropic.AuthenticationError, 401), LLMPermanentError),
        (lambda: _status_error(anthropic.BadRequestError, 400), LLMPermanentError),
        (
            lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")),
            LLMTimeoutError,
        ),
        (
            lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
            LLMTransientError,
        ),
    ],
)
def test_anthropic_adapter_error_mapping(make_exc: Any, expected: type) -> None:
    from equitydesk.infrastructure.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(api_key="unused", client=_fake_client(make_exc()))
    with pytest.raises(expected):
        provider.complete(LLMRequest(system="s", user="u"))


def test_anthropic_adapter_refusal() -> None:
    from equitydesk.infrastructure.llm.anthropic_provider import AnthropicProvider

    refused = SimpleNamespace(content=[], model="m", stop_reason="refusal", usage=None)
    with pytest.raises(LLMRefusalError):
        AnthropicProvider(api_key="unused", client=_fake_client(refused)).complete(
            LLMRequest(system="s", user="u")
        )
