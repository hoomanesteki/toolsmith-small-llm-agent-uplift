"""Four ways to get better, measured on one axis.

The thesis of this phase is that you do not know which lever wins until you put
them all on the same chart. Prompt compilation, routing, context engineering and
fine-tuning are usually compared by anecdote, in different papers, on different
benchmarks. Here they are four rows in one table, produced by one harness.

The ordering is deliberate: **C, then B, then A, then D**. Do the free one
first, the highest-evidence one second, the token-cost one third, and the GPU
one last and only if time remains. A track that shows no gain is a published
result, not a failure, and this file's job is to make that outcome as easy to
report as a win.

Every track tunes on **val** and reports on **test**. Never the other way round.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from toolsmith.config import REPO_ROOT

OPTIMIZE_DIR = REPO_ROOT / "eval" / "optimize"

Verdict = Literal["gain", "null", "regression", "unmeasurable"]


@dataclass
class TrackResult:
    """What one optimisation track found, including nothing."""

    track: str
    title: str
    lever: str
    verdict: Verdict
    headline: str
    """One sentence a reader can quote."""

    tuned_on: str = "val"
    reported_on: str = "test"
    baseline: dict[str, float] = field(default_factory=dict)
    optimised: dict[str, float] = field(default_factory=dict)
    delta: dict[str, float] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    chosen: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)
    provenance: str = "simulated"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, directory: Path | None = None) -> Path:
        directory = directory or OPTIMIZE_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.track}.json"
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return path


def read_track(track: str, directory: Path | None = None) -> dict[str, Any] | None:
    path = (directory or OPTIMIZE_DIR) / f"{track}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_all(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    directory = directory or OPTIMIZE_DIR
    if not directory.exists():
        return {}
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("track_*.json"))
    }


def relative(new: float, old: float) -> float:
    """Signed relative change, guarded against a zero baseline."""
    if old == 0:
        return 0.0 if new == 0 else float("inf")
    return (new - old) / abs(old)
