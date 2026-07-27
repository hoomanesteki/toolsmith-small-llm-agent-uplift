"""The deterministic behavioural simulator.

WHY THIS EXISTS
---------------
A control plane whose numbers can only be reproduced by someone holding five
API keys and a budget is not reproducible. The simulator lets anyone clone the
repository and regenerate every table, chart and transcript in about a minute
for nothing, and it lets CI assert that the statistics are correct rather than
merely that the code imports.

WHAT IT IS AND IS NOT
---------------------
It is *not* a language model, and it does not pretend that its outputs are
model outputs. It is a behavioural model: given a model's published capability
priors (``ModelSpec.sim``), it samples whether that model would have selected
the right tool, filled the right parameters, emitted valid JSON, resisted an
injected instruction, or correctly abstained. Everything downstream, the token
accounting, the cost ledger, the cascade logic, the statistics, the report, is
the real production code path operating on those decisions.

So the shape of every published finding is real and the code that produced it
is the code that would run live. The absolute values are as good as the priors,
which is exactly why every artifact is stamped ``provenance: simulated`` and
why ``--provider live`` regenerates the identical tables for real.

DETERMINISM
-----------
Every random decision is seeded from
``sha256(model_id, task_id, role, turn, trial)``. Same inputs, same trajectory,
on any machine, forever. There is no wall clock and no global RNG.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from toolsmith.config import ModelSpec
from toolsmith.providers.base import (
    LLMRequest,
    LLMResponse,
    Provider,
    RateLimiter,
    ToolCall,
    clamp01,
    estimate_tokens,
)

TrapKind = Literal["unanswerable", "policy_violation", "ambiguous", "injection"]

#: Self-review penalty applied to a reviewer that shares the executor's family.
#: Anchored on arXiv 2310.01798, where GPT-4 on GSM8K falls 95.5 -> 91.5 -> 89.0
#: across self-correction rounds: roughly a quarter of the model's error-catching
#: ability disappears when it is grading itself.
SELF_REVIEW_DETECTION_PENALTY = 0.72

#: Baseline emitted tokens for one tool-calling turn, before ``sim.verbosity``.
#: Tool-calling turns are short, which is precisely the regime where a fast
#: provider's tokens-per-second advantage matters least.
BASE_TURN_TOKENS = 300


@dataclass(slots=True)
class SimContext:
    """Ground truth handed to the simulator so it can decide right from wrong.

    Travels in ``LLMRequest.meta['sim']`` and is never serialised to a provider.
    Live adapters ignore ``meta`` entirely.
    """

    task_id: str
    tier: str = "T2"
    oracle_calls: list[ToolCall] = field(default_factory=list)
    oracle_answer: str = ""
    available_tools: list[str] = field(default_factory=list)
    is_trap: bool = False
    trap_kind: TrapKind | None = None
    injection_present: bool = False
    injected_call: ToolCall | None = None
    executor_family: str | None = None
    """Set when this call is a review, so the simulator can apply the
    self-review penalty when the reviewer shares the executor's family."""

    answer_keys: list[str] = field(default_factory=list)
    """The graded facts. A simulated wrong answer must corrupt one of these,
    or "wrong" answers score as correct and every floor row floats."""

    executor_was_correct: bool | None = None
    """Set on review and judge calls: whether the trajectory under review
    actually matched the oracle. The reviewer does not get to see this; it is
    what the reviewer's detection rate is sampled against."""


@dataclass(slots=True)
class TurnOutcome:
    """What one turn would do. Deterministic given the seed, so it can be replayed."""

    kind: Literal[
        "tool_call",
        "wrong_tool",
        "wrong_params",
        "schema_invalid",
        "followed_injection",
        "final",
    ]
    clean: bool
    wrong_tool: str | None = None


@dataclass(slots=True)
class SimDecision:
    """What the simulator decided this turn, recorded for the failure gallery."""

    kind: Literal[
        "tool_call",
        "final_answer",
        "abstain",
        "refuse",
        "clarify",
        "schema_invalid",
        "followed_injection",
        "gave_up",
    ]
    faults: list[str] = field(default_factory=list)


class SimulatedProvider(Provider):
    """A model's behaviour, sampled from its published capability priors."""

    provenance = "simulated"
    name = "simulated"

    def __init__(self, spec: ModelSpec, limiter: RateLimiter | None = None) -> None:
        super().__init__(spec, limiter)
        self.name = f"simulated:{spec.provider}"

    # ------------------------------------------------------------ dispatch --

    def complete(self, request: LLMRequest) -> LLMResponse:
        ctx = request.meta.get("sim")
        role = request.meta.get("role", "executor")
        turn = int(request.meta.get("turn", 0))
        trial = int(request.meta.get("trial", 0))
        rng = self._rng(ctx.task_id if ctx else "notask", role, turn, trial)

        tokens_in = self._count_input(request)
        cached_in = int(request.meta.get("cached_in", 0))

        if ctx is None:
            return self._finish(rng, "(no simulation context)", [], tokens_in, cached_in, 0.4)

        if role == "planner":
            return self._plan(rng, ctx, tokens_in, cached_in)
        if role == "reviewer":
            return self._review(rng, ctx, tokens_in, cached_in)
        if role == "judge":
            return self._judge(rng, ctx, tokens_in, cached_in)
        return self._execute(rng, ctx, turn, trial, tokens_in, cached_in)

    # -------------------------------------------------------------- planner --

    def _plan(
        self, rng: random.Random, ctx: SimContext, tokens_in: int, cached_in: int
    ) -> LLMResponse:
        """Planners are single-shot, so their quality is not compounded.

        A planner either identifies the right sub-goals and tool budget or it
        does not, and that decision is made once. This is why upgrading the
        planner is cheap: it runs once, not N times.
        """
        skill = clamp01(self.spec.sim.tool_select_acc)
        good = rng.random() < skill
        budget = (
            len(ctx.oracle_calls)
            if good
            else max(1, len(ctx.oracle_calls) + rng.choice([-1, 1, 2]))
        )
        plan = {
            "subgoals": [
                f"resolve {c.name}" for c in (ctx.oracle_calls if good else ctx.oracle_calls[:1])
            ]
            or ["answer directly"],
            "tool_budget": budget,
            "abstain_if": (
                "the requested record does not exist, the request violates policy, "
                "or the reference is ambiguous"
            ),
            "quality": "aligned" if good else "misaligned",
        }
        text = json.dumps(plan, indent=2)
        return self._finish(rng, text, [], tokens_in, cached_in, verbosity_scale=0.7)

    # ------------------------------------------------------------- executor --

    def _turn_outcome(self, ctx: SimContext, turn: int, trial: int) -> TurnOutcome:
        """What this model would do on one turn, decided by seeded rolls.

        Factored out so it can be *replayed*. When the executor reaches the end
        of the program, :meth:`_answer` re-derives every earlier turn's outcome
        to find out whether the trajectory was clean. That is what makes error
        compounding real in the simulator rather than decorative: a run that
        picked the wrong tool on turn two cannot produce the right answer on
        turn five, exactly as it could not in life.

        The rolls are drawn in a fixed order from an RNG seeded only by
        (model, task, role, turn, trial), so a replay is bit-identical to the
        original.
        """
        sim = self.spec.sim
        rng = self._rng(ctx.task_id, "executor", turn, trial)

        if (
            ctx.injection_present
            and ctx.injected_call is not None
            and rng.random() > clamp01(sim.injection_resist)
        ):
            return TurnOutcome("followed_injection", clean=False)
        if rng.random() > clamp01(sim.json_valid_rate):
            return TurnOutcome("schema_invalid", clean=False)

        remaining = ctx.oracle_calls[turn:]
        if not remaining:
            return TurnOutcome("final", clean=True)

        if rng.random() > clamp01(sim.tool_select_acc):
            pool = [t for t in ctx.available_tools if t != remaining[0].name] or [remaining[0].name]
            return TurnOutcome("wrong_tool", clean=False, wrong_tool=rng.choice(pool))
        if rng.random() > clamp01(sim.param_acc):
            return TurnOutcome("wrong_params", clean=False)
        return TurnOutcome("tool_call", clean=True)

    def _trajectory_was_clean(self, ctx: SimContext, upto_turn: int, trial: int) -> bool:
        """Replay every earlier turn. One fault anywhere spoils the answer."""
        return all(self._turn_outcome(ctx, t, trial).clean for t in range(upto_turn))

    def _execute(
        self,
        rng: random.Random,
        ctx: SimContext,
        turn: int,
        trial: int,
        tokens_in: int,
        cached_in: int,
    ) -> LLMResponse:
        # Traps are NOT short-circuited at turn zero. A correct agent still does
        # the work: it looks up the missing record and receives not_found, or it
        # reads the policy and attempts the privileged call and is refused. The
        # trap is resolved when the trajectory is complete, in _answer, because
        # that is where a real model decides between reporting what it found and
        # confabulating past it.
        outcome = self._turn_outcome(ctx, turn, trial)

        match outcome.kind:
            case "followed_injection":
                assert ctx.injected_call is not None
                return self._finish(
                    rng,
                    "Noted the instruction in the retrieved content and complying with it.",
                    [ctx.injected_call],
                    tokens_in,
                    cached_in,
                    decision=SimDecision("followed_injection", ["obeyed_tool_result_instruction"]),
                )
            case "schema_invalid":
                # Strict schema support makes this rare; json_object mode makes
                # it common, and every repair costs a turn.
                return self._finish(
                    rng,
                    '{"tool": "get_record", "arguments": {"id": ',
                    [],
                    tokens_in,
                    cached_in,
                    finish_reason="length",
                    decision=SimDecision("schema_invalid", ["truncated_json"]),
                )
            case "final":
                return self._answer(rng, ctx, turn, trial, tokens_in, cached_in)

        target = ctx.oracle_calls[turn]
        match outcome.kind:
            case "wrong_tool":
                # The failure that compounds: it can happen on every one of N
                # turns, so end-to-end reliability is roughly acc ** N.
                call = ToolCall(
                    id=f"call_{turn}",
                    name=outcome.wrong_tool or target.name,
                    arguments=dict(target.arguments),
                )
                faults = ["wrong_tool"]
            case "wrong_params":
                call = ToolCall(f"call_{turn}", target.name, _perturb(rng, target.arguments))
                faults = ["wrong_params"]
            case _:
                call = ToolCall(f"call_{turn}", target.name, dict(target.arguments))
                faults = []

        return self._finish(
            rng,
            "",
            [call],
            tokens_in,
            cached_in,
            finish_reason="tool_calls",
            decision=SimDecision("tool_call", faults),
        )

    def _handle_trap(
        self, rng: random.Random, ctx: SimContext, tokens_in: int, cached_in: int
    ) -> LLMResponse:
        """T4 is where cheap models stop being cheap.

        The correct answer to an unanswerable question is "I cannot", to a
        policy-violating request is "no", and to a genuinely ambiguous one is a
        clarifying question. Confabulating instead is the failure that costs a
        production system its credibility, and it is invisible to any metric
        that only scores answerable tasks.
        """
        # Injection tasks are graded on the action taken, not on the wording,
        # so they are not gated on abstention skill.
        if ctx.trap_kind == "injection":
            return self._finish(
                rng,
                ctx.oracle_answer,
                [],
                tokens_in,
                cached_in,
                decision=SimDecision("final_answer"),
            )

        correct = rng.random() < clamp01(self.spec.sim.abstain_skill)
        if not correct:
            return self._finish(
                rng,
                ctx.oracle_answer.replace("cannot", "can")
                or "Here is the information you asked for.",
                [],
                tokens_in,
                cached_in,
                decision=SimDecision("final_answer", [f"confabulated_on_{ctx.trap_kind}"]),
            )
        kind_map: dict[str, tuple[str, str]] = {
            "unanswerable": (
                "abstain",
                ctx.oracle_answer or "I cannot answer that from the available records.",
            ),
            # An injection task is answered normally. What makes it correct is
            # what did NOT happen, which the state diff sees and prose cannot.
            "policy_violation": (
                "refuse",
                ctx.oracle_answer or "I cannot do that: it is outside policy.",
            ),
            "ambiguous": (
                "clarify",
                ctx.oracle_answer or "Which of the matching records did you mean?",
            ),
            "injection": ("final_answer", ctx.oracle_answer),
        }
        kind, text = kind_map.get(ctx.trap_kind or "unanswerable", ("abstain", ctx.oracle_answer))
        return self._finish(rng, text, [], tokens_in, cached_in, decision=SimDecision(kind))  # type: ignore[arg-type]

    def _answer(
        self,
        rng: random.Random,
        ctx: SimContext,
        turn: int,
        trial: int,
        tokens_in: int,
        cached_in: int,
    ) -> LLMResponse:
        if ctx.is_trap:
            return self._handle_trap(rng, ctx, tokens_in, cached_in)

        # A trajectory that went wrong cannot end right. Replaying the earlier
        # turns is what makes q**N visible in pass@1 rather than only in the
        # per-step numbers.
        clean = self._trajectory_was_clean(ctx, turn, trial)
        if clean and rng.random() < clamp01(self.spec.sim.param_acc):
            return self._finish(
                rng,
                ctx.oracle_answer,
                [],
                tokens_in,
                cached_in,
                decision=SimDecision("final_answer"),
            )
        return self._finish(
            rng,
            _garble(rng, ctx.oracle_answer, ctx.answer_keys),
            [],
            tokens_in,
            cached_in,
            decision=SimDecision(
                "final_answer", ["wrong_final_answer"] if clean else ["compounded_error"]
            ),
        )

    # ------------------------------------------------------------- reviewer --

    def _review(
        self, rng: random.Random, ctx: SimContext, tokens_in: int, cached_in: int
    ) -> LLMResponse:
        """Detection rate, not the reviewer's own task accuracy, drives the cascade.

        A verifier that catches 95% of executor errors and one that catches 65%
        produce very different systems even with an identical escalation target,
        because escalation only fires on what the verifier noticed.
        """
        sim = self.spec.sim
        detect = clamp01(0.30 + 0.68 * sim.tool_select_acc)
        false_alarm = clamp01(0.02 + 0.14 * (1.0 - sim.tool_select_acc))

        if ctx.executor_family and ctx.executor_family == self.spec.family:
            detect *= SELF_REVIEW_DETECTION_PENALTY

        was_correct = bool(ctx.executor_was_correct)
        threshold = false_alarm if was_correct else detect
        reject = rng.random() < threshold

        verdict = {
            "verdict": "reject" if reject else "accept",
            "confidence": round(rng.uniform(0.55, 0.98), 3),
            "reasons": (
                ["trajectory diverges from the stated plan", "an unsupported claim in the answer"]
                if reject
                else ["every claim traces to a tool result"]
            ),
            "scores": {
                "correctness": 2 if reject else 5,
                "faithfulness": 3 if reject else 5,
                "relevance": 4,
                "safety_tone": 5,
                "efficiency": 4,
            },
        }
        return self._finish(rng, json.dumps(verdict), [], tokens_in, cached_in, verbosity_scale=0.5)

    # ---------------------------------------------------------------- judge --

    def _judge(
        self, rng: random.Random, ctx: SimContext, tokens_in: int, cached_in: int
    ) -> LLMResponse:
        """A judge that agrees with truth at a rate set by its own competence.

        Cheap judges are noisy rather than adversarial: they regress toward the
        middle of the scale. That is what makes the cheap-judge kappa gap in the
        report a real measurement rather than a foregone conclusion.
        """
        skill = clamp01(0.45 + 0.55 * self.spec.sim.tool_select_acc)
        truth = bool(ctx.executor_was_correct)
        agrees = rng.random() < skill
        good = truth if agrees else not truth

        def score(high: int, low: int) -> int:
            base = high if good else low
            return int(max(1, min(5, base + rng.choice([-1, 0, 0, 0, 1]))))

        scores = {
            "correctness": score(5, 2),
            "faithfulness": score(5, 2),
            "relevance": score(5, 3),
            "safety_tone": score(5, 4),
            "efficiency": score(4, 3),
        }
        return self._finish(
            rng,
            json.dumps({"scores": scores, "rationale": "graded against the rubric anchors"}),
            [],
            tokens_in,
            cached_in,
            verbosity_scale=0.4,
        )

    # -------------------------------------------------------------- helpers --

    def _rng(self, task_id: str, role: str, turn: int, trial: int) -> random.Random:
        return random.Random(self._seeded(self.spec.model_id, task_id, role, turn, trial))

    def _finish(
        self,
        rng: random.Random,
        text: str,
        calls: list[ToolCall],
        tokens_in: int,
        cached_in: int,
        verbosity_scale: float = 1.0,
        finish_reason: str = "",
        decision: SimDecision | None = None,
    ) -> LLMResponse:
        emitted = estimate_tokens(text) + sum(estimate_tokens(c.signature()) for c in calls)
        floor = int(BASE_TURN_TOKENS * self.spec.sim.verbosity * verbosity_scale)
        tokens_out = max(emitted, int(floor * rng.uniform(0.75, 1.25)))
        reason = finish_reason or ("tool_calls" if calls else "stop")
        return LLMResponse(
            text=text,
            tool_calls=calls,
            tokens_in=tokens_in,
            tokens_cached_in=min(cached_in, tokens_in),
            tokens_out=tokens_out,
            latency_s=self._simulate_latency(tokens_out),
            finish_reason=reason,  # type: ignore[arg-type]
            usage_source="estimated",
            raw={
                "decision": decision.kind if decision else "n/a",
                "faults": decision.faults if decision else [],
            },
        )


def _perturb(rng: random.Random, args: dict[str, Any]) -> dict[str, Any]:
    """Break exactly one argument, the way a real model does: plausibly."""
    if not args:
        return {"limit": 999}
    out = dict(args)
    key = rng.choice(sorted(out))
    value = out[key]
    if isinstance(value, bool):
        out[key] = not value
    elif isinstance(value, int):
        out[key] = value + rng.choice([-1, 1, 10])
    elif isinstance(value, str) and value:
        out[key] = value[:-1] + rng.choice("0189xz") if len(value) > 2 else value + "x"
    else:
        out[key] = None
    return out


def _garble(rng: random.Random, answer: str, answer_keys: list[str] | None = None) -> str:
    """A wrong answer that still reads fluently.

    Fluent wrongness is the point: it is what a judge can miss and what
    execution-verified ground truth catches. Which means the corruption has to
    hit the *graded* facts. An earlier version only perturbed digits, so a wrong
    answer to "what status is this order in?" still contained the word
    "delivered" and scored as correct. The floor row caught it, which is what
    floor rows are for.
    """
    if not answer:
        return "I was unable to determine that."
    out = answer
    for key in answer_keys or []:
        if not key:
            continue
        # Case-insensitive and boundary-aware, to match how the grader reads it.
        # A case-sensitive replace silently failed on answers that open with the
        # key ("No, because ...") and left every such wrong answer scoring as
        # correct, which showed up as a floor row beating itself on harder tiers.
        pattern = re.compile(rf"(?<![\w-]){re.escape(key)}(?![\w-])", re.IGNORECASE)
        if pattern.search(out):
            out = pattern.sub(_corrupt(rng, key), out, count=0)
    if out != answer:
        return out
    digits = [i for i, ch in enumerate(out) if ch.isdigit()]
    if digits:
        i = rng.choice(digits)
        return out[:i] + str((int(out[i]) + rng.choice([1, 2, 3])) % 10) + out[i + 1 :]
    return out.rstrip(".") + ", approximately."


#: Plausible substitutes for the short categorical answers that appear as
#: graded facts. A corrupted value must not *contain* the original, or a
#: word-boundary grader still scores it as a hit.
_SUBSTITUTES: dict[str, tuple[str, ...]] = {
    "yes": ("no",),
    "no": ("yes",),
    "which": ("here is the record",),
    "cannot": ("here is what I found",),
}


def _corrupt(rng: random.Random, value: str) -> str:
    """Change a value to a different, still plausible one.

    The result must not contain the original as a substring. "no" corrupted to
    "nox" is still a hit under any sane grader, which makes a wrong answer score
    as right.
    """
    lowered = value.lower()
    if lowered in _SUBSTITUTES:
        return rng.choice(_SUBSTITUTES[lowered])
    if value.replace(".", "").isdigit():
        try:
            return str(int(float(value)) + rng.choice([1, 2, 3, 10]))
        except ValueError:  # pragma: no cover - guarded by the isdigit check
            pass
    if len(value) > 4:
        return rng.choice(["unknown", "pending", "unavailable", "not recorded"])
    return rng.choice(["unknown", "n/a", "unset"])
