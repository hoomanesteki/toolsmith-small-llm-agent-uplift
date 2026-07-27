"""[G3] The output gate.

The last chance to notice that a fluent answer is not a true one. Four checks:

* **citation validity**: every id the response cites must be one the trajectory
  actually retrieved. Citing a plausible-looking section that was never fetched
  is the most common grounding failure and the easiest to miss by reading.
* **claim support**: every identifier, date and number in the answer must appear
  in the evidence the run observed.
* **PII leakage**: an answer must not carry identifiers out of the world.
* **shape**: an empty or truncated response is a failure, not a response.

One deliberate default: unsupported claims are *recorded* rather than refused.
The harness needs to measure how often each configuration hallucinates, and a
gate that suppresses the symptom destroys the measurement. Production flips
``refuse_on_unsupported_claims`` on, and the report states which setting
produced which number.
"""

from __future__ import annotations

from typing import Any

from toolsmith.runtime.gates.base import Gate, GateVerdict
from toolsmith.runtime.gates.detectors import (
    Finding,
    check_claim_support,
    detect_pii,
    extract_citations,
    redact,
)


class OutputGate(Gate):
    name = "output"

    def run(self, text: str, **context: Any) -> GateVerdict:
        evidence: str = context.get("evidence", "")
        available_citations: set[str] = set(context.get("available_citations", []))
        verdict = GateVerdict(gate=self.name, payload=text)

        if not text.strip():
            verdict.action = "refuse"
            verdict.reason = "the model produced no response"
            return verdict

        # 1. Citations must have been retrieved, not invented.
        if self.config.check_citations and available_citations:
            cited = extract_citations(text)
            invented = [c for c in cited if c not in available_citations]
            verdict.metrics["citations_made"] = len(cited)
            verdict.metrics["citations_invented"] = len(invented)
            for citation in invented:
                verdict.findings.append(
                    Finding(
                        rule="grounding.invented_citation",
                        severity="high",
                        span=citation,
                        note="cited an id that no tool call in this run returned",
                    )
                )
            if invented:
                verdict.action = "refuse" if self.config.refuse_on_unsupported_claims else "allow"
                verdict.reason = (
                    f"cited {len(invented)} id(s) never retrieved: {', '.join(invented[:3])}"
                )

        # 2. Claims must trace to observed evidence.
        if self.config.check_claim_support and evidence:
            support = check_claim_support(text, evidence)
            verdict.metrics["claims_checked"] = support.total
            verdict.metrics["claim_support_rate"] = round(support.rate, 4)
            verdict.metrics["claim_checker"] = "lexical_atoms"
            for atom in support.unsupported[:8]:
                verdict.findings.append(
                    Finding(
                        rule="grounding.unsupported_claim",
                        severity="medium",
                        span=atom,
                        note="appears in the answer but in no tool result this run observed",
                    )
                )
            if support.unsupported and self.config.refuse_on_unsupported_claims:
                verdict.action = "refuse"
                verdict.reason = (
                    f"{len(support.unsupported)} unsupported value(s) in the answer: "
                    f"{', '.join(support.unsupported[:3])}"
                )

        # 3. Nothing identifying leaves the system.
        if self.config.scan_output_for_pii:
            pii = detect_pii(text)
            leaked = [
                f
                for f in pii
                if f.rule in {"pii.card", "pii.national_id", "pii.api_key", "pii.iban"}
            ]
            if leaked:
                verdict.findings.extend(leaked)
                verdict.payload = redact(verdict.payload, leaked)
                if verdict.action == "allow":
                    verdict.action = "redact"
                verdict.reason = (verdict.reason + " | " if verdict.reason else "") + (
                    f"redacted {len(leaked)} identifier(s) from the response"
                )

        return verdict
