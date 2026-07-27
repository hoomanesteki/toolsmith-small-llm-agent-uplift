"""Adapter for every provider that speaks the OpenAI chat-completions dialect.

Groq, OpenAI, Mistral and Together are all one class with a different base URL,
which is a useful thing to know and an annoying thing to discover one endpoint
at a time.

Two behaviours here are not cosmetic:

* **Strict JSON.** Where the model card claims ``strict_json``, the adapter
  sends ``response_format: json_schema`` with ``strict: true``. Where it only
  claims ``json_object`` (Qwen3.6 on Groq, for example), it degrades and the
  runtime pays for that in repair turns. That difference shows up in the matrix.
* **Reported usage wins.** When the provider returns a usage block we take it
  and mark ``usage_source="reported"``, so a live row's cost is the invoice
  number rather than our estimate.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from toolsmith.config import ModelSpec
from toolsmith.env import api_key
from toolsmith.providers.base import (
    LLMRequest,
    LLMResponse,
    Message,
    Provider,
    ProviderError,
    RateLimiter,
    ToolCall,
)

BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "together": "https://api.together.xyz/v1",
}


def _to_wire(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id or "",
            "content": message.content,
        }
    body: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        body["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, sort_keys=True),
                },
            }
            for call in message.tool_calls
        ]
    return body


class OpenAICompatProvider(Provider):
    """Live chat-completions client. Retries transport errors, never model errors."""

    provenance = "live"

    def __init__(
        self,
        spec: ModelSpec,
        limiter: RateLimiter | None = None,
        *,
        timeout: float = 90.0,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(spec, limiter)
        self.name = spec.provider
        self.base_url = BASE_URLS[spec.provider]
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = client

    # ------------------------------------------------------------ transport --

    def _http(self) -> httpx.Client:
        if self._client is None:
            key = api_key(self.spec.provider)
            if not key:
                raise ProviderError(
                    f"no API key for {self.spec.provider}. "
                    f"Set it in .env, or run with --provider simulated."
                )
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": [_to_wire(m) for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.stop:
            payload["stop"] = request.stop
        if request.tools:
            payload["tools"] = [t.to_openai() for t in request.tools]
            payload["tool_choice"] = "auto"
        if request.response_format == "json_schema" and self.spec.supports.strict_json:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "toolsmith_output",
                    "strict": True,
                    "schema": request.json_schema or {"type": "object"},
                },
            }
        elif request.response_format in ("json_object", "json_schema"):
            payload["response_format"] = {"type": "json_object"}
        return payload

    # -------------------------------------------------------------- calling --

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = self._payload(request)
        tokens_in = self._count_input(request)
        if self.limiter is not None:
            self.limiter.acquire(tokens_in)

        started = time.perf_counter()
        last_error: str | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._http().post("/chat/completions", json=payload)
            except httpx.HTTPError as exc:  # transport, not model
                last_error = f"transport: {exc}"
                time.sleep(min(8.0, 2.0**attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"http {response.status_code}: {response.text[:200]}"
                retry_after = float(response.headers.get("retry-after", min(8.0, 2.0**attempt)))
                time.sleep(retry_after)
                continue
            if response.status_code >= 400:
                raise ProviderError(f"http {response.status_code}: {response.text[:500]}")
            return self._parse(response.json(), tokens_in, time.perf_counter() - started)

        raise ProviderError(
            f"{self.spec.provider} failed after {self.max_retries} tries: {last_error}"
        )

    def _parse(self, body: dict[str, Any], tokens_in: int, latency: float) -> LLMResponse:
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"__unparsable__": fn.get("arguments", "")}
            calls.append(
                ToolCall(
                    id=raw.get("id") or str(uuid.uuid4()), name=fn.get("name", ""), arguments=args
                )
            )

        usage = body.get("usage") or {}
        reported_in = usage.get("prompt_tokens")
        cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        return LLMResponse(
            text=message.get("content") or "",
            tool_calls=calls,
            tokens_in=int(reported_in) if reported_in else tokens_in,
            tokens_cached_in=int(cached),
            tokens_out=int(usage.get("completion_tokens") or 0),
            latency_s=latency,
            finish_reason="tool_calls" if calls else (choice.get("finish_reason") or "stop"),
            usage_source="reported" if reported_in else "estimated",
            raw=body,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
