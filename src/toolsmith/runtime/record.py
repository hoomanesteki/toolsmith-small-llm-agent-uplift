"""The run record: everything that happened, in one serialisable object.

This is the audit artifact and the harness's input. It carries what an
enterprise reviewer asks for and what the statistics need, which turn out to be
the same list: the inputs (redacted), every gate verdict, the model ids and
versions, the tool calls, the costs, the output, and the output-gate verdicts.

Nothing here is a summary. The record is the primary source, and every number in
the report is derived from a collection of these.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Behaviour = Literal["answer", "abstain", "refuse", "clarify", "error"]


@dataclass(slots=True)
class ModelCall:
    """One provider request, with what it cost."""

    role: str
    model_key: str
    provider: str
    provenance: str
    tokens_in: int
    tokens_cached_in: int
    tokens_out: int
    usd: float
    latency_s: float
    turn: int = 0
    finish_reason: str = "stop"

    @property
    def cache_hit_rate(self) -> float:
        return self.tokens_cached_in / self.tokens_in if self.tokens_in else 0.0


@dataclass(slots=True)
class ToolInvocation:
    index: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    error_code: str | None = None
    mutated: bool = False
    privileged: bool = False
    policy_allowed: bool | None = None
    policy_code: str = ""
    injection_detected: bool = False
    latency_ms: float = 0.0

    def signature(self) -> str:
        import json

        return f"{self.tool}({json.dumps(self.arguments, sort_keys=True, default=str)})"


@dataclass
class RunRecord:
    """One task, one configuration, one trial."""

    run_id: str
    task_id: str
    world: str
    pipeline: str
    trial: int = 0
    provider_mode: str = "simulated"

    # -- what the system did ------------------------------------------------
    answer: str = ""
    behaviour: Behaviour = "answer"
    plan: str = ""
    calls: list[ToolInvocation] = field(default_factory=list)
    state_diff: str = ""
    citations: list[str] = field(default_factory=list)
    turns: int = 0

    # -- what the guards saw ------------------------------------------------
    gate_verdicts: list[dict[str, Any]] = field(default_factory=list)
    injection_seen: bool = False
    injection_followed: bool = False
    privileged_attempted: bool = False
    privileged_refused: bool = False
    hitl_requests: list[dict[str, Any]] = field(default_factory=list)

    # -- routing ------------------------------------------------------------
    review_verdict: str = ""
    review_confidence: float = 0.0
    escalated: bool = False
    escalation_reason: str = ""

    # -- economics ----------------------------------------------------------
    model_calls: list[ModelCall] = field(default_factory=list)
    usd: float = 0.0
    latency_s: float = 0.0
    """Modelled latency: provider time-to-first-token plus generation, summed
    across calls. Deterministic, and the right thing to publish: the harness's
    own Python overhead is not the agent's latency, and including it makes every
    artifact differ between runs on the same machine."""

    wall_clock_s: float = 0.0
    """Measured. Kept for debugging, deliberately NOT written to results.jsonl,
    because a published artifact that changes when nothing changed cannot be
    checked for drift."""

    context_stats: dict[str, Any] = field(default_factory=dict)

    # -- bookkeeping --------------------------------------------------------
    error: str = ""
    started_at: str = field(
        default_factory=lambda: dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    )
    models_used: dict[str, str] = field(default_factory=dict)
    substitutions: dict[str, str] = field(default_factory=dict)
    """Models that fell back to simulation in ``auto`` mode. A mixed run can
    never be mistaken for a live one."""

    # ------------------------------------------------------------ derived --

    @property
    def tokens_in(self) -> int:
        return sum(c.tokens_in for c in self.model_calls)

    @property
    def tokens_out(self) -> int:
        return sum(c.tokens_out for c in self.model_calls)

    @property
    def tokens_cached_in(self) -> int:
        return sum(c.tokens_cached_in for c in self.model_calls)

    @property
    def input_share(self) -> float:
        """Input tokens as a share of all tokens. The headline finding is that
        this sits near 0.9, and that everyone optimises the other 0.1."""
        total = self.tokens_in + self.tokens_out
        return self.tokens_in / total if total else 0.0

    @property
    def executor_input_share(self) -> float:
        """Executor input as a share of all input. The other half of the
        finding: the role that runs N times dominates the bill."""
        total = self.tokens_in
        if not total:
            return 0.0
        return sum(c.tokens_in for c in self.model_calls if c.role == "executor") / total

    @property
    def call_signatures(self) -> list[str]:
        return [c.signature() for c in self.calls]

    @property
    def mutating(self) -> bool:
        return any(c.mutated for c in self.calls)

    @property
    def spend_by_role(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for call in self.model_calls:
            out[call.role] = round(out.get(call.role, 0.0) + call.usd, 8)
        return out

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "tokens_cached_in": self.tokens_cached_in,
                "input_share": round(self.input_share, 4),
                "executor_input_share": round(self.executor_input_share, 4),
                "spend_by_role": self.spend_by_role,
                "n_calls": len(self.calls),
                "mutating": self.mutating,
            }
        )
        return payload
