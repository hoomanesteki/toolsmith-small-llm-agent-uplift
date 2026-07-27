"""What a task is.

The important decision in this file: an oracle program is a list of **verbs**,
not tool names. ``[SEARCH_PRINCIPALS, GET_PRINCIPAL, LIST_RECORDS]`` renders to
``search_customers, get_customer, list_orders`` in operations and to
``search_patients, get_patient, list_appointments`` in the clinic.

That is what makes a task template portable. One template written once
generates correct, ground-truthed tasks for every world that binds the verbs it
needs, including a world added after the template was written. It is also why
the transfer measurement is honest: the clinic tasks are not translations of the
ops tasks, they are the same programs executed against a different schema.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from toolsmith.governance.provenance import Provenance
from toolsmith.worlds.base import Verb

Tier = Literal["T1", "T2", "T3", "T4", "T5"]
Split = Literal["train", "val", "test", "test_hidden"]

TrapKind = Literal["unanswerable", "policy_violation", "ambiguous", "injection"]

#: What the task is for. Drives which metric a failure lands in.
TIER_PURPOSE: dict[Tier, str] = {
    "T1": "Single lookup. The floor: every model should pass these.",
    "T2": "Multi-hop, two to four calls. The bulk of the suite.",
    "T3": "Conditional and policy reasoning. Where cheap models start failing.",
    "T4": "Traps. The correct answer is often 'I cannot', 'no', or a question.",
    "T5": "Retrieval-grounded. Citation and faithfulness.",
}


class OracleStep(BaseModel):
    """One gold tool call, expressed in the shared grammar."""

    model_config = ConfigDict(extra="forbid")

    verb: Verb
    arguments: dict[str, Any] = Field(default_factory=dict)
    expect_ok: bool = True
    """False for steps that are *supposed* to fail, such as the refused
    privileged call in a policy-violation trap. A trajectory that succeeds where
    the oracle failed is wrong, not lucky."""

    note: str = ""

    def signature(self, tool_name: str) -> str:
        return f"{tool_name}({json.dumps(self.arguments, sort_keys=True, default=str)})"


class InjectionSpec(BaseModel):
    """An instruction to plant inside a tool result during this task."""

    model_config = ConfigDict(extra="forbid")

    verb: Verb
    payload: str
    match: dict[str, Any] = Field(default_factory=dict)
    field_name: str = "notes"
    lure: str = ""
    """What the injection is trying to make the agent do, in plain words. Used
    to group the failure gallery by attack rather than by symptom."""


class Task(BaseModel):
    """One evaluable request, with its ground truth attached."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    world: str
    tier: Tier
    template: str
    prompt: str

    program: list[OracleStep] = Field(default_factory=list)
    """The gold trajectory. Executed in a fresh sandbox to produce ground truth."""

    oracle_answer: str = ""
    """The gold answer as a sentence, for display and for judged dimensions."""

    answer_keys: list[str] = Field(default_factory=list)
    """The facts that must appear in a response for it to be correct.

    Grading a free-text answer by string equality punishes phrasing; grading it
    with a model puts a model in the loop of its own evaluation. Keys are the
    middle path: the specific values a correct answer cannot omit (an id, a
    status, a number, a date), matched after normalisation. Every key must be
    present. This is computed by code and is half of pass@1."""

    oracle_state_diff: str = ""
    """Signature of the state change the gold program produces. Empty for
    read-only tasks, which is itself an assertion: a read-only task that mutates
    the world has failed even if its answer is right."""

    oracle_calls: list[str] = Field(default_factory=list)
    expected_citations: list[str] = Field(default_factory=list)

    # -- traps --------------------------------------------------------------
    is_trap: bool = False
    trap_kind: TrapKind | None = None
    expected_behaviour: Literal["answer", "abstain", "refuse", "clarify", "ignore_and_answer"] = (
        "answer"
    )
    injections: list[InjectionSpec] = Field(default_factory=list)

    # -- bookkeeping --------------------------------------------------------
    split: Split = "train"
    seed: int = 0
    tags: list[str] = Field(default_factory=list)
    difficulty: int = Field(default=1, ge=1, le=5)
    provenance: Provenance

    @property
    def n_oracle_calls(self) -> int:
        return len(self.program)

    @property
    def mutating(self) -> bool:
        return bool(self.oracle_state_diff)

    def content_key(self) -> str:
        """Identity for decontamination: the prompt, normalised.

        Deliberately the prompt alone. Two tasks with the same question and
        different ids are the same task for leakage purposes, whatever their
        oracle programs look like.
        """
        return " ".join(self.prompt.lower().split())

    def program_key(self) -> str:
        """Identity independent of wording.

        Two tasks with the same gold program and the same answer are the same
        task, however differently they are phrased. This is what the generator
        deduplicates on and what decontamination uses to tell "asked twice" from
        "asked about a different row".
        """
        payload = {
            "world": self.world,
            "program": [(s.verb.value, s.arguments, s.expect_ok) for s in self.program],
            "answer_keys": sorted(self.answer_keys),
            "behaviour": self.expected_behaviour,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def fingerprint(self) -> str:
        payload = {
            "world": self.world,
            "tier": self.tier,
            "prompt": self.content_key(),
            "program": [(s.verb.value, s.arguments) for s in self.program],
            "answer": self.oracle_answer,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


class TaskSuite(BaseModel):
    """A generated dataset plus everything needed to trust it."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[Task]
    world_digests: dict[str, str]
    seed: int
    generated_by: str = "toolsmith.tasks.generate"

    def by_tier(self) -> dict[str, list[Task]]:
        out: dict[str, list[Task]] = {}
        for task in self.tasks:
            out.setdefault(task.tier, []).append(task)
        return out

    def by_split(self) -> dict[str, list[Task]]:
        out: dict[str, list[Task]] = {}
        for task in self.tasks:
            out.setdefault(task.split, []).append(task)
        return out

    def counts(self) -> dict[str, dict[str, int]]:
        table: dict[str, dict[str, int]] = {}
        for task in self.tasks:
            row = table.setdefault(task.world, {})
            row[task.tier] = row.get(task.tier, 0) + 1
            row["total"] = row.get("total", 0) + 1
        return table
