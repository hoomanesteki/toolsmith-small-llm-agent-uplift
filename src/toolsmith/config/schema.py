"""Typed configuration schema.

Every model, price, capability and role assignment in ToolSmith is declared in
YAML and validated here. Nothing about a model is allowed to live in Python
source: swapping ``gpt-oss-20b`` for ``claude-opus-5`` must be a YAML edit, and
``test_no_model_id_is_hardcoded_in_source`` in ``tests/test_governance.py``
proves it.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Provider = Literal[
    "simulated",  # deterministic in-process simulator; costs nothing, spends nothing
    "groq",
    "anthropic",
    "openai",
    "google",
    "mistral",
    "together",
    "mlx",  # local Apple-silicon inference
]

Tier = Literal["local", "cheap", "mid", "frontier", "guard"]

#: Whether a provider's outputs may legally be used as training data.
#: ``forbidden`` entries are rejected by the license firewall (see ``toolsmith ci firewall``).
TrainingDataUse = Literal["allowed", "competing_use_only", "forbidden"]

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]


class Capabilities(BaseModel):
    """What a model can actually do, as opposed to what its marketing says."""

    model_config = ConfigDict(extra="forbid")

    tools: bool = False
    strict_json: bool = False
    parallel_tools: bool = False
    reasoning: bool = False


class SimProfile(BaseModel):
    """Behavioural profile driving the deterministic simulator.

    These are the published, self-reported capability numbers from the model
    cards plus a small number of derived rates. They are *inputs* to the
    simulator, never outputs of ToolSmith: the whole point of the project is
    that you re-measure them yourself. Anything measured here is written to
    ``eval/results/results.jsonl`` with ``provenance`` attached.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["card", "spec", "interpolated"] = "interpolated"
    """Where these priors came from. ``card`` = the model card, ``spec`` = the
    build spec's measured tables, ``interpolated`` = fitted from neighbours.
    The report prints this next to every simulated row so a reader can tell a
    published prior from a guess."""

    tau2: Fraction | None = None
    """Published tau-squared-bench pass rate, if the card reports one."""

    bfcl: float | None = Field(default=None, ge=0.0, le=100.0)
    """Published BFCL-v4 score, if the card reports one."""

    tok_per_s: float = Field(default=60.0, gt=0)
    ttft_s: float = Field(default=1.0, ge=0)

    json_valid_rate: Fraction = 0.99
    tool_select_acc: Fraction = 0.95
    param_acc: Fraction = 0.95
    injection_resist: Fraction = 0.9
    abstain_skill: Fraction = 0.8
    """Probability of correctly abstaining on a T4 trap rather than confabulating."""

    verbosity: float = Field(default=1.0, gt=0)
    """Multiplier on emitted tokens per turn, relative to the 300-token baseline."""


class ModelSpec(BaseModel):
    """One row of ``configs/models.yaml``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = ""
    provider: Provider
    model_id: str
    price_in_per_m: float = Field(ge=0)
    price_out_per_m: float = Field(ge=0)

    cache_read_discount: Fraction = 0.0
    """Fraction of the input price waived on a cache hit. Anthropic 0.90, Groq 0.50."""

    batch_discount: Fraction = 0.0
    """Fraction waived for offline batch submission. Does not stack with caching."""

    cache_ttl_s: int = Field(default=0, ge=0)
    supports: Capabilities = Field(default_factory=Capabilities)
    tier: Tier = "mid"
    family: str = "unknown"
    """Vendor family. The reviewer may never share a family with the executor."""

    training_data_use: TrainingDataUse = "forbidden"
    sim: SimProfile = Field(default_factory=SimProfile)

    verified_on: dt.date | None = None
    retires_on: dt.date | None = None
    notes: str = ""

    @property
    def is_free(self) -> bool:
        return self.price_in_per_m == 0.0 and self.price_out_per_m == 0.0

    def cost_usd(self, tokens_in: int, tokens_out: int, cached_in: int = 0) -> float:
        """Exact spend for one call.

        ``cached_in`` is the number of input tokens served from the prefix cache;
        they are billed at ``(1 - cache_read_discount)`` of the input rate. Groq
        additionally does not count cached tokens against rate limits, which the
        limiter in :mod:`toolsmith.providers.base` models separately.
        """
        fresh_in = max(0, tokens_in - cached_in)
        rate_in = self.price_in_per_m / 1_000_000
        rate_out = self.price_out_per_m / 1_000_000
        cached_rate = rate_in * (1.0 - self.cache_read_discount)
        return fresh_in * rate_in + cached_in * cached_rate + tokens_out * rate_out


class RoleAssignment(BaseModel):
    """Which model runs which stage, plus the escalation policy."""

    model_config = ConfigDict(extra="forbid")

    planner: str
    executor: str
    reviewer: str
    escalate_to: str | None = None
    escalate_on: list[
        Literal["verifier_reject", "schema_invalid", "low_confidence", "budget_exceeded", "never"]
    ] = Field(default_factory=list)

    # `judge_panel`, `guard_injection`, `guard_policy` and `max_wall_clock_s`
    # used to sit here. Nothing read any of them, and because this model forbids
    # extras, a field that exists and is ignored is worse than no field: setting
    # it in YAML produced silence rather than an error, so a pipeline could
    # declare a guard model and quietly not have one. Removing them turns that
    # silence into a validation failure, which is the behaviour the rest of this
    # file is built on. The judge panel now lives in `panels:` under
    # `configs/rubrics/`, next to the rubric it grades against.
    max_turns: int = Field(default=8, ge=1, le=64)
    max_tool_calls: int = Field(default=12, ge=1, le=128)
    max_usd_per_task: float = Field(default=1.0, gt=0)

    tool_exposure: Literal["all_in_prompt", "tool_search"] = "tool_search"
    """Track C's lever. ``all_in_prompt`` is the naive baseline we measure against."""

    compaction_at: Fraction = 0.60
    prompt_variant: str = "base"
    """Selects a compiled prompt bundle from Track A. ``base`` is the hand-written one."""

    confidence_threshold: Fraction = 0.5
    """Track B's tuned tau. Learned on val, reported on test, never tuned on test."""


class PipelineSpec(BaseModel):
    """A named, fully-resolved configuration. One row of the eval matrix."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    label: str = ""
    description: str = ""
    roles: RoleAssignment
    tags: list[str] = Field(default_factory=list)


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cap_usd: float = Field(default=20.0, gt=0)
    warn_at_usd: float = Field(default=12.0, gt=0)
    per_run_cap_usd: float = Field(default=5.0, gt=0)
    stop_on_breach: bool = True

    @model_validator(mode="after")
    def _ordering(self) -> BudgetPolicy:
        if self.warn_at_usd >= self.cap_usd:
            raise ValueError("warn_at_usd must be below cap_usd")
        if self.per_run_cap_usd > self.cap_usd:
            raise ValueError("per_run_cap_usd must not exceed cap_usd")
        return self


class RateLimit(BaseModel):
    """One provider's limits, written by ``toolsmith probe limits``."""

    model_config = ConfigDict(extra="forbid")

    provider: Provider
    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None
    probed_on: dt.date | None = None
    source: Literal["probed", "documented", "assumed"] = "assumed"
    notes: str = ""


class RubricDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    weight: float = Field(default=1.0, ge=0)
    question: str
    anchors: dict[str, str] = Field(default_factory=dict)


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    scale: tuple[int, int] = (1, 5)
    dimensions: list[RubricDimension]

    @property
    def names(self) -> list[str]:
        return [d.name for d in self.dimensions]


class Registry(BaseModel):
    """The fully-loaded configuration surface."""

    model_config = ConfigDict(extra="forbid")

    models: dict[str, ModelSpec]
    pipelines: dict[str, PipelineSpec] = Field(default_factory=dict)
    limits: dict[str, RateLimit] = Field(default_factory=dict)
    budget: BudgetPolicy = Field(default_factory=BudgetPolicy)
    rubrics: dict[str, Rubric] = Field(default_factory=dict)
    panels: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    def model(self, key: str) -> ModelSpec:
        try:
            return self.models[key]
        except KeyError:
            known = ", ".join(sorted(self.models))
            raise KeyError(f"unknown model {key!r}. Known models: {known}") from None

    def pipeline(self, name: str) -> PipelineSpec:
        try:
            return self.pipelines[name]
        except KeyError:
            known = ", ".join(sorted(self.pipelines))
            raise KeyError(f"unknown pipeline {name!r}. Known pipelines: {known}") from None

    @model_validator(mode="after")
    def _pipelines_reference_known_models(self) -> Registry:
        for name, pipe in self.pipelines.items():
            roles = pipe.roles
            referenced = [roles.planner, roles.executor, roles.reviewer]
            if roles.escalate_to is not None:
                referenced.append(roles.escalate_to)
            for key in referenced:
                if key not in self.models:
                    raise ValueError(
                        f"pipeline {name!r} references unknown model {key!r}; "
                        "add it to configs/models.yaml"
                    )

        # Panels name models too. They did not go through here while they were a
        # tuple in Python, so a typo in a judge seat surfaced as a KeyError deep
        # in a matrix run rather than as a config error at load.
        for panel, seats in self.panels.items():
            for key in seats:
                if key not in self.models:
                    raise ValueError(
                        f"judge panel {panel!r} references unknown model {key!r}; "
                        "add it to configs/models.yaml"
                    )
        return self

    def assert_reviewer_independence(self) -> list[str]:
        """Return a list of pipelines where the reviewer shares the executor's family.

        Self-review measurably degrades accuracy (arXiv 2310.01798 reports GPT-4 on
        GSM8K falling 95.5 -> 91.5 -> 89.0 across self-correction rounds), so the
        harness refuses to publish a configuration that does it by accident.
        """
        offenders = []
        for name, pipe in self.pipelines.items():
            ex = self.models[pipe.roles.executor]
            rv = self.models[pipe.roles.reviewer]
            if ex.family == rv.family and ex.family != "unknown":
                offenders.append(name)
        return offenders
