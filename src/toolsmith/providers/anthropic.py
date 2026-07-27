"""Anthropic Messages API adapter.

Two differences from the OpenAI dialect that matter for this project:

* The system prompt is a top-level field, not a message. That is convenient,
  because it makes the stable-prefix rule easy to enforce.
* ``cache_control`` breakpoints are explicit. We mark the end of the system
  block and the end of the tool list, which is exactly the prefix the runtime
  promises never to mutate. That is what turns Opus's $5.00/M input rate into
  an effective $1.84/M and rewrites the cost argument in section 1.5.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from toolsmith.config import ModelSpec
from toolsmith.env import api_key
from toolsmith.providers.base import (
    LLMRequest,
    LLMResponse,
    Provider,
    ProviderError,
    RateLimiter,
    ToolCall,
)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    provenance = "live"
    name = "anthropic"

    def __init__(
        self,
        spec: ModelSpec,
        limiter: RateLimiter | None = None,
        *,
        timeout: float = 120.0,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(spec, limiter)
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            key = api_key("anthropic")
            if not key:
                raise ProviderError("no ANTHROPIC_API_KEY. Run with --provider simulated instead.")
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={
                    "x-api-key": key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
            )
        return self._client

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        system_parts = [m.content for m in request.messages if m.role == "system"]
        turns: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                continue
            if message.role == "tool":
                turns.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id or "",
                                "content": message.content,
                            }
                        ],
                    }
                )
                continue
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                )
            turns.append(
                {"role": message.role, "content": blocks or [{"type": "text", "text": ""}]}
            )

        payload: dict[str, Any] = {
            "model": self.spec.model_id,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": turns,
        }
        if system_parts:
            # One cache breakpoint at the end of the frozen system block.
            payload["system"] = [
                {
                    "type": "text",
                    "text": "\n\n".join(system_parts),
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if request.tools:
            tools = [t.to_anthropic() for t in request.tools]
            tools[-1]["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = tools
        return payload

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = self._payload(request)
        tokens_in = self._count_input(request)
        if self.limiter is not None:
            self.limiter.acquire(tokens_in)

        started = time.perf_counter()
        last_error: str | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._http().post(API_URL, json=payload)
            except httpx.HTTPError as exc:
                last_error = f"transport: {exc}"
                time.sleep(min(8.0, 2.0**attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"http {response.status_code}: {response.text[:200]}"
                time.sleep(float(response.headers.get("retry-after", min(8.0, 2.0**attempt))))
                continue
            if response.status_code >= 400:
                raise ProviderError(f"http {response.status_code}: {response.text[:500]}")
            return self._parse(response.json(), tokens_in, time.perf_counter() - started)
        raise ProviderError(f"anthropic failed after {self.max_retries} tries: {last_error}")

    def _parse(self, body: dict[str, Any], tokens_in: int, latency: float) -> LLMResponse:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in body.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=block.get("id") or str(uuid.uuid4()),
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                    )
                )
        usage = body.get("usage") or {}
        cached = int(usage.get("cache_read_input_tokens") or 0)
        fresh = int(usage.get("input_tokens") or 0)
        return LLMResponse(
            text="\n".join(p for p in text_parts if p),
            tool_calls=calls,
            tokens_in=(fresh + cached) or tokens_in,
            tokens_cached_in=cached,
            tokens_out=int(usage.get("output_tokens") or 0),
            latency_s=latency,
            finish_reason="tool_calls" if calls else (body.get("stop_reason") or "stop"),
            usage_source="reported" if usage else "estimated",
            raw=body,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
