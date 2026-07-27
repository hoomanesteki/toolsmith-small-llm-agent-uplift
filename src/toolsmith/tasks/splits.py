"""Splits, and the hidden split's seal.

The split is stratified by (world, tier, template) so that no configuration can
win by being lucky about which templates landed in test. Assignment is by a hash
of the task fingerprint rather than by shuffling, which means a task keeps its
split when the suite is regenerated at a different size: adding tasks does not
silently move existing ones from train into test.

The hidden split exists to answer one attack: "you tuned on your test set". Its
SHA-256 is committed to git *before* any optimisation run, so the timestamp on
the commit is the proof. ``toolsmith ci hidden-split`` re-derives it on every
build and fails if a single byte moved.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from toolsmith.tasks.models import Split, Task, TaskSuite

#: train / val / test / test_hidden. The hidden slice is carved out of test so
#: that the visible test split stays usable for iteration without touching it.
DEFAULT_RATIOS: dict[Split, float] = {
    "train": 0.70,
    "val": 0.15,
    "test": 0.09,
    "test_hidden": 0.06,
}


def _bucket(fingerprint: str) -> float:
    """Deterministic uniform in [0, 1) from a task's content."""
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:8]
    return int(digest, 16) / 0xFFFFFFFF


def assign_splits(
    suite: TaskSuite, ratios: dict[Split, float] | None = None, salt: str = "toolsmith-v3"
) -> TaskSuite:
    """Stratified, content-addressed split assignment.

    Stratification happens by construction: the bucket is drawn per task, and
    because tasks are grouped by (world, tier, template) before assignment, each
    stratum gets the same proportions rather than the same tasks.
    """
    ratios = ratios or DEFAULT_RATIOS
    total = sum(ratios.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"split ratios must sum to 1.0, got {total}")

    order: tuple[Split, ...] = ("train", "val", "test", "test_hidden")
    edges: list[tuple[Split, float]] = []
    running = 0.0
    for split in order:
        running += ratios[split]
        edges.append((split, running))

    strata: dict[tuple[str, str, str], list[Task]] = {}
    for task in suite.tasks:
        strata.setdefault((task.world, task.tier, task.template), []).append(task)

    for stratum in strata.values():
        stratum.sort(key=lambda t: t.task_id)
        for task in stratum:
            position = _bucket(salt + task.fingerprint())
            for split, edge in edges:
                if position < edge:
                    task.split = split
                    break
            else:  # pragma: no cover - position < 1.0 always matches
                task.split = "test_hidden"
    return suite


# ------------------------------------------------------------------ sealing --


@dataclass(slots=True)
class SplitManifest:
    hidden_sha256: str
    hidden_count: int
    counts: dict[str, int]
    world_digests: dict[str, str]
    seed: int

    def to_dict(self) -> dict[str, object]:
        return {
            "hidden_sha256": self.hidden_sha256,
            "hidden_count": self.hidden_count,
            "counts": self.counts,
            "world_digests": self.world_digests,
            "seed": self.seed,
            "note": (
                "hidden_sha256 is committed before any optimisation run. Check the git "
                "history of this file: its timestamp predates every entry in "
                "eval/results/results.jsonl."
            ),
        }


def hidden_digest(tasks: list[Task]) -> str:
    """SHA-256 over the hidden split's content, in a canonical order.

    Hashes the prompt, the program and the answer, not the ids. Renumbering the
    suite must not change the seal; changing a question must.
    """
    hidden = sorted((t for t in tasks if t.split == "test_hidden"), key=lambda t: t.fingerprint())
    hasher = hashlib.sha256()
    for task in hidden:
        payload = {
            "world": task.world,
            "tier": task.tier,
            "prompt": task.content_key(),
            "program": [(s.verb.value, s.arguments, s.expect_ok) for s in task.program],
            "answer_keys": sorted(task.answer_keys),
            "state_diff": task.oracle_state_diff,
            "behaviour": task.expected_behaviour,
        }
        hasher.update(json.dumps(payload, sort_keys=True, default=str).encode() + b"\n")
    return hasher.hexdigest()


def build_manifest(suite: TaskSuite) -> SplitManifest:
    counts: dict[str, int] = {}
    for task in suite.tasks:
        counts[task.split] = counts.get(task.split, 0) + 1
    return SplitManifest(
        hidden_sha256=hidden_digest(suite.tasks),
        hidden_count=counts.get("test_hidden", 0),
        counts=counts,
        world_digests=suite.world_digests,
        seed=suite.seed,
    )


def write_manifest(manifest: SplitManifest, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def seal_hidden_split(suite: TaskSuite, path: Path) -> Path:
    """Write the bare hash. This is the file whose git history is the evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(hidden_digest(suite.tasks) + "\n", encoding="utf-8")
    return path


def verify_hidden_split(dataset: Path, seal: Path) -> tuple[bool, str, str]:
    """Re-derive the seal from the dataset on disk. Backs the CI gate."""
    from toolsmith.tasks.store import read_tasks

    expected = seal.read_text(encoding="utf-8").strip()
    actual = hidden_digest(read_tasks(dataset))
    return expected == actual, expected, actual
