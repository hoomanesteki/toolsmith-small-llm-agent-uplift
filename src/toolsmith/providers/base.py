"""Provider-neutral request and response types.

Everything above this module speaks only in :class:`LLMRequest` and
:class:`LLMResponse`. Nothing above it knows what an Anthropic content block or
an OpenAI tool_calls array looks like. That boundary is what lets a pipeline
YAML swap a model without touching Python.

Token accounting is done here rather than trusting each provider's usage field,
so that cost comparisons across providers use one consistent ruler. Where a
provider does report usage, the live adapter overwrites the estimate and the
ledger records which one it used.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from toolsmith.config import ModelSpec, RateLimit

Role = Literal["system", "user", "assistant", "tool"]


# --------------------------------------------------------------- tokenising --

_WORDISH = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Deterministic, provider-neutral token estimate.

    A real BPE tokeniser would be more accurate and would also make cost
    comparisons depend on which vendor's tokeniser you happened to pick. We use
    one ruler for every provider: word-ish pieces, with a correction for the
    fact that JSON and identifiers fragment more than prose. Empirically within
    about 8% of tiktoken on this project's traffic, and identically wrong for
    every model, which is what matters for a comparison.
    """
    if not text:
        return 0
    pieces = _WORDISH.findall(text)
    # Long alphanumeric runs (ids, hashes, snake_case) split further.
    extra = sum(len(p) // 6 for p in pieces if len(p) > 6)
    return max(1, len(pieces) + extra)


def estimate_message_tokens(messages: list[Message]) -> int:
    # +4 per message for role framing, matching the usual chat-format overhead.
    return sum(estimate_tokens(m.content_for_tokens()) + 4 for m in messages)


# ----------------------------------------------------------------- messages --


@dataclass(slots=True)
class ToolCall:
    """A model's request to run one tool."""

    id: str
    name: str
    arguments: dict[str, Any]

    def signature(self) -> str:
        """Stable identity used for oracle comparison and dedupe."""
        return f"{self.name}({json.dumps(self.arguments, sort_keys=True, default=str)})"


@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def content_for_tokens(self) -> str:
        parts = [self.content]
        for call in self.tool_calls:
            parts.append(call.signature())
        return "\n".join(p for p in parts if p)


@dataclass(slots=True)
class ToolSchema:
    """A JSON-schema tool definition, provider-neutral."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass(slots=True)
class LLMRequest:
    messages: list[Message]
    tools: list[ToolSchema] = field(default_factory=list)
    temperature: float = 0.0
    max_tokens: int = 1024
    seed: int = 0
    response_format: Literal["text", "json_object", "json_schema"] = "text"
    json_schema: dict[str, Any] | None = None
    stop: list[str] = field(default_factory=list)

    meta: dict[str, Any] = field(default_factory=dict)
    """Out-of-band context (task_id, role, turn). Live adapters ignore it; the
    simulator uses it to look up ground truth. Never sent over the wire."""

    def cache_key(self) -> str:
        payload = {
            "messages": [(m.role, m.content_for_tokens(), m.tool_call_id) for m in self.messages],
            "tools": sorted(t.name for t in self.tools),
            "temperature": self.temperature,
            "seed": self.seed,
            "response_format": self.response_format,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def prefix_hash(self, n_messages: int = 2) -> str:
        """Hash of the stable prefix: system prompt, skills, tool list.

        Prefix caching only pays if this hash is identical across turns. The
        executor loop asserts it, because a mutated prefix silently costs the
        50-90% cache discount and, on Groq, also stops the tokens being free
        against the rate limit.
        """
        head = self.messages[:n_messages]
        blob = json.dumps(
            [(m.role, m.content_for_tokens()) for m in head] + sorted(t.name for t in self.tools),
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass(slots=True)
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_cached_in: int = 0
    tokens_out: int = 0
    latency_s: float = 0.0
    finish_reason: Literal["stop", "tool_calls", "length", "error", "filtered"] = "stop"
    usage_source: Literal["estimated", "reported"] = "estimated"
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    def as_message(self) -> Message:
        return Message(role="assistant", content=self.text, tool_calls=list(self.tool_calls))


# ------------------------------------------------------------ rate limiting --


class RateLimiter:
    """Sliding-window limiter over requests and tokens.

    Models the two limits that actually bind in practice: requests per minute
    and tokens per day. Groq's free tier is bounded by TPD, roughly 80 real
    agent turns, so the limiter reports headroom rather than silently blocking.
    """

    def __init__(self, limit: RateLimit) -> None:
        self.limit = limit
        self._requests: deque[float] = deque()
        self._tokens_today = 0
        self._day_start = time.time()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._requests and now - self._requests[0] > 60.0:
            self._requests.popleft()
        if now - self._day_start > 86400:
            self._day_start = now
            self._tokens_today = 0

    def headroom(self) -> dict[str, int | None]:
        with self._lock:
            self._prune(time.time())
            return {
                "requests_this_minute": len(self._requests),
                "rpm": self.limit.rpm,
                "tokens_today": self._tokens_today,
                "tpd": self.limit.tpd,
            }

    def acquire(self, tokens: int, billable_tokens: int | None = None) -> float:
        """Block until the call fits. Returns seconds slept.

        ``billable_tokens`` defaults to ``tokens``. Pass a smaller number for
        cache hits: on Groq, cached tokens do not count against rate limits,
        which is the single most exploitable fact on the free tier.
        """
        billable = tokens if billable_tokens is None else billable_tokens
        slept = 0.0
        while True:
            with self._lock:
                now = time.time()
                self._prune(now)
                rpm_ok = self.limit.rpm is None or len(self._requests) < self.limit.rpm
                tpd_ok = self.limit.tpd is None or self._tokens_today + billable <= self.limit.tpd
                if rpm_ok and tpd_ok:
                    self._requests.append(now)
                    self._tokens_today += billable
                    return slept
                if not tpd_ok:
                    raise RuntimeError(
                        f"{self.limit.provider} daily token budget exhausted "
                        f"({self._tokens_today}/{self.limit.tpd}). "
                        "Use --provider simulated, or wait for the window to roll."
                    )
                wait = 60.0 - (now - self._requests[0]) + 0.05
            time.sleep(max(0.0, wait))
            slept += max(0.0, wait)


# ------------------------------------------------------------------ provider --


class ProviderError(RuntimeError):
    """A provider call failed in a way the caller must handle."""


class Provider(ABC):
    """One vendor's chat-completions endpoint, normalised."""

    name: str = "abstract"
    provenance: Literal["simulated", "live"] = "live"

    def __init__(self, spec: ModelSpec, limiter: RateLimiter | None = None) -> None:
        self.spec = spec
        self.limiter = limiter

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Run one completion. Implementations must not raise for model-level
        failures (bad JSON, refusal); they set ``finish_reason`` instead.
        Transport failures raise :class:`ProviderError`."""

    # -- shared helpers ------------------------------------------------------

    def _count_input(self, request: LLMRequest) -> int:
        tools = sum(estimate_tokens(json.dumps(t.to_openai())) for t in request.tools)
        return estimate_message_tokens(request.messages) + tools

    def _simulate_latency(self, tokens_out: int) -> float:
        return self.spec.sim.ttft_s + tokens_out / self.spec.sim.tok_per_s

    @staticmethod
    def _seeded(*parts: Any) -> int:
        blob = "|".join(str(p) for p in parts)
        return int(hashlib.sha256(blob.encode()).hexdigest()[:12], 16)


def cache_hit_tokens(prev_prefix: str | None, request: LLMRequest, prefix_tokens: int) -> int:
    """How many input tokens are served from cache on this call.

    Returns the full prefix length when the stable prefix is byte-identical to
    the previous turn's, and zero otherwise. There is no partial credit: a
    single mutated character at position 3 of the system prompt invalidates
    everything after it. This is why the runtime freezes prefix ordering.
    """
    if prev_prefix is None:
        return 0
    return prefix_tokens if prev_prefix == request.prefix_hash() else 0


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
