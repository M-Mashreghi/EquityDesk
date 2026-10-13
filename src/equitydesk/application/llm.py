"""Structured LLM access: provider port, versioned prompts, schema validation, safe failure.

Raw model text never reaches the research domain: every response is parsed as JSON and
validated against an explicit pydantic schema. After bounded retries the caller receives a
`StructuredResult` with either a validated object or a structured failure.
"""

from __future__ import annotations

import json
import logging
import re
import string
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- provider port
@dataclass(frozen=True)
class LLMRequest:
    system: str
    user: str
    json_schema: dict[str, Any] | None = None
    max_tokens: int = 16000
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str | None = None


class LLMError(RuntimeError):
    """Base provider error. Messages must never contain credentials."""


class LLMTimeoutError(LLMError):
    pass


class LLMTransientError(LLMError):
    """Rate limit, overload, 5xx, connection failure: retryable."""


class LLMPermanentError(LLMError):
    """Bad request, auth, unknown model: retrying cannot help."""


class LLMRefusalError(LLMError):
    """The model declined the request (stop_reason == 'refusal')."""


class LLMProvider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


# --------------------------------------------------------------------------- prompts
@dataclass(frozen=True)
class PromptTemplate:
    prompt_id: str
    version: str
    system: str
    user_template: str  # string.Template syntax: $name

    def render(self, variables: Mapping[str, str]) -> tuple[str, str]:
        tpl = string.Template(self.user_template)
        missing = [
            name
            for name in {
                m.group("named") or m.group("braced")
                for m in tpl.pattern.finditer(self.user_template)
            }
            if name and name not in variables
        ]
        if missing:
            raise KeyError(f"prompt {self.prompt_id} missing variables: {sorted(missing)}")
        return self.system, tpl.substitute(variables)

    @property
    def ref(self) -> str:
        return f"{self.prompt_id}@{self.version}"


# --------------------------------------------------------------------------- results
class FailureCode(StrEnum):
    MALFORMED_JSON = "malformed_json"
    SCHEMA_VIOLATION = "schema_violation"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    REFUSED = "refused"
    TRUNCATED = "truncated"


@dataclass(frozen=True)
class StructuredFailure:
    code: FailureCode
    detail: str


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T | None
    failure: StructuredFailure | None
    prompt_ref: str
    schema_version: str
    model: str | None
    attempts: int
    input_tokens: int = 0
    output_tokens: int = 0
    errors: tuple[StructuredFailure, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.value is not None


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse a JSON object from model text, tolerating a markdown code fence. Raises ValueError."""
    candidate = text.strip()
    fence = _FENCE.match(candidate)
    if fence:
        candidate = fence.group(1)
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found")
        candidate = candidate[start : end + 1]
    return json.loads(candidate)


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema tightened for structured outputs: closed objects, all keys required."""
    schema = model.model_json_schema()

    def tighten(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = sorted(node["properties"])
            for value in node.values():
                tighten(value)
        elif isinstance(node, list):
            for item in node:
                tighten(item)

    tighten(schema)
    return schema


class StructuredLLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        max_retries: int = 2,
        timeout_seconds: float = 60.0,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep

    def generate[T: BaseModel](
        self,
        prompt: PromptTemplate,
        variables: Mapping[str, str],
        schema: type[T],
        schema_version: str,
    ) -> StructuredResult[T]:
        system, user = prompt.render(variables)
        request = LLMRequest(
            system=system,
            user=user,
            json_schema=strict_json_schema(schema),
            timeout_seconds=self.timeout_seconds,
        )
        errors: list[StructuredFailure] = []
        model: str | None = None
        tokens_in = tokens_out = 0
        attempts = 0
        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            try:
                response = self.provider.complete(request)
            except LLMTimeoutError as exc:
                errors.append(StructuredFailure(FailureCode.TIMEOUT, type(exc).__name__))
            except LLMTransientError as exc:
                errors.append(StructuredFailure(FailureCode.PROVIDER_ERROR, str(exc)[:200]))
            except LLMRefusalError as exc:
                errors.append(StructuredFailure(FailureCode.REFUSED, str(exc)[:200]))
                break
            except LLMPermanentError as exc:
                errors.append(StructuredFailure(FailureCode.PROVIDER_ERROR, str(exc)[:200]))
                break
            else:
                model = response.model
                tokens_in += response.input_tokens
                tokens_out += response.output_tokens
                if response.stop_reason == "refusal":
                    errors.append(StructuredFailure(FailureCode.REFUSED, "model declined"))
                    break
                if response.stop_reason == "max_tokens":
                    errors.append(StructuredFailure(FailureCode.TRUNCATED, "hit max_tokens"))
                else:
                    try:
                        data = extract_json(response.text)
                    except ValueError as exc:
                        errors.append(StructuredFailure(FailureCode.MALFORMED_JSON, str(exc)[:200]))
                    else:
                        try:
                            value = schema.model_validate(data)
                        except ValidationError as exc:
                            errors.append(
                                StructuredFailure(FailureCode.SCHEMA_VIOLATION, _summarize(exc))
                            )
                        else:
                            return StructuredResult(
                                value=value,
                                failure=None,
                                prompt_ref=prompt.ref,
                                schema_version=schema_version,
                                model=model,
                                attempts=attempts,
                                input_tokens=tokens_in,
                                output_tokens=tokens_out,
                                errors=tuple(errors),
                            )
            if attempt < self.max_retries:
                log.warning(
                    "structured LLM attempt failed",
                    extra={
                        "prompt": prompt.ref,
                        "attempt": attempts,
                        "code": errors[-1].code.value,
                    },
                )
                self.sleep(self.backoff_seconds * (2**attempt))
        return StructuredResult(
            value=None,
            failure=errors[-1]
            if errors
            else StructuredFailure(FailureCode.PROVIDER_ERROR, "no attempt"),
            prompt_ref=prompt.ref,
            schema_version=schema_version,
            model=model,
            attempts=attempts,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            errors=tuple(errors),
        )


def _summarize(exc: ValidationError) -> str:
    parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
    return "; ".join(parts)[:300]
