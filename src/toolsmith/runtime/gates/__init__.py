"""Five gates, mapped to the risks they exist for.

| Gate | Where | Catches | OWASP LLM Top 10 |
|---|---|---|---|
| Input | before the planner | oversized input, PII, direct injection, policy | LLM01, LLM02, LLM06 |
| Tool result | after every tool call | indirect injection planted in data | LLM01, LLM05 |
| Policy function | before every privileged write | unauthorised consequential action | LLM06, LLM08 |
| Output | before the response | invented citations, unsupported claims, PII leak | LLM02, LLM09 |
| Budget | every turn | runaway loops and unbounded spend | LLM04, LLM10 |

The policy function is not in this package on purpose: it lives with the world,
because authorisation is domain logic and pretending otherwise is how it ends up
in a prompt.
"""

from toolsmith.runtime.gates.base import Gate, GateAction, GateConfig, GateVerdict, GuardFn
from toolsmith.runtime.gates.detectors import (
    INJECTION_RULES,
    INJECTION_THRESHOLD,
    PII_PATTERNS,
    Finding,
    InjectionVerdict,
    SupportReport,
    check_claim_support,
    detect_injection,
    detect_pii,
    extract_citations,
    redact,
    spotlight,
)
from toolsmith.runtime.gates.input import InputGate
from toolsmith.runtime.gates.output import OutputGate
from toolsmith.runtime.gates.tool_result import ToolResultGate

__all__ = [
    "INJECTION_RULES",
    "INJECTION_THRESHOLD",
    "PII_PATTERNS",
    "Finding",
    "Gate",
    "GateAction",
    "GateConfig",
    "GateVerdict",
    "GuardFn",
    "InjectionVerdict",
    "InputGate",
    "OutputGate",
    "SupportReport",
    "ToolResultGate",
    "check_claim_support",
    "detect_injection",
    "detect_pii",
    "extract_citations",
    "redact",
    "spotlight",
]
