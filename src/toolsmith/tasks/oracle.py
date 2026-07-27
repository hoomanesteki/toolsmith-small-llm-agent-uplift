"""Executing oracle programs, and refusing to ship a task whose oracle is wrong.

The claim this module backs is the one the whole project rests on: ground truth
is computed, never written by hand and never judged by a model.

So every generated task is executed before it is allowed into the dataset. If
the gold program errors where it should succeed, or succeeds where it should be
refused, the task is discarded and counted. That count is published, because a
generator with a silent 12% invalid rate is a generator whose numbers mean
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from toolsmith.tasks.models import Task
from toolsmith.worlds.base import StateDiff, WorldSpec
from toolsmith.worlds.sandbox import Injection, Sandbox, WorldBuild


@dataclass(slots=True)
class OracleOutcome:
    """What running the gold program actually did."""

    ok: bool
    calls: list[str] = field(default_factory=list)
    state_diff: StateDiff = field(default_factory=StateDiff)
    citations: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)

    @property
    def state_signature(self) -> str:
        return self.state_diff.signature() if self.state_diff.changes else ""


def to_injections(task: Task, world: WorldSpec) -> list[Injection]:
    """Render a task's verb-level injection specs into sandbox injections."""
    out = []
    for spec in task.injections:
        if not world.has(spec.verb):
            continue
        out.append(
            Injection(
                tool=world.resolve(spec.verb),
                payload=spec.payload,
                match=spec.match,
                field_name=spec.field_name,
            )
        )
    return out


def execute_program(
    task: Task, world: WorldSpec, build: WorldBuild, *, with_injections: bool = False
) -> OracleOutcome:
    """Run the gold trajectory in a fresh sandbox and observe what it produced.

    ``with_injections`` is off by default. The oracle is what a correct agent
    does, and a correct agent ignores planted instructions, so the gold state
    diff must be computed from a clean run. The injected run is what the system
    under test sees.
    """
    outcome = OracleOutcome(ok=True)
    injections = to_injections(task, world) if with_injections else []

    with Sandbox(world, build, injections=injections) as sandbox:
        for index, step in enumerate(task.program):
            if not world.has(step.verb):
                outcome.ok = False
                outcome.failures.append(
                    f"step {index}: world {world.key} does not bind {step.verb}"
                )
                break
            tool_name = world.resolve(step.verb)
            result = sandbox.call(tool_name, step.arguments)
            outcome.calls.append(step.signature(tool_name))
            outcome.citations.extend(result.citations)
            outcome.results.append({"tool": tool_name, "ok": result.ok, "data": result.data})

            if result.ok != step.expect_ok:
                outcome.ok = False
                outcome.failures.append(
                    f"step {index} ({tool_name}): expected ok={step.expect_ok}, "
                    f"got ok={result.ok} ({result.error_code}: {result.error})"
                )
                break
        outcome.state_diff = sandbox.state_diff()
    return outcome


@dataclass(slots=True)
class Verdict:
    valid: bool
    reasons: list[str] = field(default_factory=list)
    outcome: OracleOutcome | None = None


def check_consistency(task: Task, outcome: OracleOutcome) -> list[str]:
    """Everything verifiable without re-running the program.

    Split out from :func:`verify_task` because generation executes once and then
    checks; re-executing to check would double the cost of building the suite
    for no extra assurance.
    """
    reasons: list[str] = []

    if not outcome.ok:
        reasons.extend(outcome.failures)

    if outcome.state_signature != task.oracle_state_diff:
        reasons.append(
            "recorded state diff does not match a fresh execution "
            f"(recorded {task.oracle_state_diff[:60]!r}, got {outcome.state_signature[:60]!r})"
        )

    if outcome.calls != task.oracle_calls:
        reasons.append("recorded call signatures do not match a fresh execution")

    if not task.oracle_answer.strip():
        reasons.append("oracle_answer is empty")

    if task.tier == "T5" and not task.expected_citations:
        reasons.append("a grounded task must declare its expected citations")

    if task.expected_citations and not set(task.expected_citations) <= set(outcome.citations):
        reasons.append(
            "expected citations were not returned by the gold program: "
            f"{sorted(set(task.expected_citations) - set(outcome.citations))}"
        )

    if task.is_trap and task.expected_behaviour == "answer":
        reasons.append("a trap must expect abstain, refuse, clarify or ignore_and_answer")

    if not task.is_trap and task.expected_behaviour != "answer":
        reasons.append("a non-trap task must expect a plain answer")

    return reasons


def verify_task(task: Task, world: WorldSpec, build: WorldBuild) -> Verdict:
    """Execute the gold program and assert the task agrees with the result."""
    outcome = execute_program(task, world, build)
    return Verdict(
        valid=not check_consistency(task, outcome),
        reasons=check_consistency(task, outcome),
        outcome=outcome,
    )


@dataclass
class GenerationReport:
    """Published alongside the dataset. A silent rejection rate hides a bug."""

    accepted: int = 0
    rejected: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def record(self, verdict: Verdict) -> None:
        if verdict.valid:
            self.accepted += 1
            return
        self.rejected += 1
        for reason in verdict.reasons:
            key = reason.split("(")[0].strip()[:80]
            self.reasons[key] = self.reasons.get(key, 0) + 1

    @property
    def rejection_rate(self) -> float:
        total = self.accepted + self.rejected
        return self.rejected / total if total else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "rejected": self.rejected,
            "rejection_rate": round(self.rejection_rate, 4),
            "reasons": dict(sorted(self.reasons.items(), key=lambda kv: -kv[1])),
        }
