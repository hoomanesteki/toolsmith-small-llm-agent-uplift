"""Check ``configs/models.yaml`` against what the providers actually serve today.

The build spec this project came from documented five model retirements in four
months and two more scheduled inside three weeks. Hardcoding a model id or a
price from a document is how a repository quietly starts publishing numbers for
a model that no longer exists.

So: nothing is hardcoded from prose. This probe hits each provider's model-list
endpoint, diffs it against the registry, and writes a drift report. It is safe
to run with no keys, in which case it reports only what it could check.

Run::

    uv run toolsmith probe models            # report drift
    uv run toolsmith probe models --write     # also write configs/generated/
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from toolsmith.config import CONFIG_DIR, Registry, load_registry
from toolsmith.env import api_key

#: Model-list endpoints, per provider. Google and MLX are handled separately.
LIST_ENDPOINTS: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai/v1/models", "bearer"),
    "openai": ("https://api.openai.com/v1/models", "bearer"),
    "mistral": ("https://api.mistral.ai/v1/models", "bearer"),
    "together": ("https://api.together.xyz/v1/models", "bearer"),
    "google": ("https://generativelanguage.googleapis.com/v1beta/models", "query"),
    "anthropic": ("https://api.anthropic.com/v1/models", "x-api-key"),
}

RETIREMENT_WARNING_DAYS = 30


@dataclass
class Drift:
    """One discrepancy between the registry and reality."""

    severity: str  # "error" | "warn" | "info"
    model_key: str
    message: str


@dataclass
class ProbeReport:
    probed_on: dt.date
    checked: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    catalogue: dict[str, list[str]] = field(default_factory=dict)
    drift: list[Drift] = field(default_factory=list)

    @property
    def errors(self) -> list[Drift]:
        return [d for d in self.drift if d.severity == "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "probed_on": self.probed_on.isoformat(),
            "checked": self.checked,
            "skipped": self.skipped,
            "catalogue": self.catalogue,
            "drift": [d.__dict__ for d in self.drift],
        }


def _list_models(provider: str, timeout: float = 20.0) -> list[str]:
    url, auth = LIST_ENDPOINTS[provider]
    key = api_key(provider)
    if not key:
        raise PermissionError(f"no key for {provider}")
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if auth == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif auth == "x-api-key":
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    else:
        params["key"] = key

    response = httpx.get(url, headers=headers, params=params, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    rows = body.get("data") or body.get("models") or []
    ids = []
    for row in rows:
        ident = row.get("id") or row.get("name") or ""
        ids.append(ident.removeprefix("models/"))
    return sorted(i for i in ids if i)


def probe(registry: Registry | None = None, *, today: dt.date | None = None) -> ProbeReport:
    registry = registry or load_registry()
    today = today or dt.date.today()
    report = ProbeReport(probed_on=today)

    providers = sorted({s.provider for s in registry.models.values()} & set(LIST_ENDPOINTS))
    for provider in providers:
        try:
            report.catalogue[provider] = _list_models(provider)
            report.checked.append(provider)
        except PermissionError:
            report.skipped[provider] = "no API key"
        except httpx.HTTPError as exc:
            report.skipped[provider] = f"request failed: {exc}"

    for key, spec in sorted(registry.models.items()):
        # 1. Does the model id still exist in the provider's catalogue?
        served = report.catalogue.get(spec.provider)
        if served is not None and spec.model_id not in served:
            report.drift.append(
                Drift(
                    "error",
                    key,
                    f"model_id {spec.model_id!r} is not in {spec.provider}'s live catalogue. "
                    f"Nearest: {', '.join(_nearest(spec.model_id, served)) or 'none'}",
                )
            )

        # 2. Is it verified at all?
        if spec.verified_on is None:
            report.drift.append(
                Drift(
                    "warn", key, "verified_on is null: price and id are UNVERIFIED, do not publish"
                )
            )
        elif (today - spec.verified_on).days > 90:
            report.drift.append(
                Drift("warn", key, f"last verified {(today - spec.verified_on).days} days ago")
            )

        # 3. Is it about to die?
        if spec.retires_on is not None:
            days = (spec.retires_on - today).days
            if days < 0:
                report.drift.append(Drift("error", key, f"RETIRED on {spec.retires_on}"))
            elif days <= RETIREMENT_WARNING_DAYS:
                report.drift.append(
                    Drift("warn", key, f"retires in {days} days ({spec.retires_on})")
                )

    return report


def _nearest(target: str, candidates: list[str], n: int = 3) -> list[str]:
    """Cheap suggestion list, for the common case of a renamed or versioned id."""
    stem = target.split("/")[-1].split("-")[0].lower()
    return [c for c in candidates if stem in c.lower()][:n]


def write_report(report: ProbeReport, directory: Path | None = None) -> Path:
    directory = directory or CONFIG_DIR / "generated"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "models_probe.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
