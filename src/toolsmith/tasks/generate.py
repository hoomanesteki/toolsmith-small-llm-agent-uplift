"""Generate the task suite, verify every task, and refuse the invalid ones.

The loop is: draw a template, ask it for a draft, execute the draft's gold
program in a fresh sandbox, and keep it only if the execution agrees with what
the template claimed. Nothing enters the dataset on trust.

Duplicates are dropped by fingerprint rather than by prompt, so two templates
that happen to converge on the same question and the same program count once.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field

from toolsmith.governance.provenance import oracle_provenance
from toolsmith.tasks.models import Task, TaskSuite, Tier
from toolsmith.tasks.oracle import (
    GenerationReport,
    Verdict,
    check_consistency,
    execute_program,
)
from toolsmith.tasks.templates import Ctx, Draft, Template, templates_for
from toolsmith.worlds import WorldSpec, all_worlds, build_world
from toolsmith.worlds.sandbox import WorldBuild

#: Target share of the suite per tier. T4 is over-weighted relative to its
#: apparent importance because abstention and injection resistance are the
#: metrics that decide whether a system is deployable, and almost no public
#: evaluation measures them.
TIER_QUOTA: dict[Tier, float] = {
    "T1": 0.24,
    "T2": 0.30,
    "T3": 0.18,
    "T4": 0.16,
    "T5": 0.12,
}

DEFAULT_TOTAL = 8_000

#: How many attempts a tier may spend per task it is asked to produce.
MAX_ATTEMPTS_PER_TASK = 24

#: Consecutive misses before a template is retired for this tier.
#:
#: Templates have finite unique task spaces. ``t1_policy_fact`` over four tiers
#: and three fields can produce twelve distinct tasks and no more; once they are
#: all drawn, every further attempt is a duplicate. Retiring the exhausted
#: template hands its remaining budget to templates that still have room, which
#: is the difference between filling a quota and stalling at 80% of it.
EXHAUSTION_THRESHOLD = 60


@dataclass
class WorldPlan:
    world: WorldSpec
    build: WorldBuild
    templates: list[Template]
    quota: dict[Tier, int] = field(default_factory=dict)


#: Relative share of each tier's quota, by world role. The primary world gets
#: the largest slice because it is where optimisation happens. The transfer
#: world gets enough for a statistically meaningful comparison and no more,
#: because spending budget there buys nothing: it is never trained on.
ROLE_SHARE: dict[str, float] = {"primary": 0.50, "transfer": 0.28, "grounding": 0.22}


def plan_quotas(
    total: int, worlds: list[WorldSpec], builds: dict[str, WorldBuild]
) -> list[WorldPlan]:
    """Allocate per tier first, then across the worlds that support that tier.

    Allocating per world first starves any tier only one world can produce. T5
    exists solely in the grounding domain, so a world-first split caps it at a
    fraction of that world's slice rather than at its share of the suite: 211
    tasks instead of 960. Tier-first fixes it without special-casing T5, which
    matters because the same problem arrives with any future tier a single
    domain owns.
    """
    available = {w.key: templates_for(w) for w in worlds}
    quotas: dict[str, dict[Tier, int]] = {w.key: {} for w in worlds}

    for tier, tier_share in TIER_QUOTA.items():
        supporting = [w for w in worlds if any(t.tier == tier for t in available[w.key])]
        if not supporting:
            continue
        weight_total = sum(ROLE_SHARE.get(w.role, 0.2) for w in supporting)
        tier_budget = total * tier_share
        for world in supporting:
            weight = ROLE_SHARE.get(world.role, 0.2) / weight_total
            quotas[world.key][tier] = max(1, round(tier_budget * weight))

    return [
        WorldPlan(
            world=world,
            build=builds[world.key],
            templates=available[world.key],
            quota=quotas[world.key],
        )
        for world in worlds
    ]


class TemplatePool:
    """Weighted template sampling that retires exhausted templates.

    Every template has a finite unique task space. ``t1_policy_fact`` over four
    tiers and three fields can produce twelve distinct tasks and no more. Once
    they are all drawn, every further attempt is a duplicate, and a naive
    weighted sampler will keep spending the tier's budget rediscovering them.
    Retiring the template hands that budget to templates that still have room,
    which is the difference between filling a quota and stalling below it.
    """

    def __init__(self, templates: list[Template]) -> None:
        self._templates = list(templates)
        self._misses: dict[str, int] = {}

    @property
    def alive(self) -> bool:
        return bool(self._templates)

    def pick(self, rng: random.Random) -> Template | None:
        if not self._templates:
            return None
        weights = [t.weight for t in self._templates]
        return rng.choices(self._templates, weights=weights, k=1)[0]

    def miss(self, template: Template) -> None:
        count = self._misses.get(template.name, 0) + 1
        self._misses[template.name] = count
        if count >= EXHAUSTION_THRESHOLD:
            self._templates = [t for t in self._templates if t.name != template.name]

    def hit(self, template: Template) -> None:
        self._misses[template.name] = 0

    def retired(self) -> list[str]:
        return sorted(n for n, c in self._misses.items() if c >= EXHAUSTION_THRESHOLD)


def _draft_to_task(
    draft: Draft, template: Template, world: WorldSpec, index: int, seed: int
) -> Task:
    return Task(
        task_id=f"{world.key}-{template.tier}-{index:05d}",
        world=world.key,
        tier=template.tier,
        template=template.name,
        prompt=draft.prompt,
        program=draft.program,
        oracle_answer=draft.answer,
        answer_keys=draft.answer_keys,
        expected_citations=draft.expected_citations,
        is_trap=draft.is_trap,
        trap_kind=draft.trap_kind,
        expected_behaviour=draft.expected_behaviour,  # type: ignore[arg-type]
        injections=draft.injections,
        difficulty=draft.difficulty,
        tags=draft.tags,
        seed=seed,
        provenance=oracle_provenance(seed=seed),
    )


def generate_world(
    plan: WorldPlan, seed: int, report: GenerationReport, seen: set[str] | None = None
) -> list[Task]:
    """Generate, execute and verify this world's share of the suite.

    ``seen`` is shared across worlds on purpose. Some templates are entirely
    domain-independent ("what is today's date in this system?"), and two worlds
    producing that question with the same answer have produced one task twice.
    Letting both through would put an identical pair in train and test, which
    the decontamination gate would correctly call leakage.
    """
    tasks: list[Task] = []
    seen = set() if seen is None else seen
    conn = sqlite3.connect(plan.build.path)
    conn.execute("PRAGMA query_only = ON")
    try:
        for tier, target in sorted(plan.quota.items()):
            index = 0
            attempts = 0
            budget = target * MAX_ATTEMPTS_PER_TASK
            produced = 0
            pool = TemplatePool([t for t in plan.templates if t.tier == tier])
            while produced < target and attempts < budget and pool.alive:
                attempts += 1
                rng = random.Random(f"{seed}:{plan.world.key}:{tier}:{attempts}")
                template = pool.pick(rng)
                if template is None:
                    break

                ctx = Ctx(world=plan.world, conn=conn, rng=rng)
                try:
                    draft = template.fn(ctx)
                except Exception as exc:  # a broken template must not kill the build
                    report.rejected += 1
                    key = f"template {template.name} raised {type(exc).__name__}"
                    report.reasons[key] = report.reasons.get(key, 0) + 1
                    continue
                if draft is None:
                    pool.miss(template)
                    continue

                index += 1
                task = _draft_to_task(draft, template, plan.world, index, seed)
                # Three keys, catching three different ways to produce the same
                # task twice: the exact task, the same question and answer from
                # a different world, and the same gold program reached through
                # different wording.
                keys = (
                    task.fingerprint(),
                    f"{task.content_key()}|{sorted(task.answer_keys)}",
                    f"prog:{task.program_key()}",
                )
                if any(k in seen for k in keys):
                    pool.miss(template)
                    continue

                # Execute once. The recorded ground truth is whatever the gold
                # program actually produced; the checks then assert the task
                # agrees with it.
                outcome = execute_program(task, plan.world, plan.build)
                task.oracle_calls = outcome.calls
                task.oracle_state_diff = outcome.state_signature
                reasons = check_consistency(task, outcome)
                verdict = Verdict(valid=not reasons, reasons=reasons, outcome=outcome)
                report.record(verdict)
                if not verdict.valid:
                    continue

                seen.update(keys)
                pool.hit(template)
                tasks.append(task)
                produced += 1
    finally:
        conn.close()
    return tasks


def generate(
    total: int = DEFAULT_TOTAL,
    seed: int = 20260726,
    world_keys: list[str] | None = None,
) -> tuple[TaskSuite, GenerationReport]:
    worlds = [w for k, w in sorted(all_worlds().items()) if world_keys is None or k in world_keys]
    builds = {w.key: build_world(w) for w in worlds}
    report = GenerationReport()

    tasks: list[Task] = []
    seen: set[str] = set()
    for plan in plan_quotas(total, worlds, builds):
        tasks.extend(generate_world(plan, seed, report, seen))

    suite = TaskSuite(
        tasks=tasks,
        world_digests={k: b.digest for k, b in builds.items()},
        seed=seed,
    )
    return suite, report
