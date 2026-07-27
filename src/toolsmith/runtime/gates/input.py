"""[G1] The input gate.

Four checks, cheapest first, because the cheap ones reject most of what the
expensive one would have to look at:

1. shape and length, in code, zero cost
2. PII detection and redaction, local, roughly a millisecond
3. injection classification, local rules plus an optional model
4. policy and topic, a model when one is configured

One decision here is worth defending. An injection detected in *user* input
does not refuse by default. Users legitimately paste suspicious text and ask
"is this an attack?", and a gate that refuses them has produced the over-refusal
failure that makes assistants useless. The user is the principal; the tool-result
gate, where data has no business issuing instructions, is where refusal belongs.
"""

from __future__ import annotations

from typing import Any

from toolsmith.runtime.gates.base import Gate, GateConfig, GateVerdict, GuardFn
from toolsmith.runtime.gates.detectors import detect_injection, detect_pii, redact


class InputGate(Gate):
    name = "input"

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
        del context  # the input gate needs no call context
        verdict = GateVerdict(gate=self.name, payload=text)

        # 1. Shape. Free, and it catches the accidental paste of a log file.
        if not text.strip():
            verdict.action = "refuse"
            verdict.reason = "empty request"
            return verdict
        if len(text) > self.config.max_input_chars:
            verdict.action = "refuse"
            verdict.reason = (
                f"request is {len(text):,} characters, over the "
                f"{self.config.max_input_chars:,} limit"
            )
            return verdict

        # 2. PII. Redact rather than refuse: the request is usually legitimate
        # and the identifier usually incidental.
        if self.config.detect_pii:
            pii = detect_pii(text)
            if pii:
                verdict.findings.extend(pii)
                if self.config.redact_pii:
                    verdict.payload = redact(verdict.payload, pii)
                    verdict.action = "redact"
                    verdict.reason = f"redacted {len(pii)} identifier(s)"

        # 3. Injection. Recorded always, refused only if configured.
        if self.config.detect_injection:
            injection = detect_injection(text, self.config.injection_threshold)
            verdict.metrics["injection_score"] = injection.score
            if injection.detected:
                verdict.findings.extend(injection.findings)
                if self.config.refuse_on_injection:
                    verdict.action = "refuse"
                    verdict.reason = (
                        f"input matched {', '.join(injection.rules)} (score {injection.score:.2f})"
                    )
                    return verdict
                verdict.reason = (
                    verdict.reason or f"noted injection-shaped input (score {injection.score:.2f})"
                )

        # 4. Policy. A model, when one is configured for it.
        if self.guard is not None:
            flagged, reason = self.guard(verdict.payload, "input_policy")
            verdict.model_used = self.guard_model
            if flagged:
                verdict.action = "refuse"
                verdict.reason = reason or "refused by the policy classifier"
        return verdict
