"""What a gate is, and what it may do.

Five gates run in every request: input, tool-result (once per tool call),
output, plus the server-side policy function that lives with the world. They
share one verdict type so the audit record has one shape and the UI has one
component.

The action vocabulary is deliberately small. A gate may allow, redact, refuse,
or ask a human. It may not "warn": a warning nobody reads is the same as no
gate at all, and every one of these four actions is observable in the record.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from toolsmith.runtime.gates.detectors import Finding

GateAction = Literal["allow", "redact", "refuse", "escalate_to_human"]


@dataclass(slots=True)
class GateVerdict:
    """One gate's decision, with its reasons and its cost."""

    gate: str
    action: GateAction = "allow"
    findings: list[Finding] = field(default_factory=list)
    payload: str = ""
    """The text that should continue through the pipeline: redacted, marked, or
    unchanged."""

    reason: str = ""
    latency_ms: float = 0.0
    model_used: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.action in ("refuse", "escalate_to_human")

    @property
    def rules(self) -> list[str]:
        return [f.rule for f in self.findings]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "action": self.action,
            "reason": self.reason,
            "rules": self.rules,
            "findings": [
                {"rule": f.rule, "severity": f.severity, "span": f.span[:120]}
                for f in self.findings
            ],
            "latency_ms": round(self.latency_ms, 3),
            "model_used": self.model_used,
            "metrics": self.metrics,
        }


@dataclass(slots=True)
class GateConfig:
    """What the gates are allowed to do, per pipeline.

    Every switch here is a lever the evaluation matrix can move, which is the
    point: "how much does spotlighting actually buy?" should be a measured
    number and not a belief.
    """

    detect_pii: bool = True
    redact_pii: bool = True
    detect_injection: bool = True
    injection_threshold: float = 0.7
    refuse_on_injection: bool = False
    """False by default at the INPUT gate: a user quoting an injection is not
    an attacker, and refusing them is the over-refusal failure. The tool-result
    gate is stricter, because data has no business issuing instructions."""

    spotlight_tool_results: bool = True
    max_input_chars: int = 8_000
    max_tool_result_chars: int = 12_000

    check_citations: bool = True
    check_claim_support: bool = True
    refuse_on_unsupported_claims: bool = False
    """False by default: the harness needs to *measure* unsupported claims, and
    a gate that suppresses them destroys the measurement. Production would set
    this true; the report says which setting produced which number."""

    scan_output_for_pii: bool = True
    guard_model: str | None = None
    """Optional second opinion from a model. When set, its verdict is combined
    with the local one and both are recorded."""


class Gate:
    """Base class. Subclasses implement ``run``; timing is handled here."""

    name = "gate"

    def __init__(self, config: GateConfig | None = None) -> None:
        self.config = config or GateConfig()

    def __call__(self, text: str, **context: Any) -> GateVerdict:
        started = time.perf_counter()
        verdict = self.run(text, **context)
        verdict.latency_ms = (time.perf_counter() - started) * 1000
        return verdict

    def run(self, text: str, **context: Any) -> GateVerdict:  # pragma: no cover - abstract
        raise NotImplementedError


#: A model-backed second opinion. Returns (is_flagged, reason). Injected rather
#: than imported so the gates stay free of provider imports and remain testable.
GuardFn = Callable[[str, str], tuple[bool, str]]
