"""Gemini ``generateContent`` adapter.

Gemini sits in this project as a judge seat: family-disjoint from every system
under test, and cheap enough at ``gemini-2.0-flash`` prices to grade a whole
matrix. It is never a training source. Google's terms bar using the Services to
develop competing models, so the license firewall marks every Google model
``training_data_use: forbidden`` and CI fails if one appears on a training row.
"""

from __future__ import annotations

import time
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
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GoogleProvider(Provider):
    provenance = "live"
    name = "google"

    def __init__(
        self,
        spec: ModelSpec,
        limiter: RateLimiter | None = None,
        *,
        timeout: float = 90.0,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(spec, limiter)
        self.timeout = timeout
        self._client = client

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if m.role == "assistant" else "user",
                    "parts": [{"text": m.content or m.content_for_tokens()}],
                }
                for m in request.messages
                if m.role != "system"
            ],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if request.response_format != "text":
            payload["generationConfig"]["responseMimeType"] = "application/json"
        return payload

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = api_key("google")
        if not key:
            raise ProviderError("no GOOGLE_API_KEY. Run with --provider simulated instead.")
        client = self._client or httpx.Client(timeout=self.timeout)

        tokens_in = self._count_input(request)
        if self.limiter is not None:
            self.limiter.acquire(tokens_in)

        started = time.perf_counter()
        response = client.post(
            f"{BASE_URL}/{self.spec.model_id}:generateContent",
            params={"key": key},
            json=self._payload(request),
        )
        if response.status_code >= 400:
            raise ProviderError(f"http {response.status_code}: {response.text[:500]}")

        body = response.json()
        candidate = (body.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts", [])
        usage = body.get("usageMetadata") or {}
        return LLMResponse(
            text="".join(p.get("text", "") for p in parts),
            tokens_in=int(usage.get("promptTokenCount") or tokens_in),
            tokens_cached_in=int(usage.get("cachedContentTokenCount") or 0),
            tokens_out=int(usage.get("candidatesTokenCount") or 0),
            latency_s=time.perf_counter() - started,
            usage_source="reported" if usage else "estimated",
            raw=body,
        )
