"""Load and validate ``configs/`` into a single :class:`Registry`.

The loader is deliberately boring. It reads YAML, applies defaults, and hands
back a validated object. All of the interesting policy (which model may be a
reviewer, which may produce training data) lives in the schema so that a bad
config fails at load time rather than three hours into an eval run.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml

from toolsmith.config.schema import (
    BudgetPolicy,
    ModelSpec,
    PipelineSpec,
    RateLimit,
    Registry,
    Rubric,
)

#: Repository root, resolved from this file rather than the cwd so that the CLI
#: behaves the same from any directory.
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = Path(os.environ.get("TOOLSMITH_CONFIG_DIR", REPO_ROOT / "configs"))


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a YAML mapping, got {type(data).__name__}")
    return data


def load_models(path: Path | None = None) -> dict[str, ModelSpec]:
    raw = _read_yaml(path or CONFIG_DIR / "models.yaml")
    models: dict[str, ModelSpec] = {}
    for key, body in (raw.get("models") or {}).items():
        models[key] = ModelSpec(key=key, **body)
    return models


def load_pipelines(directory: Path | None = None) -> dict[str, PipelineSpec]:
    directory = directory or CONFIG_DIR / "pipelines"
    pipelines: dict[str, PipelineSpec] = {}
    if not directory.exists():
        return pipelines
    for path in sorted(directory.glob("*.yaml")):
        raw = _read_yaml(path)
        for name, body in (raw.get("pipelines") or {}).items():
            body.setdefault("label", name)
            pipelines[name] = PipelineSpec(name=name, **body)
    return pipelines


def load_limits(path: Path | None = None) -> dict[str, RateLimit]:
    raw = _read_yaml(path or CONFIG_DIR / "limits.yaml")
    return {
        key: RateLimit(
            provider=body.get("provider", key), **{k: v for k, v in body.items() if k != "provider"}
        )
        for key, body in (raw.get("limits") or {}).items()
    }


def load_budget(path: Path | None = None) -> BudgetPolicy:
    raw = _read_yaml(path or CONFIG_DIR / "budget.yaml")
    return BudgetPolicy(**(raw.get("budget") or {}))


def load_rubrics(directory: Path | None = None) -> dict[str, Rubric]:
    directory = directory or CONFIG_DIR / "rubrics"
    rubrics: dict[str, Rubric] = {}
    if not directory.exists():
        return rubrics
    for path in sorted(directory.glob("*.yaml")):
        raw = _read_yaml(path)
        for name, body in (raw.get("rubrics") or {}).items():
            rubrics[name] = Rubric(name=name, **body)
    return rubrics


@functools.lru_cache(maxsize=4)
def load_registry(config_dir: Path | None = None) -> Registry:
    """Load every config file into one validated object.

    Cached, because the registry is read on nearly every code path and parsing
    it repeatedly inside a 5,000-task eval loop is pure waste.
    """
    directory = config_dir or CONFIG_DIR
    return Registry(
        models=load_models(directory / "models.yaml"),
        pipelines=load_pipelines(directory / "pipelines"),
        limits=load_limits(directory / "limits.yaml"),
        budget=load_budget(directory / "budget.yaml"),
        rubrics=load_rubrics(directory / "rubrics"),
    )


def clear_cache() -> None:
    """Drop the memoised registry. Used by tests that write temporary configs."""
    load_registry.cache_clear()
