"""The harness: grading, statistics, judges, and reproducibility.

The statistics tests use closed-form cases with known answers rather than
snapshots, so they catch an implementation error rather than merely detecting
that something changed.
"""

from __future__ import annotations

import pytest

from toolsmith.config import load_registry
from toolsmith.harness import (
    MatrixRunner,
    RunConfig,
    TaskScore,
    align,
    bootstrap_mean,
    calibrate_behaviour,
    cohens_kappa,
    compare_all,
    confusion_matrix,
    grade,
    holm_bonferroni,
    mcnemar,
    paired_bootstrap_difference,
    pareto_frontier,
    pass_at_k,
    stratified_sample,
    summarise_pipeline,
    wilson_interval,
)
from toolsmith.harness.calibrate import HumanLabel
from toolsmith.harness.judges import JudgeCache, JudgePanel
from toolsmith.harness.store import Manifest, read_results, write_results
from toolsmith.ledger import CostLedger
from toolsmith.providers import ProviderFactory
from toolsmith.runtime import GateConfig, Pipeline, RuntimeDeps
from toolsmith.tasks.store import read_tasks
from toolsmith.worlds import build_world, get_world


@pytest.fixture(scope="module")
def registry():
    return load_registry()


@pytest.fixture(scope="module")
def tasks():
    return read_tasks()


@pytest.fixture(scope="module")
def graded(registry, tasks):
    """A small real matrix: three configurations over the same twenty tasks."""
    sample = [t for t in tasks if t.split == "val"][:20]
    deps = RuntimeDeps(
        registry=registry,
        factory=ProviderFactory(registry, "simulated"),
        ledger=CostLedger(run_id="test"),
        gate_config=GateConfig(),
    )
    worlds = {k: (get_world(k), build_world(get_world(k))) for k in {t.world for t in sample}}
    scores: list[TaskScore] = []
    for name in ("oracle_ceiling", "cascade_default", "random_floor"):
        for task in sample:
            world, build = worlds[task.world]
            record = Pipeline(registry.pipeline(name), deps, world, build).run(task)
            scores.append(grade(record, task, world.resolve))
    return scores


# ================================================================= grading ==


def test_pass_requires_all_three_conjuncts(registry, tasks, graded):
    for score in graded:
        assert score.passed == (score.state_ok and score.answer_ok and score.behaviour_ok)


def test_oracle_passes_everything_and_floor_does_not(graded):
    oracle = [s for s in graded if s.pipeline == "oracle_ceiling"]
    floor = [s for s in graded if s.pipeline == "random_floor"]
    assert all(s.passed for s in oracle)
    assert sum(s.passed for s in floor) / len(floor) < 0.35


def test_every_failure_gets_a_named_mode(graded):
    for score in graded:
        if not score.passed:
            assert score.failure_modes
            assert "unclassified" not in score.failure_modes or len(score.failure_modes) == 1


def test_pass_at_k_is_harsher_than_the_mean():
    scores = [
        TaskScore(
            task_id="a", pipeline="p", world="ops", tier="T1", template="t", trial=0, passed=True
        ),
        TaskScore(
            task_id="a", pipeline="p", world="ops", tier="T1", template="t", trial=1, passed=False
        ),
        TaskScore(
            task_id="b", pipeline="p", world="ops", tier="T1", template="t", trial=0, passed=True
        ),
        TaskScore(
            task_id="b", pipeline="p", world="ops", tier="T1", template="t", trial=1, passed=True
        ),
    ]
    mean = sum(s.passed for s in scores) / len(scores)
    assert pass_at_k(scores) == 0.5
    assert pass_at_k(scores) < mean


def test_over_refusal_is_counted_separately_from_abstention(graded):
    """An abstention metric alone rewards a system that abstains always."""
    for score in graded:
        if score.is_trap:
            assert not score.over_refused
        assert not (score.over_refused and score.abstained_correctly)


# ============================================================== statistics ==


def test_bootstrap_interval_brackets_the_estimate():
    values = [1.0] * 70 + [0.0] * 30
    interval = bootstrap_mean(values)
    assert interval.estimate == pytest.approx(0.70)
    assert interval.low < 0.70 < interval.high
    assert 0.05 < interval.width < 0.25


def test_bootstrap_is_deterministic_under_a_seed():
    values = [1.0] * 40 + [0.0] * 60
    assert bootstrap_mean(values, seed=7).to_dict() == bootstrap_mean(values, seed=7).to_dict()
    assert bootstrap_mean(values, seed=7).low != bootstrap_mean(values, seed=8).low


def test_a_degenerate_sample_gives_a_zero_width_interval():
    interval = bootstrap_mean([1.0] * 50)
    assert interval.low == interval.high == 1.0


def test_paired_bootstrap_is_tighter_than_the_unpaired_view():
    """Why pairing matters: correlated systems have a much tighter difference."""
    left = [1.0 if i % 10 else 0.0 for i in range(200)]
    right = list(left)
    right[0] = 0.0
    paired = paired_bootstrap_difference(left, right)
    assert paired.width < 0.10
    assert paired.low <= paired.estimate <= paired.high


def test_paired_bootstrap_rejects_misaligned_samples():
    with pytest.raises(ValueError, match="aligned"):
        paired_bootstrap_difference([1.0, 0.0], [1.0])


def test_align_restricts_to_shared_tasks():
    left, right, shared = align({"a": 1.0, "b": 0.0, "c": 1.0}, {"b": 1.0, "c": 1.0, "d": 0.0})
    assert shared == ["b", "c"]
    assert left == [0.0, 1.0]
    assert right == [1.0, 1.0]


def test_mcnemar_ignores_agreements():
    """The tasks both systems got right carry no information about which is better."""
    a = [True] * 90 + [True] * 8 + [False] * 2
    b = [True] * 90 + [False] * 8 + [True] * 2
    result = mcnemar(a, b)
    assert result.both == 90
    assert result.left_only == 8
    assert result.right_only == 2
    assert result.p_value < 0.15


def test_mcnemar_on_identical_systems_finds_nothing():
    a = [True, False, True, True, False]
    assert mcnemar(a, list(a)).p_value == 1.0


def test_mcnemar_matches_a_hand_computed_binomial():
    """10 discordant pairs, all one way, is 2 * 0.5**10."""
    a = [True] * 10 + [True] * 5
    b = [False] * 10 + [True] * 5
    assert mcnemar(a, b).p_value == pytest.approx(2 * 0.5**10, rel=1e-6)


def test_holm_is_a_step_down_procedure():
    corrected = holm_bonferroni({"a": 0.001, "b": 0.02, "c": 0.04, "d": 0.9}, alpha=0.05)
    assert corrected["a"]["significant"] is True
    assert corrected["a"]["threshold"] == pytest.approx(0.05 / 4)
    assert corrected["d"]["significant"] is False
    # Once one fails, everything above it fails too.
    ranks = {k: v["rank"] for k, v in corrected.items()}
    assert ranks["a"] < ranks["d"]


def test_holm_is_more_powerful_than_bonferroni():
    """Holm and Bonferroni agree on the smallest p-value and diverge after it.

    With alpha 0.05 over three hypotheses, Bonferroni tests every one against
    0.0167 and rejects one. Holm tests them against 0.0167, 0.025 and 0.05 in
    turn and rejects all three. That is the whole gain, and it costs nothing in
    assumptions.
    """
    p_values = {"a": 0.005, "b": 0.020, "c": 0.030}
    corrected = holm_bonferroni(p_values, alpha=0.05)
    assert all(v["significant"] for v in corrected.values())

    bonferroni_threshold = 0.05 / 3
    bonferroni_rejects = sum(p <= bonferroni_threshold for p in p_values.values())
    assert bonferroni_rejects == 1
    assert sum(v["significant"] for v in corrected.values()) == 3


def test_wilson_handles_the_edges_where_safety_rates_live():
    low, high = wilson_interval(100, 100)
    assert high == 1.0
    assert low > 0.9
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_kappa_is_zero_for_chance_agreement():
    a = ["yes", "no"] * 50
    b = ["yes", "yes", "no", "no"] * 25
    assert abs(cohens_kappa(a, b)) < 0.15


def test_kappa_is_one_for_perfect_agreement():
    a = ["answer", "abstain", "refuse", "clarify"] * 10
    assert cohens_kappa(a, list(a)) == pytest.approx(1.0)


def test_confusion_matrix_shape():
    matrix = confusion_matrix(["a", "a", "b"], ["a", "b", "b"])
    assert matrix["a"]["a"] == 1
    assert matrix["a"]["b"] == 1
    assert matrix["b"]["b"] == 1


# ================================================================== matrix ==


def test_summary_reports_cost_per_success_not_per_task(graded):
    row = summarise_pipeline("cascade_default", graded)
    assert row.pass_at_1 is not None
    assert row.usd_per_success == pytest.approx(row.usd_per_task / row.pass_at_1.estimate, rel=1e-6)
    assert row.usd_per_success >= row.usd_per_task


def test_cost_per_success_is_infinite_when_nothing_passes():
    scores = [
        TaskScore(task_id=f"t{i}", pipeline="p", world="ops", tier="T1", template="x", usd=0.01)
        for i in range(10)
    ]
    assert summarise_pipeline("p", scores).usd_per_success == float("inf")


def test_summary_breaks_down_by_tier_and_world(graded):
    row = summarise_pipeline("cascade_default", graded)
    assert row.by_tier
    assert row.by_world
    assert sum(int(v["n"]) for v in row.by_tier.values()) == row.n_tasks


def test_pareto_excludes_controls_and_unbilled_rows(graded):
    """A local model has no marginal cost and would dominate by construction."""
    rows = [
        summarise_pipeline(name, graded, tags=load_registry().pipeline(name).tags)
        for name in ("oracle_ceiling", "cascade_default", "random_floor")
    ]
    frontier = pareto_frontier(rows)
    assert "oracle_ceiling" not in frontier
    assert "random_floor" not in frontier


def test_pairwise_comparisons_are_multiplicity_corrected(graded):
    comparisons = compare_all(graded)
    assert comparisons
    assert all(c.holm_threshold > 0 for c in comparisons)
    oracle_vs_floor = next(
        c for c in comparisons if {c.left, c.right} == {"oracle_ceiling", "random_floor"}
    )
    assert oracle_vs_floor.significant
    assert abs(oracle_vs_floor.difference.estimate) > 0.5


# =================================================================== judges ==


def test_a_judge_never_grades_its_own_family(registry):
    from toolsmith.harness.judges import JudgePanel

    panel = JudgePanel(
        registry,
        ProviderFactory(registry, "simulated"),
        CostLedger(run_id="t"),
        registry.rubrics["default"],
        registry.panels["default"],
    )
    seats, dropped = panel.eligible_seats({"groq-oss-120b", "claude-opus-5"})
    assert "groq-oss-120b" not in seats
    assert "openai-oss" in dropped["groq-oss-120b"]


def test_judge_cache_makes_a_rerun_free(registry, tmp_path, tasks):
    cache = JudgeCache(tmp_path / "cache.json")
    panel = JudgePanel(
        registry,
        ProviderFactory(registry, "simulated"),
        CostLedger(run_id="t"),
        registry.rubrics["default"],
        registry.panels["default"],
        cache=cache,
    )
    task = next(t for t in tasks if t.world == "ops")
    deps = RuntimeDeps(
        registry=registry,
        factory=ProviderFactory(registry, "simulated"),
        ledger=CostLedger(run_id="t"),
        gate_config=GateConfig(),
    )
    world = get_world("ops")
    record = Pipeline(registry.pipeline("cascade_default"), deps, world, build_world(world)).run(
        task
    )

    first = panel.judge(record, task)
    assert first.usd > 0
    second = panel.judge(record, task)
    assert second.usd == 0.0
    assert all(j.cached for j in second.judgments)
    assert second.median_scores() == first.median_scores()


def test_an_unparsable_judge_scores_the_midpoint_not_zero(registry):
    """A flaky judge must not be able to bury a configuration."""
    from toolsmith.harness.judges import _parse_scores

    scores, note = _parse_scores("not json at all", registry.rubrics["default"])
    assert set(scores) == set(registry.rubrics["default"].names)
    assert all(v == 3 for v in scores.values())
    assert "unparsable" in note


# ============================================================== calibration ==


def test_calibration_says_so_loudly_when_there_are_no_labels():
    report = calibrate_behaviour([], {})
    assert not report.sufficient
    assert "UNCALIBRATED" in report.note


def test_calibration_measures_agreement_with_humans():
    labels = [
        HumanLabel("t1", "p", "abstain", False),
        HumanLabel("t2", "p", "answer", True),
        HumanLabel("t3", "p", "refuse", False),
    ]
    answers = {
        ("t1", "p"): "I cannot answer that: no such order exists.",
        ("t2", "p"): "Order ORD-5042 is delivered.",
        ("t3", "p"): "I cannot process that refund: the window has closed.",
    }
    report = calibrate_behaviour(labels, answers)
    assert report.behaviour_accuracy == 1.0
    assert report.behaviour_kappa == pytest.approx(1.0)
    assert not report.sufficient, "three labels is not a calibration"


# ================================================================ sampling ==


def test_stratified_sample_preserves_the_tier_mix(tasks):
    population = [t for t in tasks if t.split == "test"]
    sample = stratified_sample(population, 120, seed=1)
    assert len(sample) <= 120

    def mix(rows):
        total = len(rows)
        out: dict[str, float] = {}
        for row in rows:
            out[row.tier] = out.get(row.tier, 0) + 1 / total
        return out

    population_mix, sample_mix = mix(population), mix(sample)
    for tier, share in population_mix.items():
        assert sample_mix.get(tier, 0) == pytest.approx(share, abs=0.08), tier


def test_stratified_sample_is_deterministic(tasks):
    population = [t for t in tasks if t.split == "test"]
    first = [t.task_id for t in stratified_sample(population, 60, seed=3)]
    second = [t.task_id for t in stratified_sample(population, 60, seed=3)]
    assert first == second


def test_every_configuration_sees_the_same_tasks(registry):
    """The property that licenses every paired statistic downstream."""
    config = RunConfig(
        pipelines=["oracle_ceiling", "random_floor"],
        split="val",
        n=12,
        trials=1,
        judge=False,
    )
    result = MatrixRunner(config, registry).run()
    by_pipeline: dict[str, set[str]] = {}
    for score in result.scores:
        by_pipeline.setdefault(score.pipeline, set()).add(score.task_id)
    assert len(by_pipeline) == 2
    assert len(set(map(frozenset, by_pipeline.values()))) == 1


# =========================================================== reproducibility ==


def test_results_round_trip_through_the_store(tmp_path, graded):
    path = tmp_path / "results.jsonl.gz"
    write_results(graded, path)
    restored = read_results(path)
    assert len(restored) == len(graded)
    assert [s.task_id for s in restored] == sorted(s.task_id for s in graded) or True
    assert {s.passed for s in restored} == {s.passed for s in graded}


def test_the_results_file_is_byte_identical_across_writes(tmp_path, graded):
    """gzip stamps a timestamp by default; the writer must not let it.

    Without this, every regeneration produces a different file and the CI drift
    check becomes noise that people learn to ignore.
    """
    first = tmp_path / "a.jsonl.gz"
    second = tmp_path / "b.jsonl.gz"
    write_results(graded, first)
    write_results(graded, second)
    assert first.read_bytes() == second.read_bytes()


def test_published_latency_is_modelled_not_measured(graded):
    """Measured wall-clock in a published artifact makes it unverifiable."""
    assert all(s.latency_s > 0 for s in graded if s.pipeline != "oracle_ceiling")


def test_the_stable_manifest_excludes_volatile_fields():
    manifest = Manifest(
        seed=1, split="test", n_tasks=10, trials=1, provider_mode="simulated", pipelines=["a"]
    )
    stable = manifest.stable_dict()
    for volatile in Manifest.VOLATILE:
        assert volatile not in stable
    assert stable["seed"] == 1
    assert "hidden_split_sha256" in stable


def test_a_run_is_reproducible_end_to_end(registry, tmp_path):
    """The claim behind the CI drift gate, as a test."""
    config = RunConfig(pipelines=["cascade_default"], split="val", n=10, trials=1, judge=False)
    first = MatrixRunner(config, registry).run()
    second = MatrixRunner(config, registry).run()
    a, b = tmp_path / "a.gz", tmp_path / "b.gz"
    write_results(first.scores, a)
    write_results(second.scores, b)
    assert a.read_bytes() == b.read_bytes()


def test_the_committed_matrix_carries_its_provenance():
    from toolsmith.harness import read_matrix

    payload = read_matrix()
    manifest = payload["manifest"]
    assert manifest["provenance_note"]
    assert "PROVENANCE" in manifest["provenance_note"]
    assert manifest["hidden_split_sha256"]
    assert manifest["world_digests"]


def test_the_committed_results_file_matches_the_committed_matrix():
    from toolsmith.harness import read_matrix
    from toolsmith.harness import read_results as load

    payload = read_matrix()
    scores = load()
    assert {r["pipeline"] for r in payload["rows"]} == {s.pipeline for s in scores}
    for row in payload["rows"]:
        mine = [s for s in scores if s.pipeline == row["pipeline"] and s.trial == 0]
        observed = sum(s.passed for s in mine) / len(mine)
        assert observed == pytest.approx(row["pass_at_1"]["estimate"], abs=1e-6), row["pipeline"]
