"""The lineage DAG: every published number traces back to its inputs.

``lineage.jsonl`` is append-only. Each line is one node and its parents, so the
whole graph is

    world seed -> task -> oracle -> trajectory -> gate verdict -> run -> metric

Reading it backwards from a cell in the results table tells you which task
produced it, which oracle program defined its ground truth, which seed built
the database that oracle ran against, and which config was in force.

**Status: the structure exists and the pipeline does not yet write to it.** The
log, the traversal and the append-only guarantee are implemented and tested; no
production code path emits a node. Saying so here rather than leaving the
docstring to imply otherwise, because a governance claim you cannot check is
exactly what the rest of this package exists to avoid. Every fact the DAG would
carry is currently recoverable from the provenance record on each row, which is
written; the DAG would make the walk a lookup rather than a join.

Append-only matters. A lineage file you can rewrite is a lineage file that
proves nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from toolsmith.config import REPO_ROOT

NodeKind = Literal[
    "world",
    "task",
    "oracle",
    "split",
    "config",
    "run",
    "trajectory",
    "gate",
    "judgment",
    "metric",
    "artifact",
]

LINEAGE_PATH = REPO_ROOT / "eval" / "lineage.jsonl"


@dataclass(slots=True)
class LineageNode:
    node_id: str
    kind: NodeKind
    label: str
    parents: list[str] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))

    def to_json(self) -> str:
        return json.dumps(
            {
                "node_id": self.node_id,
                "kind": self.kind,
                "label": self.label,
                "parents": self.parents,
                "attrs": self.attrs,
                "ts": self.ts,
            },
            sort_keys=True,
            default=str,
        )


class LineageLog:
    """Append-only writer, safe across threads."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or LINEAGE_PATH
        self._lock = threading.Lock()

    def emit(
        self,
        node_id: str,
        kind: NodeKind,
        label: str,
        parents: list[str] | None = None,
        **attrs: Any,
    ) -> LineageNode:
        node = LineageNode(node_id, kind, label, parents or [], attrs)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(node.to_json() + "\n")
        return node

    def read(self) -> list[LineageNode]:
        if not self.path.exists():
            return []
        nodes = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            nodes.append(
                LineageNode(
                    node_id=row["node_id"],
                    kind=row["kind"],
                    label=row["label"],
                    parents=row.get("parents", []),
                    attrs=row.get("attrs", {}),
                    ts=row.get("ts", ""),
                )
            )
        return nodes

    def ancestors(self, node_id: str) -> list[LineageNode]:
        """Everything a node depends on, breadth-first. The 'why is this number
        what it is' query."""
        by_id = {n.node_id: n for n in self.read()}
        seen: set[str] = set()
        frontier = [node_id]
        out: list[LineageNode] = []
        while frontier:
            current = frontier.pop(0)
            if current in seen:
                continue
            seen.add(current)
            node = by_id.get(current)
            if node is None:
                continue
            out.append(node)
            frontier.extend(node.parents)
        return out

    def as_graph(self) -> dict[str, Any]:
        """Node and edge lists, shaped for the UI's DAG renderer."""
        nodes = self.read()
        return {
            "nodes": [
                {"id": n.node_id, "kind": n.kind, "label": n.label, "attrs": n.attrs} for n in nodes
            ],
            "edges": [
                {"source": parent, "target": n.node_id} for n in nodes for parent in n.parents
            ],
        }
