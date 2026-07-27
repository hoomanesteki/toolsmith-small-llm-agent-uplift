"""Where results live, and what makes them regenerable.

``results.jsonl`` is the primary source: one graded run per line. Every number
in the report, the site and the UI is derived from it, and nothing is derived
from anything else. That is what lets CI assert that a fresh run reproduces the
committed artifacts byte for byte, which in turn is what makes "every number is
regenerable" a check rather than a promise.

The manifest records the inputs: the seed, the world digests, the hidden-split
hash, the config, the ledger totals, the git commit. A published number without
its manifest is an anecdote.
"""

from __future__ import annotations

import gzip
import io
import json
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from toolsmith.config import REPO_ROOT
from toolsmith.harness.grading import TaskScore
from toolsmith.harness.judges import PanelVerdict
from toolsmith.harness.matrix import Comparison, PipelineRow

RESULTS_DIR = REPO_ROOT / "eval" / "results"
TRACES_DIR = REPO_ROOT / "eval" / "transcripts"
#: The primary source is gzipped. Eight thousand graded runs is 7.5 MB of JSON
#: and about 600 KB compressed, and a repository that argues for counting costs
#: honestly should not put seven megabytes of its own into every clone. The
#: readers below accept either form, so a plain file dropped in still works.
RESULTS_PATH = RESULTS_DIR / "results.jsonl.gz"
MATRIX_PATH = RESULTS_DIR / "matrix.json"
MANIFEST_PATH = RESULTS_DIR / "manifest.json"
JUDGMENTS_PATH = RESULTS_DIR / "judgments.jsonl.gz"


def write_lines(path: Path, lines: Iterable[str]) -> Path:
    """Write text lines, gzipped when the name says so, byte-reproducibly.

    Two gzip defaults have to be suppressed or the same content produces
    different bytes on every write, and the CI drift check becomes noise people
    learn to ignore:

    * ``mtime`` stamps the current time into the header
    * ``filename`` stamps the output path into the header, so ``a.gz`` and
      ``b.gz`` differ at byte 10 with identical contents
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(lines)
    if path.suffix != ".gz":
        path.write_text(body, encoding="utf-8")
        return path

    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, compresslevel=9, mtime=0) as gz:
        gz.write(body.encode("utf-8"))
    path.write_bytes(buffer.getvalue())
    return path


def _read_text(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    return path.read_text(encoding="utf-8")


def _resolve(path: Path) -> Path:
    """Accept the gzipped or plain form, whichever exists."""
    if path.exists():
        return path
    alternate = (
        path.with_suffix("") if path.suffix == ".gz" else path.with_suffix(path.suffix + ".gz")
    )
    return alternate if alternate.exists() else path


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):  # pragma: no cover - git may be absent
        return ""


@dataclass
class Manifest:
    """Everything needed to reproduce this results file exactly."""

    seed: int
    split: str
    n_tasks: int
    trials: int
    provider_mode: str
    pipelines: list[str]
    world_digests: dict[str, str] = field(default_factory=dict)
    hidden_split_sha256: str = ""
    ledger: dict[str, Any] = field(default_factory=dict)
    substitutions: dict[str, str] = field(default_factory=dict)
    judge_cache_hit_rate: float = 0.0
    wall_clock_s: float = 0.0
    git_commit: str = field(default_factory=lambda: _git("rev-parse", "HEAD"))
    git_dirty: bool = field(default_factory=lambda: bool(_git("status", "--porcelain")))
    toolsmith_version: str = ""
    provenance_note: str = ""

    #: Fields that legitimately differ between two runs of the same command.
    #: They are recorded, but not into the artifact CI diffs, because a file
    #: that changes when nothing changed cannot prove that nothing changed.
    VOLATILE = ("git_commit", "git_dirty", "wall_clock_s", "judge_cache_hit_rate")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def stable_dict(self) -> dict[str, Any]:
        """What goes into matrix.json: inputs only, no timings, no git state."""
        return {k: v for k, v in asdict(self).items() if k not in self.VOLATILE}


def write_results(scores: list[TaskScore], path: Path = RESULTS_PATH) -> Path:
    ordered = sorted(scores, key=lambda s: (s.pipeline, s.task_id, s.trial))
    return write_lines(
        path,
        (json.dumps(s.to_dict(), sort_keys=True, default=str) + "\n" for s in ordered),
    )


def read_results(path: Path = RESULTS_PATH) -> list[TaskScore]:
    path = _resolve(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Run `uv run toolsmith matrix run` to produce it."
        )
    scores = []
    for line in _read_text(path).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row.pop("calls_vs_oracle", None)
        scores.append(TaskScore(**row))
    return scores


def write_judgments(verdicts: list[PanelVerdict], path: Path = JUDGMENTS_PATH) -> Path:
    ordered = sorted(verdicts, key=lambda v: (v.pipeline, v.task_id))
    return write_lines(
        path,
        (json.dumps(v.to_dict(), sort_keys=True, default=str) + "\n" for v in ordered),
    )


def write_matrix(
    rows: list[PipelineRow],
    comparisons: list[Comparison],
    manifest: Manifest,
    extras: dict[str, Any] | None = None,
    path: Path = MATRIX_PATH,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest": manifest.stable_dict(),
        "rows": [r.to_dict() for r in rows],
        "comparisons": [c.to_dict() for c in comparisons],
        **(extras or {}),
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return path


def read_matrix(path: Path = MATRIX_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist. Run `uv run toolsmith matrix run`.")
    return json.loads(path.read_text(encoding="utf-8"))


def write_traces(traces: dict[str, str], directory: Path = TRACES_DIR) -> int:
    """Committed event streams. The zero-key demo reads these.

    Filenames are derived from the run key rather than from a counter, so a
    re-run overwrites the same file and the diff shows what changed instead of
    a wholesale renumbering.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for existing in directory.glob("*.jsonl"):
        existing.unlink()
    for run_id, jsonl in sorted(traces.items()):
        safe = run_id.replace(":", "__").replace("/", "_")
        (directory / f"{safe}.jsonl").write_text(jsonl + "\n", encoding="utf-8")
    return len(traces)


def read_traces(directory: Path = TRACES_DIR) -> dict[str, str]:
    if not directory.exists():
        return {}
    return {
        path.stem.replace("__", ":"): path.read_text(encoding="utf-8")
        for path in sorted(directory.glob("*.jsonl"))
    }


def write_manifest(manifest: Manifest, path: Path = MANIFEST_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return path
