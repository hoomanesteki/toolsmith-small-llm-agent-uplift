"""Resolve a model key to a live client or the simulator.

One switch, three modes:

``simulated``  every model becomes :class:`SimulatedProvider`. Costs nothing,
               needs no keys, and is what CI and the committed fixtures use.
``live``       every model must have a working key, or the run fails loudly
               rather than silently degrading to fake numbers.
``auto``       live where a key exists, simulated where it does not, with the
               substitution recorded on every affected row so a mixed run can
               never be mistaken for a live one.

``auto`` is the mode that makes partial keys useful: hold only a Groq key and
the open-weight rows are real while the frontier rows stay simulated, and the
report says so on the row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from toolsmith.config import ModelSpec, Registry
from toolsmith.env import available_providers
from toolsmith.providers.base import Provider, RateLimiter
from toolsmith.providers.simulated import SimulatedProvider

ProviderMode = Literal["auto", "simulated", "live"]


class ProviderFactory:
    """Builds and caches one provider instance per model key.

    Caching matters for more than speed: the rate limiter and the HTTP
    connection pool are per-instance, so a fresh client per call would defeat
    both.
    """

    def __init__(self, registry: Registry, mode: ProviderMode = "simulated") -> None:
        self.registry = registry
        self.mode = mode
        self._cache: dict[str, Provider] = {}
        self._limiters: dict[str, RateLimiter] = {}
        self._substitutions: dict[str, str] = {}

    # ------------------------------------------------------------ resolving --

    def _limiter(self, provider: str) -> RateLimiter | None:
        limit = self.registry.limits.get(provider)
        if limit is None:
            return None
        if provider not in self._limiters:
            self._limiters[provider] = RateLimiter(limit)
        return self._limiters[provider]

    def _build_live(self, spec: ModelSpec) -> Provider:
        limiter = self._limiter(spec.provider)
        match spec.provider:
            case "groq" | "openai" | "mistral" | "together":
                from toolsmith.providers.openai_compat import OpenAICompatProvider

                return OpenAICompatProvider(spec, limiter)
            case "anthropic":
                from toolsmith.providers.anthropic import AnthropicProvider

                return AnthropicProvider(spec, limiter)
            case "google":
                from toolsmith.providers.google import GoogleProvider

                return GoogleProvider(spec, limiter)
            case "mlx":
                from toolsmith.providers.mlx import MLXProvider

                return MLXProvider(spec, limiter)
            case "simulated":
                return SimulatedProvider(spec, limiter)
            case _:  # pragma: no cover - the schema forbids it
                raise ValueError(f"no adapter for provider {spec.provider!r}")

    def get(self, model_key: str) -> Provider:
        if model_key in self._cache:
            return self._cache[model_key]

        spec = self.registry.model(model_key)
        keys = available_providers()

        if self.mode == "simulated" or spec.provider == "simulated":
            provider: Provider = SimulatedProvider(spec, self._limiter(spec.provider))
        elif self.mode == "live":
            if not keys.get(spec.provider, False):
                raise RuntimeError(
                    f"--provider live but no key for {spec.provider!r} "
                    f"(needed by model {model_key!r}). Add it to .env or use --provider auto."
                )
            provider = self._build_live(spec)
        else:  # auto
            if keys.get(spec.provider, False):
                provider = self._build_live(spec)
            else:
                provider = SimulatedProvider(spec, self._limiter(spec.provider))
                self._substitutions[model_key] = f"no {spec.provider} key: simulated"

        self._cache[model_key] = provider
        return provider

    # ----------------------------------------------------------- reporting --

    @property
    def substitutions(self) -> dict[str, str]:
        """Models that silently fell back to simulation in ``auto`` mode."""
        return dict(self._substitutions)

    def provenance_for(self, model_key: str) -> str:
        return self.get(model_key).provenance

    def close(self) -> None:
        for provider in self._cache.values():
            closer = getattr(provider, "close", None)
            if callable(closer):
                closer()
        self._cache.clear()


@dataclass(slots=True)
class ProviderStatus:
    provider: str
    key_present: bool
    models: list[str]
    limits: dict[str, Any] | None


@dataclass(slots=True)
class Availability:
    """What this machine could actually run right now. Backs ``toolsmith doctor``."""

    providers: list[ProviderStatus]
    live_capable: list[str]
    budget_cap_usd: float


def describe_availability(registry: Registry) -> Availability:
    keys = available_providers()
    by_provider: dict[str, list[str]] = {}
    for key, spec in sorted(registry.models.items()):
        by_provider.setdefault(spec.provider, []).append(key)

    statuses = []
    for provider, models in sorted(by_provider.items()):
        limit = registry.limits.get(provider)
        statuses.append(
            ProviderStatus(
                provider=provider,
                key_present=keys.get(provider, False),
                models=models,
                limits=limit.model_dump(mode="json") if limit is not None else None,
            )
        )
    return Availability(
        providers=statuses,
        live_capable=sorted(p for p, ok in keys.items() if ok and p != "simulated"),
        budget_cap_usd=registry.budget.cap_usd,
    )
