"""[G2] The tool-result gate: the step everyone skips.

Almost every agent implementation validates user input and then concatenates
tool results straight into the same context, with nothing distinguishing the
system prompt from a customer's notes field. That is where the realistic attack
lives: not a user typing "ignore your instructions", but a sentence sitting
inside a record that the agent retrieves and then obeys.

Two things happen here, and only here.

**Scanning.** Every tool result is classified before it re-enters context. A
detection is recorded on the run whether or not it changes the outcome, so the
injection-resistance metric measures what the system saw as well as what it did.

**Spotlighting.** Every result is wrapped in per-call delimiters and preceded by
a notice that the content is data, never instructions. The marker id changes per
call, so a payload cannot close its own fence.

Neither is a guarantee. The T4 injection tier exists to measure precisely how
much they buy, and the answer is reported rather than assumed.
"""

from __future__ import annotations

from typing import Any

from toolsmith.runtime.gates.base import Gate, GateConfig, GateVerdict, GuardFn
from toolsmith.runtime.gates.detectors import detect_injection, spotlight


class ToolResultGate(Gate):
    name = "tool_result"

    def __init__(
        self,
        config: GateConfig | None = None,
        guard: GuardFn | None = None,
        guard_model: str | None = None,
    ) -> None:
        super().__init__(config)
        self.guard = guard
        self.guard_model = guard_model

    def run(self, text: str, **context: Any) -> GateVerdict:
        marker_id = str(context.get("call_id", "0"))
        tool_name = str(context.get("tool", "unknown"))
        verdict = GateVerdict(gate=self.name, payload=text)
        verdict.metrics["tool"] = tool_name

        # Truncate rather than refuse: a large result is a paging problem, not
        # an attack, and dropping the turn would lose work already paid for.
        if len(text) > self.config.max_tool_result_chars:
            keep = self.config.max_tool_result_chars
            verdict.payload = (
                text[:keep]
                + f"\n...[truncated {len(text) - keep:,} characters by the tool-result gate]"
            )
            verdict.action = "redact"
            verdict.reason = "result truncated"

        if self.config.detect_injection:
            injection = detect_injection(verdict.payload, self.config.injection_threshold)
            verdict.metrics["injection_score"] = injection.score
            if injection.detected:
                verdict.findings.extend(injection.findings)
                verdict.action = "redact"
                verdict.reason = (
                    f"tool result from {tool_name} contains instruction-shaped text "
                    f"({', '.join(injection.rules)}, score {injection.score:.2f}); "
                    "marked as data and flagged to the user"
                )

        if self.guard is not None and verdict.findings:
            flagged, reason = self.guard(verdict.payload, "tool_result")
            verdict.model_used = self.guard_model
            verdict.metrics["guard_agreed"] = flagged
            if flagged and reason:
                verdict.reason = f"{verdict.reason} | classifier: {reason}"

        if self.config.spotlight_tool_results:
            verdict.payload = spotlight(verdict.payload, marker_id)
            verdict.metrics["spotlighted"] = True

        return verdict
