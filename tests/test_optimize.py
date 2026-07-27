"""The optimizer tracks.

The tests that matter here are about discipline rather than about outcomes: a
track must tune on validation and report on test, must never claim a gain it
cannot measure, and must publish a null in the same shape as a win.
"""

from __future__ import annotations

import json

import pytest

from toolsmith.config import load_registry
from toolsmith.optimize import TRACKS, read_all, track_a_prompts, track_d_lora
from toolsmith.optimize.base import TrackResult, relative
from toolsmith.optimize.track_b_router import TAU_GRID, _variant
from toolsmith.optimize.track_d_lora import REFUSAL_REGRESSION_LIMIT, safety_gate


def test_every_track_is_registered_in_run_order():
    assert list(TRACKS) == ["c", "b", "a", "d"], (
        "order is C, B, A, D: free first, highest-evidence second, token-cost third, GPU last"
    )


def test_a_track_result_serialises_completely():
    result = TrackResult(
        track="t", title="T", lever="l", verdict="null", headline="nothing happened"
    )
    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["verdict"] == "null"
    assert payload["headline"]


def test_relative_change_is_guarded_against_a_zero_baseline():
    assert relative(1.0, 2.0) == pytest.approx(-0.5)
    assert relative(0.0, 0.0) == 0.0
    assert relative(1.0, 0.0) == float("inf")


# =================================================================== track A ==


def test_track_a_refuses_to_claim_an_accuracy_gain_it_cannot_measure():
    """The simulator does not read prompts, so a prompt gain would be an artifact."""
    result = track_a_prompts.run(provider_mode="simulated")
    assert result.verdict == "unmeasurable"
    assert all(c["accuracy"] is None for c in result.candidates)
    assert any("does not read the prompt" in note or "priors" in note for note in result.notes)


def test_track_a_still_measures_the_real_token_cost():
    result = track_a_prompts.run(provider_mode="simulated")
    per_turn = [c["tokens_per_turn"] for c in result.candidates]
    assert min(per_turn) < max(per_turn), "the candidates must actually differ in length"
    assert result.delta["spread_pct"] > 20


def test_track_a_keeps_the_baseline_rather_than_optimising_the_visible_axis():
    """Choosing the cheapest prompt on cost alone is the mistake this project argues against."""
    result = track_a_prompts.run(provider_mode="simulated")
    assert result.chosen["variant"] == "base"


def test_track_a_registers_its_candidates_as_selectable_bundles():
    from toolsmith.runtime.prompts import BUNDLES, get_bundle

    track_a_prompts.run(provider_mode="simulated")
    assert "terse" in BUNDLES
    assert get_bundle("terse").executor != get_bundle("base").executor


# =================================================================== track B ==


def test_the_tau_variant_changes_exactly_one_thing():
    registry = load_registry()
    base = registry.pipeline("cascade_default")
    variant = _variant(base, 0.85)
    assert variant.roles.confidence_threshold == 0.85
    assert variant.roles.executor == base.roles.executor
    assert variant.roles.escalate_to == base.roles.escalate_to
    assert base.roles.confidence_threshold != 0.85, "the original must not be mutated"


def test_the_tau_grid_spans_never_to_always():
    assert min(TAU_GRID) == 0.0
    assert max(TAU_GRID) == 1.0


# =================================================================== track D ==


def test_track_d_is_a_documented_null_not_an_absence():
    result = track_d_lora.run()
    assert result.verdict == "null"
    assert "Not run" in result.headline
    assert result.chosen["decision"] == "do not fine-tune"
    assert result.chosen["revisit_when"]
    assert len(result.candidates) >= 2, "the alternative it was weighed against must be shown"


def test_the_safety_gate_blocks_an_adapter_that_stopped_refusing():
    verdict = safety_gate(
        refusal_before=92.0, refusal_after=80.0, over_refusal_before=4.0, over_refusal_after=4.0
    )
    assert not verdict.passed
    assert "degraded alignment" in verdict.reason
    assert verdict.to_dict()["refusal_drop_pp"] == 12.0


def test_the_safety_gate_blocks_an_adapter_that_refuses_everything():
    """Guarding one direction only produces the other failure."""
    verdict = safety_gate(
        refusal_before=92.0, refusal_after=93.0, over_refusal_before=4.0, over_refusal_after=40.0
    )
    assert not verdict.passed
    assert "useless" in verdict.reason


def test_the_safety_gate_passes_a_well_behaved_adapter():
    verdict = safety_gate(
        refusal_before=92.0, refusal_after=90.0, over_refusal_before=4.0, over_refusal_after=5.0
    )
    assert verdict.passed
    assert verdict.to_dict()["limit_pp"] == REFUSAL_REGRESSION_LIMIT


def test_track_d_names_the_licence_constraint_on_training_data():
    result = track_d_lora.run()
    licence_note = next(n for n in result.notes if "training row" in n)
    assert "claude-opus-5" not in licence_note, "a forbidden model must not be listed as allowed"
    assert "groq-oss-120b" in licence_note


# ============================================================== the artifacts ==


def test_every_track_has_been_run_and_written():
    written = read_all()
    assert set(written) == {
        "track_a_prompts",
        "track_b_router",
        "track_c_context",
        "track_d_lora",
    }


def test_every_written_track_declares_where_it_tuned_and_reported():
    for name, payload in read_all().items():
        assert payload["tuned_on"], name
        assert payload["reported_on"], name
        assert payload["headline"], name
        assert payload["verdict"] in {"gain", "null", "regression", "unmeasurable"}, name


def test_no_track_tuned_on_the_split_it_reports():
    """The discipline that makes any of these numbers believable."""
    for name, payload in read_all().items():
        tuned, reported = payload["tuned_on"], payload["reported_on"]
        if tuned in {"none (no free parameters)", "n/a"}:
            continue
        assert tuned != reported, f"{name} tuned on the split it reports"


def test_the_routing_track_publishes_what_honesty_cost_it():
    payload = read_all()["track_b_router"]
    assert "left_on_the_table_by_not_tuning_on_test" in payload["delta"]


def test_the_context_track_reports_a_real_token_saving():
    payload = read_all()["track_c_context"]
    assert payload["delta"]["tokens_in_per_task_pct"] < -10, (
        "tool retrieval must actually reduce input tokens, or the claim is wrong"
    )
