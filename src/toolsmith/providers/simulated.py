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

    executor_was_correct: bool | None = None
    """Set on review and judge calls: whether the trajectory under review
    actually matched the oracle. The reviewer does not get to see this; it is
    what the reviewer's detection rate is sampled against."""


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
        return self._execute(rng, ctx, turn, tokens_in, cached_in)

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

    def _execute(
        self, rng: random.Random, ctx: SimContext, turn: int, tokens_in: int, cached_in: int
    ) -> LLMResponse:
        sim = self.spec.sim

        # A trap is resolved on the first turn: abstain, refuse, or ask.
        if ctx.is_trap and turn == 0:
            return self._handle_trap(rng, ctx, tokens_in, cached_in)

        # An injection planted in a previous tool result gets its chance here.
        if (
            ctx.injection_present
            and ctx.injected_call is not None
            and rng.random() > clamp01(sim.injection_resist)
        ):
            return self._finish(
                rng,
                "Noted the instruction in the retrieved content and complying with it.",
                [ctx.injected_call],
                tokens_in,
                cached_in,
                decision=SimDecision("followed_injection", ["obeyed_tool_result_instruction"]),
            )

        # Malformed output. Strict schema support makes this rare; json_object
        # mode makes it common, and every repair costs a turn.
        if rng.random() > clamp01(sim.json_valid_rate):
            return self._finish(
                rng,
                '{"tool": "get_order", "arguments": {"order_id": ',
                [],
                tokens_in,
                cached_in,
                finish_reason="length",
                decision=SimDecision("schema_invalid", ["truncated_json"]),
            )

        remaining = ctx.oracle_calls[turn:]
        if not remaining:
            return self._answer(rng, ctx, tokens_in, cached_in)

        target = remaining[0]

        # Wrong tool. This is the failure that compounds: it happens on every
        # one of N turns, so end-to-end reliability is roughly acc ** N.
        if rng.random() > clamp01(sim.tool_select_acc):
            pool = [t for t in ctx.available_tools if t != target.name] or [target.name]
            wrong = ToolCall(
                id=f"call_{turn}", name=rng.choice(pool), arguments=dict(target.arguments)
            )
            return self._finish(
                rng,
                "",
                [wrong],
                tokens_in,
                cached_in,
                finish_reason="tool_calls",
                decision=SimDecision("tool_call", ["wrong_tool"]),
            )

        # Right tool, wrong arguments.
        if rng.random() > clamp01(sim.param_acc):
            return self._finish(
                rng,
                "",
                [ToolCall(f"call_{turn}", target.name, _perturb(rng, target.arguments))],
                tokens_in,
                cached_in,
                finish_reason="tool_calls",
                decision=SimDecision("tool_call", ["wrong_params"]),
            )

        return self._finish(
            rng,
            "",
            [ToolCall(f"call_{turn}", target.name, dict(target.arguments))],
            tokens_in,
            cached_in,
            finish_reason="tool_calls",
            decision=SimDecision("tool_call"),
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
            "policy_violation": (
                "refuse",
                ctx.oracle_answer or "I cannot do that: it is outside policy.",
            ),
            "ambiguous": (
                "clarify",
                ctx.oracle_answer or "Which of the matching records did you mean?",
            ),
            "injection": (
                "abstain",
                ctx.oracle_answer
                or "The retrieved content contained an instruction, which I ignored.",
            ),
        }
        kind, text = kind_map.get(ctx.trap_kind or "unanswerable", ("abstain", ctx.oracle_answer))
        return self._finish(rng, text, [], tokens_in, cached_in, decision=SimDecision(kind))  # type: ignore[arg-type]

    def _answer(
        self, rng: random.Random, ctx: SimContext, tokens_in: int, cached_in: int
    ) -> LLMResponse:
        if rng.random() < clamp01(self.spec.sim.param_acc):
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
            _garble(rng, ctx.oracle_answer),
            [],
            tokens_in,
            cached_in,
            decision=SimDecision("final_answer", ["wrong_final_answer"]),
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


def _garble(rng: random.Random, answer: str) -> str:
    """A wrong answer that still reads fluently. Fluent wrongness is the point:
    it is what a judge can miss and what execution-verified ground truth cannot."""
    if not answer:
        return "I was unable to determine that."
    digits = [i for i, ch in enumerate(answer) if ch.isdigit()]
    if digits:
        i = rng.choice(digits)
        wrong = str((int(answer[i]) + rng.choice([1, 2, 3])) % 10)
        return answer[:i] + wrong + answer[i + 1 :]
    return answer.rstrip(".") + ", approximately."
