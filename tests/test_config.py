"""The config layer is the project's load-bearing claim. Test it like it."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from toolsmith.config import BudgetPolicy, ModelSpec, load_registry
from toolsmith.config.schema import Capabilities, Registry, RoleAssignment


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def test_registry_loads(registry):
    assert registry.models, "configs/models.yaml produced no models"
    assert registry.pipelines, "configs/pipelines produced no pipelines"
    assert registry.rubrics, "configs/rubrics produced no rubrics"


def test_every_pipeline_references_known_models(registry):
    # Enforced by a model_validator, so simply loading is the assertion. This
    # test exists so the failure has a name when someone breaks it.
    for name, pipe in registry.pipelines.items():
        for key in (pipe.roles.planner, pipe.roles.executor, pipe.roles.reviewer):
            assert key in registry.models, f"{name} references unknown model {key}"


def test_matrix_has_the_rows_the_report_needs(registry):
    """The published Pareto chart is meaningless without its bounds."""
    required = {
        "oracle_ceiling",
        "random_floor",
        "frontier_all_opus",
        "naive_role_split",
        "cascade_default",
    }
    assert required <= set(registry.pipelines)


def test_cost_math_matches_hand_calculation():
    spec = ModelSpec(
        provider="anthropic",
        model_id="x",
        price_in_per_m=5.0,
        price_out_per_m=25.0,
        cache_read_discount=0.90,
    )
    # 1M fresh input, no cache
    assert spec.cost_usd(1_000_000, 0) == pytest.approx(5.0)
    # 1M output
    assert spec.cost_usd(0, 1_000_000) == pytest.approx(25.0)
    # 1M input entirely cached at a 90% discount -> $0.50
    assert spec.cost_usd(1_000_000, 0, cached_in=1_000_000) == pytest.approx(0.50)
    # Half cached
    assert spec.cost_usd(1_000_000, 0, cached_in=500_000) == pytest.approx(2.75)


def test_cached_tokens_cannot_exceed_input():
    spec = ModelSpec(provider="groq", model_id="x", price_in_per_m=1.0, price_out_per_m=1.0)
    # Over-reporting cache should not produce a negative bill.
    assert spec.cost_usd(100, 0, cached_in=1_000) >= 0.0


def test_effective_input_rate_reproduces_the_caching_argument():
    """Section 1.5's claim: caching is what makes a single-vendor pipeline win.

    Solving 5.00*(1-h) + 0.50*h = 1.84 gives h = 0.702. So the spec's effective
    $1.84/M input rate for Opus is exactly a 70% prefix-cache hit rate at a 90%
    discount. That is the number heterogeneous pipelines forfeit: they cannot
    share a KV cache across a provider boundary, so every boundary is a forced
    miss and h collapses toward zero.
    """
    spec = ModelSpec(
        provider="anthropic",
        model_id="x",
        price_in_per_m=5.0,
        price_out_per_m=25.0,
        cache_read_discount=0.90,
    )
    total, cached = 1_000_000, 702_000
    effective = spec.cost_usd(total, 0, cached_in=cached) * 1_000_000 / total
    assert effective == pytest.approx(1.84, abs=0.02)


def test_budget_policy_rejects_incoherent_thresholds():
    with pytest.raises(ValidationError):
        BudgetPolicy(cap_usd=10.0, warn_at_usd=10.0)
    with pytest.raises(ValidationError):
        BudgetPolicy(cap_usd=10.0, warn_at_usd=5.0, per_run_cap_usd=50.0)


def test_unknown_model_error_lists_alternatives(registry):
    with pytest.raises(KeyError) as excinfo:
        registry.model("does-not-exist")
    assert "Known models" in str(excinfo.value)


def test_forbidden_models_are_marked(registry):
    """Anthropic, OpenAI and Google may never be a training source."""
    for key, spec in registry.models.items():
        if spec.provider in {"anthropic", "openai", "google"}:
            assert spec.training_data_use == "forbidden", f"{key} must be inference-only"


def test_training_legal_providers_are_marked(registry):
    for key, spec in registry.models.items():
        if spec.provider in {"groq", "mistral", "together", "mlx"}:
            assert spec.training_data_use != "forbidden", f"{key} should be training-legal"


def test_self_review_pipelines_are_tagged(registry):
    """A pipeline whose reviewer shares the executor's family must say so."""
    for name in registry.assert_reviewer_independence():
        assert (
            "self-review" in registry.pipelines[name].tags
            or "control" in registry.pipelines[name].tags
        ), f"pipeline {name} does self-review without the tag"


def test_recommended_pipeline_uses_a_disjoint_reviewer(registry):
    pipe = registry.pipeline("cascade_default")
    executor = registry.model(pipe.roles.executor)
    reviewer = registry.model(pipe.roles.reviewer)
    assert executor.family != reviewer.family


def test_extra_keys_are_rejected():
    """Typos in YAML must fail loudly, not be silently ignored."""
    with pytest.raises(ValidationError):
        ModelSpec(provider="groq", model_id="x", price_in_per_m=0, price_out_per_m=0, tyop=1)


def test_capabilities_default_to_conservative():
    caps = Capabilities()
    assert not caps.tools and not caps.strict_json


def test_role_assignment_bounds():
    with pytest.raises(ValidationError):
        RoleAssignment(planner="a", executor="b", reviewer="c", max_turns=0)
    with pytest.raises(ValidationError):
        RoleAssignment(planner="a", executor="b", reviewer="c", max_usd_per_task=0)


def test_verified_dates_are_not_in_the_future(registry):
    today = dt.date.today()
    for key, spec in registry.models.items():
        if spec.verified_on:
            assert spec.verified_on <= today, f"{key} claims to be verified in the future"


def test_a_judge_seat_that_names_no_model_fails_at_load():
    """Panels went through no validation while they lived in a Python tuple.

    A typo in a seat used to surface as a KeyError deep inside a matrix run,
    after the worlds were built and several thousand tasks had executed. The
    registry checks pipelines this way and now checks panels the same way, so a
    bad seat is a config error at load like everything else here.
    """
    registry = load_registry()
    with pytest.raises(ValidationError, match="unknown model"):
        Registry(
            models=registry.models,
            pipelines=registry.pipelines,
            panels={"default": ("no-such-model",)},
        )
