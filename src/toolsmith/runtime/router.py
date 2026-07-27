"""The cascade: when to spend more, and on what.

The finding this implements is that escalation is not a fallback, it is a second
independent attempt. A cheap executor with a good verifier and a frontier retry
can exceed the success rate of running the frontier model everywhere, because
two attempts beat one even when the first is weaker.

Which makes **detection rate**, not the executor's accuracy, the variable that
governs the whole system. Escalation only fires on what the verifier noticed. A
verifier that catches 95% of executor errors and one that catches 65% produce
very different systems with an identical escalation target, and that is why the
matrix has a row for each.

Confidence signals, strongest first:

1. the verifier's verdict
2. schema validity, which is free and unambiguous
3. tool-call count against the plan's budget
4. self-consistency across k cheap samples, when configured
5. output logprob, where a provider exposes it

They are combined into one score with published weights rather than tuned, and
the threshold is learned on the validation split by Track B and reported on
test. Never tuned on test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EscalationTrigger = Literal[
    "verifier_reject", "schema_invalid", "low_confidence", "budget_exceeded", "never"
]

#: Weights on the confidence signals. Deliberately fixed and published: a
#: weighting fitted on the same data the threshold is fitted on would be two
#: free parameters pretending to be one.
SIGNAL_WEIGHTS: dict[str, float] = {
    "verifier": 0.55,
    "schema_valid": 0.20,
    "within_budget": 0.15,
    "self_consistency": 0.10,
}


@dataclass(slots=True)
class ConfidenceSignals:
    """What the router knows about a trajectory before deciding to spend more."""

    verifier_accepted: bool | None = None
    verifier_confidence: float = 0.5
    schema_valid: bool = True
    tool_calls_made: int = 0
    tool_budget: int = 0
    self_consistency: float | None = None
    """Agreement across k cheap samples.

    Not currently wired to a configuration knob. There was a
    ``self_consistency_k`` field for it that nothing read, which under a schema
    that forbids extras meant setting it produced silence rather than an error.
    The function stays because the router calls it with a fixed k; the field
    went because a knob that does nothing is worse than no knob.
    """

    logprob: float | None = None

    def score(self) -> float:
        """One number in [0, 1]. Higher means less reason to escalate."""
        parts: dict[str, float] = {}
        if self.verifier_accepted is not None:
            parts["verifier"] = (
                self.verifier_confidence
                if self.verifier_accepted
                else (1.0 - self.verifier_confidence)
            )
        parts["schema_valid"] = 1.0 if self.schema_valid else 0.0
        if self.tool_budget:
            overrun = max(0, self.tool_calls_made - self.tool_budget)
            parts["within_budget"] = max(0.0, 1.0 - overrun / max(1, self.tool_budget))
        if self.self_consistency is not None:
            parts["self_consistency"] = self.self_consistency

        weight_total = sum(SIGNAL_WEIGHTS[k] for k in parts)
        if not weight_total:
            return 0.5
        return round(sum(SIGNAL_WEIGHTS[k] * v for k, v in parts.items()) / weight_total, 4)

    def breakdown(self) -> dict[str, float | bool | None]:
        return {
            "verifier_accepted": self.verifier_accepted,
            "verifier_confidence": self.verifier_confidence,
            "schema_valid": self.schema_valid,
            "tool_calls_made": self.tool_calls_made,
            "tool_budget": self.tool_budget,
            "self_consistency": self.self_consistency,
            "score": self.score(),
        }


@dataclass(slots=True)
class RoutingDecision:
    escalate: bool
    trigger: str = ""
    reason: str = ""
    confidence: float = 0.0
    signals: dict[str, float | bool | None] = field(default_factory=dict)


class Router:
    """Decides whether a trajectory gets a second, more expensive attempt."""

    def __init__(
        self,
        triggers: list[str],
        threshold: float = 0.5,
        escalate_to: str | None = None,
    ) -> None:
        self.triggers = set(triggers)
        self.threshold = threshold
        self.escalate_to = escalate_to

    @property
    def enabled(self) -> bool:
        return bool(self.escalate_to) and "never" not in self.triggers and bool(self.triggers)

    def decide(self, signals: ConfidenceSignals) -> RoutingDecision:
        score = signals.score()
        base = RoutingDecision(escalate=False, confidence=score, signals=signals.breakdown())
        if not self.enabled:
            base.reason = "escalation disabled for this configuration"
            return base

        if "schema_invalid" in self.triggers and not signals.schema_valid:
            return RoutingDecision(
                True,
                "schema_invalid",
                "the executor never produced a valid final response",
                score,
                signals.breakdown(),
            )
        if "verifier_reject" in self.triggers and signals.verifier_accepted is False:
            return RoutingDecision(
                True,
                "verifier_reject",
                f"the reviewer rejected the trajectory with confidence {signals.verifier_confidence:.2f}",
                score,
                signals.breakdown(),
            )
        if "low_confidence" in self.triggers and score < self.threshold:
            return RoutingDecision(
                True,
                "low_confidence",
                f"confidence {score:.2f} is below the tuned threshold {self.threshold:.2f}",
                score,
                signals.breakdown(),
            )
        base.reason = f"confidence {score:.2f} cleared the threshold {self.threshold:.2f}"
        return base
