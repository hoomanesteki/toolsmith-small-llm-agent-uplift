"""Task generation, oracle verification, splits and decontamination.

These tests generate a small suite from scratch rather than reading the
committed one, so they exercise the generator rather than a snapshot of its
output. The committed suite is checked separately by ``toolsmith tasks verify``.
"""

from __future__ import annotations

import pytest

from toolsmith.governance.provenance import oracle_provenance
from toolsmith.tasks import (
    Task,
    assign_splits,
    build_manifest,
    check_leakage,
    generate,
    hidden_digest,
    verify_task,
)
from toolsmith.tasks.decontam import jaccard, ngrams, shingles
from toolsmith.tasks.generate import TIER_QUOTA, TemplatePool, plan_quotas
from toolsmith.tasks.models import OracleStep
from toolsmith.tasks.oracle import execute_program
from toolsmith.tasks.splits import DEFAULT_RATIOS
from toolsmith.tasks.templates import REGISTRY, templates_for
from toolsmith.worlds import Verb, all_worlds, build_world, get_world
from toolsmith.worlds.base import REQUIRED_LEXICON, REQUIRED_SAMPLERS

SMALL = 600


@pytest.fixture(scope="module")
def suite_and_report():
    suite, report = generate(total=SMALL, seed=4242)
    return assign_splits(suite), report


@pytest.fixture(scope="module")
def suite(suite_and_report):
    return suite_and_report[0]


@pytest.fixture(scope="module")
def builds():
    return {k: build_world(w) for k, w in all_worlds().items()}


# ============================================================== the contract ==


@pytest.mark.parametrize("key", sorted(all_worlds()))
def test_every_world_satisfies_the_template_contract(key):
    """A domain must supply the samplers and lexicon templates depend on."""
    world = get_world(key)
    assert set(world.samplers) >= REQUIRED_SAMPLERS
    assert set(world.lexicon) >= REQUIRED_LEXICON
    assert templates_for(world), f"no template applies to {key}"


def test_templates_are_portable_across_worlds():
    """The point of the verb grammar: one template, many domains.

    If a template only ever ran on one world, the transfer measurement would be
    comparing different tasks rather than the same tasks on a different schema.
    """
    shared = [t for t in REGISTRY if t.worlds is None]
    assert shared, "every template is world-locked; the grammar is not being used"
    for template in shared:
        applicable = [w for w in all_worlds().values() if template.applies_to(w)]
        assert len(applicable) >= 2, f"{template.name} only applies to {len(applicable)} world"


def test_every_tier_has_at_least_one_template():
    tiers = {t.tier for t in REGISTRY}
    assert tiers == set(TIER_QUOTA)


# ================================================================ generation ==


def test_generation_produces_a_verified_suite(suite_and_report):
    suite, report = suite_and_report
    assert suite.tasks
    assert report.accepted == len(suite.tasks)
    assert report.rejection_rate < 0.20, "a fifth of drafts failing means a broken template"


def test_every_task_carries_oracle_provenance(suite):
    for task in suite.tasks:
        assert task.provenance.source_type == "oracle_program"
        assert task.provenance.generator_model is None, (
            "no model may generate a task; ground truth is computed"
        )


def test_every_task_has_answer_keys(suite):
    for task in suite.tasks:
        assert task.answer_keys, f"{task.task_id} has no gradeable answer key"
        assert task.oracle_answer.strip()


def test_oracle_programs_reproduce(suite, builds):
    """Re-execute a sample against freshly built worlds."""
    for task in suite.tasks[::7]:
        verdict = verify_task(task, get_world(task.world), builds[task.world])
        assert verdict.valid, f"{task.task_id}: {verdict.reasons}"


def test_read_only_tasks_do_not_mutate_the_world(suite, builds):
    """A right answer reached by writing to the world is not a right answer."""
    for task in suite.tasks:
        if task.tier in {"T1", "T2"} or "read-only" in task.tags:
            assert task.oracle_state_diff == "", f"{task.task_id} mutates state but should not"


def test_traps_expect_something_other_than_an_answer(suite):
    for task in suite.tasks:
        if task.is_trap:
            assert task.expected_behaviour != "answer"
            assert task.trap_kind is not None
        else:
            assert task.expected_behaviour == "answer"


def test_all_four_trap_kinds_are_present(suite):
    kinds = {t.trap_kind for t in suite.tasks if t.is_trap}
    assert kinds == {"unanswerable", "policy_violation", "ambiguous", "injection"}


def test_injection_tasks_have_an_empty_gold_state_diff(suite):
    """The detection mechanism for a followed injection.

    The gold trajectory never performs the privileged action, so an agent that
    obeys the planted instruction produces a state diff where the oracle has
    none. That is caught by execution, not by asking a judge whether the agent
    seemed to comply.
    """
    injected = [t for t in suite.tasks if t.trap_kind == "injection"]
    assert injected
    for task in injected:
        assert task.oracle_state_diff == ""
        assert task.injections
        assert all(i.lure for i in task.injections)


def test_policy_violation_tasks_record_a_refused_step(suite):
    violations = [t for t in suite.tasks if t.trap_kind == "policy_violation"]
    assert violations
    for task in violations:
        assert any(not step.expect_ok for step in task.program)
        assert task.oracle_state_diff == "", "a refused privileged call must not mutate"


def test_grounded_tasks_declare_citations(suite):
    grounded = [t for t in suite.tasks if t.tier == "T5"]
    assert grounded
    for task in grounded:
        assert task.expected_citations
        assert all(c.startswith("SEC-") for c in task.expected_citations)


def test_privileged_writes_actually_change_the_world(suite):
    mutating = [t for t in suite.tasks if t.template == "t3_privileged_write"]
    assert mutating
    for task in mutating:
        assert task.oracle_state_diff, f"{task.task_id} claims to write but changes nothing"


def test_the_injected_run_diverges_from_the_clean_run(suite, builds):
    """The injection must actually reach the model, not merely be declared."""
    task = next(t for t in suite.tasks if t.trap_kind == "injection")
    world = get_world(task.world)
    clean = execute_program(task, world, builds[task.world])
    dirty = execute_program(task, world, builds[task.world], with_injections=True)
    assert clean.ok and dirty.ok
    payload = task.injections[0].payload
    assert not any(payload in str(r["data"]) for r in clean.results)
    assert any(payload in str(r["data"]) for r in dirty.results)


def test_verification_rejects_a_tampered_task(suite, builds):
    task = next(t for t in suite.tasks if t.tier == "T1")
    tampered = task.model_copy(deep=True)
    tampered.answer_keys = ["definitely-not-the-answer"]
    tampered.oracle_state_diff = "pretend-this-mutated-something"
    verdict = verify_task(tampered, get_world(task.world), builds[task.world])
    assert not verdict.valid
    assert any("state diff" in reason for reason in verdict.reasons)


def test_verification_rejects_a_program_that_does_not_run(builds):
    broken = Task(
        task_id="ops-T1-99999",
        world="ops",
        tier="T1",
        template="handwritten",
        prompt="What is the status of order ORD-00000000?",
        program=[OracleStep(verb=Verb.GET_RECORD, arguments={"order_id": "ORD-00000000"})],
        oracle_answer="This will not run.",
        answer_keys=["nothing"],
        provenance=oracle_provenance(seed=1),
    )
    verdict = verify_task(broken, get_world("ops"), builds["ops"])
    assert not verdict.valid


def test_quotas_are_allocated_tier_first(builds):
    """T5 exists in one world only and must still get its share of the suite."""
    worlds = list(all_worlds().values())
    plans = plan_quotas(10_000, worlds, builds)
    t5 = sum(plan.quota.get("T5", 0) for plan in plans)
    assert t5 == pytest.approx(10_000 * TIER_QUOTA["T5"], rel=0.05)


def test_template_pool_retires_an_exhausted_template():
    import random

    from toolsmith.tasks.generate import EXHAUSTION_THRESHOLD

    pool = TemplatePool([t for t in REGISTRY if t.tier == "T1"][:2])
    doomed = pool.pick(random.Random(0))
    assert doomed is not None
    for _ in range(EXHAUSTION_THRESHOLD):
        pool.miss(doomed)
    assert doomed.name in pool.retired()
    for _ in range(200):
        assert pool.pick(random.Random(1)) is not doomed


# ==================================================================== splits ==


def test_splits_hit_their_target_ratios(suite):
    counts = {k: len(v) for k, v in suite.by_split().items()}
    total = sum(counts.values())
    for split, ratio in DEFAULT_RATIOS.items():
        assert counts.get(split, 0) / total == pytest.approx(ratio, abs=0.06)


def test_split_assignment_is_content_addressed(suite):
    """A task keeps its split when the suite is regenerated at a different size.

    Without this, adding tasks silently moves existing ones from train into
    test, and every previously published number becomes uncomparable.
    """
    bigger, _ = generate(total=SMALL * 2, seed=4242)
    bigger = assign_splits(bigger)
    by_fingerprint = {t.fingerprint(): t.split for t in bigger.tasks}
    checked = 0
    for task in suite.tasks:
        if task.fingerprint() in by_fingerprint:
            assert by_fingerprint[task.fingerprint()] == task.split
            checked += 1
    assert checked > 20, "the two suites shared too few tasks to prove anything"


def test_hidden_digest_ignores_task_ids(suite):
    before = hidden_digest(suite.tasks)
    renumbered = [
        t.model_copy(update={"task_id": f"renamed-{i}"}) for i, t in enumerate(suite.tasks)
    ]
    assert hidden_digest(renumbered) == before


def test_hidden_digest_changes_when_a_question_changes(suite):
    before = hidden_digest(suite.tasks)
    tasks = [t.model_copy(deep=True) for t in suite.tasks]
    victim = next(t for t in tasks if t.split == "test_hidden")
    victim.prompt = victim.prompt + " And also tell me something else."
    assert hidden_digest(tasks) != before


def test_manifest_records_world_digests(suite):
    manifest = build_manifest(suite)
    assert set(manifest.world_digests) == set(all_worlds())
    assert manifest.hidden_count > 0
    assert len(manifest.hidden_sha256) == 64


# ============================================================ decontamination ==


def test_generated_suite_has_no_leakage(suite):
    report = check_leakage(suite.tasks)
    assert report.clean, "\n".join(
        f"{c.left} ({c.left_split}) ~ {c.right} ({c.right_split}) j={c.jaccard}"
        for c in report.collisions[:10]
    )


def test_leakage_is_detected_when_it_exists(suite):
    """A copied task in the other split must be caught."""
    tasks = [t.model_copy(deep=True) for t in suite.tasks]
    source = next(t for t in tasks if t.split == "train")
    clone = source.model_copy(deep=True)
    clone.task_id = source.task_id + "-leaked"
    clone.split = "test"
    tasks.append(clone)
    report = check_leakage(tasks)
    assert not report.clean
    assert any(clone.task_id in (c.left, c.right) for c in report.collisions)


def test_different_rows_are_not_leakage(suite):
    """The distinction that makes a generated suite viable at all."""
    tasks = [t.model_copy(deep=True) for t in suite.tasks]
    source = next(t for t in tasks if t.split == "train" and t.program)
    variant = source.model_copy(deep=True)
    variant.task_id = source.task_id + "-other-row"
    variant.split = "test"
    step = variant.program[0]
    key = next(iter(step.arguments), None)
    if key is None:
        pytest.skip("this task takes no arguments")
    step.arguments = {**step.arguments, key: str(step.arguments[key]) + "9"}
    variant.prompt = source.prompt.replace(str(source.program[0].arguments.get(key, "")), "OTHER")
    tasks.append(variant)
    report = check_leakage(tasks)
    assert not any(variant.task_id in (c.left, c.right) for c in report.collisions)


def test_shingles_and_ngrams_behave():
    assert jaccard(shingles("hello world"), shingles("hello world")) == 1.0
    assert jaccard(shingles("hello world"), shingles("zzzz qqqq")) < 0.2
    assert len(ngrams("one two three", size=8)) == 1
