"""Claude provider adapter built on the official `anthropic` SDK (optional extra: `.[llm]`).

- Structured outputs via `output_config.format` (JSON schema); the application layer still
  validates every response.
- SDK retries are disabled (`max_retries=0`); the StructuredLLMClient owns the bounded retry
  policy so attempts are counted and logged in one place.
- Server-side refusal fallbacks are enabled by default (`fallbacks: "default"`).
"""

from __future__ import annotations

from typing import Any

from equitydesk.application.llm import (
    LLMPermanentError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMTransientError,
)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        enable_fallbacks: bool = True,
        client: Any = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.enable_fallbacks = enable_fallbacks
        if client is None:
            import anthropic  # optional dependency, imported only when used

            client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        self.client = client

    def complete(self, request: LLMRequest) -> LLMResponse:
        import anthropic

        output_config: dict[str, Any] = {"effort": self.effort}
        if request.json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.json_schema}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": output_config,
            "timeout": request.timeout_seconds,
        }
        try:
            if self.enable_fallbacks:
                response = self.client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **kwargs
                )
            else:
                response = self.client.messages.create(**kwargs)
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError("request timed out") from exc
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            raise LLMTransientError(f"HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMTransientError("connection error") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code == 529:
                raise LLMTransientError(f"HTTP {exc.status_code}") from exc
            raise LLMPermanentError(f"HTTP {exc.status_code}: {getattr(exc, 'type', '')}") from exc

        if response.stop_reason == "refusal":
            raise LLMRefusalError("model declined the request")
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text,
            model=getattr(response, "model", self.model),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            stop_reason=response.stop_reason,
        )
