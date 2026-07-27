"""Reading and writing the task suite.

JSON Lines, one task per line, sorted by task id. Two reasons for the format:
it streams, and it diffs. A change to one task shows up as one changed line in
review rather than a reformatted blob.

The full suite is generated rather than committed. It is deterministic, so
regenerating takes seconds and a 5 MB file in git history buys nothing. What is
committed is the manifest, the hidden-split seal, and a small readable sample,
so that a reader can see what a task looks like without running anything.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from toolsmith.config import REPO_ROOT
from toolsmith.tasks.models import Task, TaskSuite

DATA_DIR = REPO_ROOT / "data" / "tasks"
TASKS_PATH = DATA_DIR / "tasks.jsonl"
SAMPLE_PATH = DATA_DIR / "sample.jsonl"
MANIFEST_PATH = DATA_DIR / "manifest.json"
SEAL_PATH = DATA_DIR / "hidden_split.sha256"

#: How many tasks go into the committed, human-readable sample.
SAMPLE_SIZE = 90


def write_tasks(tasks: list[Task], path: Path = TASKS_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(tasks, key=lambda t: t.task_id)
    with path.open("w", encoding="utf-8") as fh:
        for task in ordered:
            fh.write(task.model_dump_json(exclude_defaults=False) + "\n")
    return path


def read_tasks(path: Path = TASKS_PATH) -> list[Task]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Run `uv run toolsmith tasks build` to generate it."
        )
    tasks = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                tasks.append(Task.model_validate_json(line))
    return tasks


def write_sample(suite: TaskSuite, path: Path = SAMPLE_PATH, size: int = SAMPLE_SIZE) -> Path:
    """A committed, readable slice: every template, in id order.

    Chosen to cover templates rather than to be random, because the point of the
    sample is that someone can read it and understand the shape of the suite.
    """
    by_template: dict[tuple[str, str], list[Task]] = {}
    for task in sorted(suite.tasks, key=lambda t: t.task_id):
        by_template.setdefault((task.world, task.template), []).append(task)

    picked: list[Task] = []
    per_template = max(1, size // max(1, len(by_template)))
    for tasks in by_template.values():
        picked.extend(tasks[:per_template])
    return write_tasks(picked[:size], path)


def load_split(split: str, path: Path = TASKS_PATH) -> list[Task]:
    return [t for t in read_tasks(path) if t.split == split]


@dataclass(slots=True)
class SuiteSummary:
    """What the suite contains. Printed by the CLI, written to summary.json,
    and read by the report generator."""

    total: int
    by_world: dict[str, dict[str, int]]
    by_tier: dict[str, int]
    by_split: dict[str, int]
    by_template: dict[str, int]
    traps: int
    mutating: int
    with_injections: int
    world_digests: dict[str, str]
    seed: int
    generation: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def summarise(suite: TaskSuite) -> SuiteSummary:
    by_tier: dict[str, int] = {}
    by_split: dict[str, int] = {}
    by_template: dict[str, int] = {}
    traps = 0
    mutating = 0
    injections = 0
    for task in suite.tasks:
        by_tier[task.tier] = by_tier.get(task.tier, 0) + 1
        by_split[task.split] = by_split.get(task.split, 0) + 1
        by_template[task.template] = by_template.get(task.template, 0) + 1
        traps += task.is_trap
        mutating += task.mutating
        injections += bool(task.injections)
    return SuiteSummary(
        total=len(suite.tasks),
        by_world=suite.counts(),
        by_tier=dict(sorted(by_tier.items())),
        by_split=dict(sorted(by_split.items())),
        by_template=dict(sorted(by_template.items())),
        traps=traps,
        mutating=mutating,
        with_injections=injections,
        world_digests=suite.world_digests,
        seed=suite.seed,
    )


def write_summary(summary: SuiteSummary, path: Path | None = None) -> Path:
    path = path or DATA_DIR / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path
