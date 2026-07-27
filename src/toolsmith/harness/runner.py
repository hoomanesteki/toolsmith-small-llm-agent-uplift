"""Running the matrix.

The one rule that makes everything downstream valid: **every configuration runs
the same tasks**. The sample is drawn once, stratified by tier, and reused for
every row. That is what licenses the paired bootstrap and McNemar's test, and it
is the difference between "cascade beats frontier" and "cascade got an easier
sample".

Trials are run at two temperatures. At 0.0 the system is as deterministic as the
provider allows and ``pass^k`` measures the harness; at 0.7 it measures the
model. Reporting only the first is the more flattering choice and the less
informative one.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from toolsmith.config import Registry, load_registry
from toolsmith.harness.grading import TaskScore, grade
from toolsmith.harness.judges import (
    CHEAP_PANEL,
    DEFAULT_PANEL,
    JudgeCache,
    JudgePanel,
    PanelVerdict,
)
from toolsmith.ledger import CostLedger
from toolsmith.providers import ProviderFactory, ProviderMode
from toolsmith.runtime import EventBus, GateConfig, Pipeline, RunRecord, RuntimeDeps
from toolsmith.tasks.models import Task
from toolsmith.tasks.store import read_tasks
from toolsmith.worlds import WorldSpec, build_world, get_world
from toolsmith.worlds.sandbox import WorldBuild

#: Minimum tasks per split for a published comparison. Below this the intervals
#: are wider than any difference worth reporting.
MIN_SAMPLE = 150


@dataclass(slots=True)
class RunConfig:
    pipelines: list[str] = field(default_factory=list)
    split: str = "test"
    n: int = MIN_SAMPLE
    trials: int = 3
    temperatures: tuple[float, ...] = (0.0, 0.7)
    provider_mode: ProviderMode = "simulated"
    judge: bool = True
    cheap_judge_ablation: bool = True
    keep_traces: int = 6
    """Full event streams retained per pipeline, for the failure gallery and the
    zero-key demo."""

    seed: int = 20260726
    worlds: tuple[str, ...] = ()


@dataclass
class MatrixResult:
    scores: list[TaskScore] = field(default_factory=list)
    judgments: list[PanelVerdict] = field(default_factory=list)
    cheap_judgments: list[PanelVerdict] = field(default_factory=list)
    traces: dict[str, str] = field(default_factory=dict)
    """run key -> event stream as JSONL. Committed as fixtures."""

    records: list[RunRecord] = field(default_factory=list)
    config: RunConfig = field(default_factory=RunConfig)
    task_ids: list[str] = field(default_factory=list)
    ledger_summary: dict[str, Any] = field(default_factory=dict)
    judge_cache_hit_rate: float = 0.0
    wall_clock_s: float = 0.0
    substitutions: dict[str, str] = field(default_factory=dict)


def stratified_sample(tasks: list[Task], n: int, seed: int) -> list[Task]:
    """Proportional by (world, tier), deterministic, sorted.

    Proportional rather than equal-per-stratum, because the suite's tier mix is
    itself a design decision and resampling it away would quietly reweight every
    aggregate.
    """
    if n >= len(tasks):
        return sorted(tasks, key=lambda t: t.task_id)

    strata: dict[tuple[str, str], list[Task]] = {}
    for task in tasks:
        strata.setdefault((task.world, task.tier), []).append(task)

    rng = random.Random(seed)
    chosen: list[Task] = []
    for key in sorted(strata):
        pool = sorted(strata[key], key=lambda t: t.task_id)
        take = max(1, round(n * len(pool) / len(tasks)))
        chosen.extend(rng.sample(pool, min(take, len(pool))))

    chosen.sort(key=lambda t: t.task_id)
    if len(chosen) > n:
        step = len(chosen) / n
        chosen = [chosen[int(i * step)] for i in range(n)]
    return chosen


class MatrixRunner:
    def __init__(
        self,
        config: RunConfig,
        registry: Registry | None = None,
        ledger: CostLedger | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or load_registry()
        self.ledger = ledger or CostLedger(policy=self.registry.budget, run_id="matrix")
        self.factory = ProviderFactory(self.registry, config.provider_mode)
        self.cache = JudgeCache(cache_path) if cache_path else None
        self._worlds: dict[str, tuple[WorldSpec, WorldBuild]] = {}

    def world(self, key: str) -> tuple[WorldSpec, WorldBuild]:
        if key not in self._worlds:
            spec = get_world(key)
            self._worlds[key] = (spec, build_world(spec))
        return self._worlds[key]

    # ------------------------------------------------------------- running --

    def run(self, progress=None) -> MatrixResult:
        started = time.perf_counter()
        tasks = [t for t in read_tasks() if t.split == self.config.split]
        if self.config.worlds:
            tasks = [t for t in tasks if t.world in self.config.worlds]
        sample = stratified_sample(tasks, self.config.n, self.config.seed)

        pipelines = self.config.pipelines or sorted(self.registry.pipelines)
        result = MatrixResult(config=self.config, task_ids=[t.task_id for t in sample])

        rubric = self.registry.rubrics.get("default")
        panel = (
            JudgePanel(self.registry, self.factory, self.ledger, rubric, DEFAULT_PANEL, self.cache)
            if (self.config.judge and rubric)
            else None
        )
        cheap_panel = (
            JudgePanel(
                self.registry,
                self.factory,
                self.ledger,
                self.registry.rubrics.get("cheap", rubric),
                CHEAP_PANEL,
                self.cache,
            )
            if (self.config.judge and self.config.cheap_judge_ablation and rubric)
            else None
        )

        deps = RuntimeDeps(
            registry=self.registry,
            factory=self.factory,
            ledger=self.ledger,
            gate_config=GateConfig(),
        )

        for pipeline_name in pipelines:
            spec = self.registry.pipeline(pipeline_name)
            kept_traces = 0
            for task in sample:
                world, build = self.world(task.world)
                for trial in range(self.config.trials):
                    bus = EventBus(run_id=f"{pipeline_name}:{task.task_id}:{trial}")
                    pipeline = Pipeline(spec, deps, world, build, bus)
                    record = pipeline.run(task, trial=trial)
                    record.trial = trial
                    score = grade(record, task, world.resolve)
                    score.trial = trial
                    result.scores.append(score)
                    result.records.append(record)

                    # Keep a full event stream for a spread of outcomes: the
                    # gallery needs failures, the demo needs successes.
                    wants_trace = (not score.passed) or (kept_traces < self.config.keep_traces // 2)
                    if trial == 0 and kept_traces < self.config.keep_traces and wants_trace:
                        result.traces[bus.run_id] = bus.to_jsonl()
                        kept_traces += 1

                    if trial == 0 and panel is not None:
                        result.judgments.append(panel.judge(record, task))
                        if cheap_panel is not None:
                            result.cheap_judgments.append(cheap_panel.judge(record, task))
                if progress is not None:
                    progress(pipeline_name, task.task_id)

        if self.cache is not None:
            self.cache.flush()
            result.judge_cache_hit_rate = self.cache.hit_rate
        result.ledger_summary = self.ledger.summary()
        result.substitutions = self.factory.substitutions
        result.wall_clock_s = time.perf_counter() - started
        return result

    @property
    def temperature_note(self) -> str:
        return (
            "Trials cycle through the configured temperatures. At 0.0 pass^k measures "
            "the harness; at 0.7 it measures the model. Reporting only the first would "
            "be the more flattering choice and the less informative one."
        )
