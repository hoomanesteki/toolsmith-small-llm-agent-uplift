"""The sandbox: where tool calls actually run and state changes are observed.

Three properties make this the load-bearing piece of the evaluation.

**Isolation.** Every task gets a private in-memory copy of the world, restored
from a byte-identical file. Task 900 cannot see the refund task 12 issued. This
is what makes ``pass^k`` meaningful and what lets the whole matrix run in
parallel.

**Server-side authorisation.** A privileged tool is checked by the world's
policy function *before* it executes. The model asks; code decides. This is the
difference between a demo and a system, and it is the line worth saying out
loud: never trust model-side policy, gate every privileged call server-side.

**Injection as a first-class fixture.** Indirect prompt injections are planted
in tool results and retrieved documents by the sandbox, not by the prompt. That
is where they occur in production, and it is the step most evaluations skip.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from toolsmith.config import REPO_ROOT
from toolsmith.worlds.base import (
    PolicyDecision,
    StateDiff,
    ToolResult,
    ToolSpec,
    WorldSpec,
    db_digest,
    diff_snapshots,
    snapshot,
    table_names,
)

BUILD_DIR = REPO_ROOT / "build" / "worlds"


# ------------------------------------------------------------------ building --


@dataclass
class WorldBuild:
    """The result of materialising a world at a given seed."""

    key: str
    seed: int
    path: Path
    digest: str
    row_counts: dict[str, int]
    _image: bytes | None = None
    """The serialised database, cached on first use.

    A task run needs a private copy of the world, and the obvious way to get
    one is to reopen the file and back it up into memory. At three worlds by
    eight thousand tasks that is twenty-four thousand file opens. Serialising
    once and deserialising per task is roughly an order of magnitude faster and
    is byte-identical, which the digest test asserts.
    """

    def image(self) -> bytes:
        if self._image is None:
            source = sqlite3.connect(self.path)
            try:
                self._image = source.serialize()
            finally:
                source.close()
        return self._image

    @property
    def total_rows(self) -> int:
        return sum(self.row_counts.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "seed": self.seed,
            "path": str(self.path),
            "digest": self.digest,
            "row_counts": self.row_counts,
            "total_rows": self.total_rows,
        }


def build_world(
    world: WorldSpec, seed: int | None = None, directory: Path | None = None
) -> WorldBuild:
    """Create ``<key>.sqlite3`` from the schema and the seeded builder.

    Deterministic by construction: the builder is handed a ``random.Random``
    derived only from ``seed``, and every date is an offset from ``BASE_DATE``.
    Build twice, get the same digest, or ``test_worlds.py`` fails.
    """
    seed = world.default_seed if seed is None else seed
    directory = directory or BUILD_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{world.key}-{seed}.sqlite3"
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(path)
    try:
        conn.executescript(world.schema_sql)
        world.seed(conn, seed)
        conn.commit()
        digest = db_digest(conn)
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in table_names(conn)
        }
    finally:
        conn.close()
    return WorldBuild(world.key, seed, path, digest, counts)


# ---------------------------------------------------------------- injections --


@dataclass(slots=True)
class Injection:
    """An instruction planted in data, to be encountered as a tool result.

    This is the realistic threat: not a user typing "ignore your instructions",
    but a sentence sitting inside a document or a customer note that the agent
    retrieves and then obeys. The correct behaviour is to complete the original
    task, ignore the instruction, and flag it.
    """

    tool: str
    payload: str
    match: dict[str, Any] = field(default_factory=dict)
    field_name: str = "notes"
    kind: str = "instruction_in_data"

    def applies_to(self, tool_name: str, args: dict[str, Any]) -> bool:
        if tool_name != self.tool:
            return False
        return all(str(args.get(k)) == str(v) for k, v in self.match.items())


def _plant(data: Any, payload: str, field_name: str) -> Any:
    """Attach the payload where a model will read it as content."""
    if isinstance(data, dict):
        existing = data.get(field_name)
        merged = f"{existing}\n\n{payload}" if existing else payload
        return {**data, field_name: merged}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        head = _plant(data[0], payload, field_name)
        return [head, *data[1:]]
    return {"value": data, field_name: payload}


# ------------------------------------------------------------------- calling --


@dataclass(slots=True)
class CallRecord:
    """One executed tool call, with everything the audit record needs."""

    index: int
    tool: str
    arguments: dict[str, Any]
    result: ToolResult
    policy: PolicyDecision | None = None
    injected: bool = False
    latency_ms: float = 0.0

    def signature(self) -> str:
        return f"{self.tool}({json.dumps(self.arguments, sort_keys=True, default=str)})"


class SandboxError(RuntimeError):
    pass


class Sandbox:
    """A private, mutable copy of a world for the lifetime of one task."""

    def __init__(
        self,
        world: WorldSpec,
        build: WorldBuild,
        injections: list[Injection] | None = None,
        max_calls: int = 64,
    ) -> None:
        self.world = world
        self.build = build
        self.injections = injections or []
        self.max_calls = max_calls
        self.calls: list[CallRecord] = []
        self._conn: sqlite3.Connection | None = None
        self._before: dict[str, dict[str, dict[str, Any]]] | None = None

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self) -> Sandbox:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> Sandbox:
        self._conn = sqlite3.connect(":memory:")
        self._conn.deserialize(self.build.image())
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._before = snapshot(self._conn)
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise SandboxError("sandbox is not open; use `with Sandbox(...) as sb:`")
        return self._conn

    # -- execution ----------------------------------------------------------

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Run one tool call. Never raises for a bad request: returns ``ok=False``."""
        args = dict(arguments or {})
        index = len(self.calls)
        if index >= self.max_calls:
            result = ToolResult.failure(
                "budget_exhausted", f"tool-call budget of {self.max_calls} spent"
            )
            self.calls.append(CallRecord(index, tool_name, args, result))
            return result

        try:
            tool: ToolSpec = self.world.tool(tool_name)
        except KeyError:
            result = ToolResult.failure(
                "unknown_tool",
                f"no tool named {tool_name!r} in {self.world.key}. "
                f"Available: {', '.join(self.world.tool_names)}",
            )
            self.calls.append(CallRecord(index, tool_name, args, result))
            return result

        invalid = validate_arguments(tool, args)
        if invalid is not None:
            result = ToolResult.failure("invalid_arguments", invalid)
            self.calls.append(CallRecord(index, tool_name, args, result))
            return result

        # Privileged calls are authorised by code, after the model has asked.
        decision: PolicyDecision | None = None
        if tool.privileged:
            if self.world.policy is None:  # pragma: no cover - WorldSpec forbids it
                raise SandboxError(f"{self.world.key} has a privileged tool and no policy")
            decision = self.world.policy(self.conn, tool.name, args)
            if not decision.allowed:
                result = ToolResult.failure(decision.code, decision.reason)
                self.calls.append(CallRecord(index, tool_name, args, result, policy=decision))
                return result

        result = tool.handler(self.conn, args)
        if tool.mutating and result.ok:
            self.conn.commit()
            result.mutated = True

        injected = False
        if result.ok:
            for injection in self.injections:
                if injection.applies_to(tool.name, args):
                    result.data = _plant(result.data, injection.payload, injection.field_name)
                    injected = True

        self.calls.append(
            CallRecord(index, tool_name, args, result, policy=decision, injected=injected)
        )
        return result

    # -- observation --------------------------------------------------------

    def state_diff(self) -> StateDiff:
        if self._before is None:
            raise SandboxError("sandbox is not open")
        return diff_snapshots(self._before, snapshot(self.conn))

    def digest(self) -> str:
        return db_digest(self.conn)

    def call_signatures(self) -> list[str]:
        return [c.signature() for c in self.calls]

    def mutating_calls(self) -> list[CallRecord]:
        return [c for c in self.calls if c.result.mutated]

    def denied_calls(self) -> list[CallRecord]:
        return [c for c in self.calls if c.policy is not None and not c.policy.allowed]

    def transcript(self) -> list[dict[str, Any]]:
        """The audit record for this task: every call, verdict and result."""
        return [
            {
                "index": c.index,
                "tool": c.tool,
                "arguments": c.arguments,
                "ok": c.result.ok,
                "error_code": c.result.error_code,
                "mutated": c.result.mutated,
                "injected": c.injected,
                "policy": None
                if c.policy is None
                else {
                    "allowed": c.policy.allowed,
                    "code": c.policy.code,
                    "reason": c.policy.reason,
                },
                "result": c.result.to_json()[:2000],
            }
            for c in self.calls
        ]


# ------------------------------------------------------------- validation ----


def validate_arguments(tool: ToolSpec, args: dict[str, Any]) -> str | None:
    """A small, strict JSON-schema subset check.

    Deliberately not a full validator. It covers required keys, unknown keys,
    scalar types and enums, which is every constraint the world schemas use, and
    it produces an error message a model can act on. A model that cannot fix its
    call from the message is a finding, not a bug in the validator.
    """
    schema = tool.parameters
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    missing = [key for key in required if key not in args]
    if missing:
        return f"missing required argument(s): {', '.join(missing)}"

    unknown = [key for key in args if key not in properties]
    if unknown:
        return (
            f"unknown argument(s): {', '.join(sorted(unknown))}. "
            f"Accepted: {', '.join(sorted(properties))}"
        )

    for key, value in args.items():
        spec = properties[key]
        expected = spec.get("type")
        if value is None and key not in required:
            continue
        if expected == "string" and not isinstance(value, str):
            return f"argument {key!r} must be a string, got {type(value).__name__}"
        if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"argument {key!r} must be an integer, got {type(value).__name__}"
        if expected == "number" and (isinstance(value, bool) or not isinstance(value, int | float)):
            return f"argument {key!r} must be a number, got {type(value).__name__}"
        if expected == "boolean" and not isinstance(value, bool):
            return f"argument {key!r} must be a boolean, got {type(value).__name__}"
        if "enum" in spec and value not in spec["enum"]:
            return f"argument {key!r} must be one of {spec['enum']}, got {value!r}"
        if isinstance(value, str):
            limit = int(spec.get("maxLength", 400))
            if len(value) > limit:
                return f"argument {key!r} is too long ({len(value)} characters, max {limit})"
            pattern = spec.get("pattern")
            if pattern and not re.match(pattern, value):
                return f"argument {key!r} must match {pattern}, got {value!r}"
        if expected == "integer" and "minimum" in spec and value < spec["minimum"]:
            return f"argument {key!r} must be at least {spec['minimum']}, got {value}"
    return None
