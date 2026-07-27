"""The judge panel: three seats, family-disjoint, cached, and calibrated.

WHERE JUDGES BELONG
-------------------
Not on the headline metric. ``pass@1`` is executed. Judges grade the five
dimensions execution cannot see: whether the tone was right, whether the answer
padded, whether a citation actually supports the sentence it is attached to.
Their value is diagnostic.

THREE RULES
-----------
**Family-disjoint from the system under test.** A model grading its own family
is measurably worse at it: GPT-4 self-correcting on GSM8K falls 95.5 to 91.5 to
89.0 across rounds (arXiv 2310.01798). The panel drops any seat that shares a
family with the configuration being judged and says so on the row, rather than
quietly scoring anyway.

**Majority vote, disagreements retained.** Where the panel splits, the item is
queued for human review in the UI. A judge panel that only reports its
consensus has thrown away its most useful output.

**Cached by content.** The key is ``hash(rubric, response, task)``, so re-running
the matrix after a code change costs nothing in judge tokens. On a fifteen-row
matrix that is the difference between an afternoon and a fortnight.

Also here: the cheap-judge ablation. Running the same panel with a $0.075/M
model and publishing the kappa gap answers "can a cheap model grade as well as
a frontier one" with a number instead of an opinion.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from toolsmith.config import Registry, Rubric
from toolsmith.ledger import CostLedger, LedgerEntry
from toolsmith.providers import LLMRequest, Message, ProviderFactory, SimContext
from toolsmith.runtime.prompts import get_bundle
from toolsmith.runtime.record import RunRecord
from toolsmith.tasks.models import Task

#: Default seats. Three families, none of which is a system under test in the
#: default matrix. The runner drops any that collide and records the drop.
DEFAULT_PANEL: tuple[str, ...] = ("groq-oss-120b", "gemini-20-flash", "mistral-medium")

#: The cheap-judge ablation seat.
CHEAP_PANEL: tuple[str, ...] = ("groq-oss-20b",)


@dataclass(slots=True)
class Judgment:
    """One seat's scores for one response."""

    judge_model: str
    scores: dict[str, int]
    rationale: str = ""
    usd: float = 0.0
    cached: bool = False

    @property
    def mean(self) -> float:
        return sum(self.scores.values()) / len(self.scores) if self.scores else 0.0


@dataclass(slots=True)
class PanelVerdict:
    """What the panel concluded, and where it disagreed."""

    task_id: str
    pipeline: str
    judgments: list[Judgment] = field(default_factory=list)
    dropped_seats: dict[str, str] = field(default_factory=dict)
    """Seat -> why it was excluded. Almost always a family collision."""

    @property
    def dimensions(self) -> list[str]:
        return sorted({d for j in self.judgments for d in j.scores})

    def median_scores(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for dimension in self.dimensions:
            values = sorted(j.scores[dimension] for j in self.judgments if dimension in j.scores)
            if values:
                out[dimension] = float(values[len(values) // 2])
        return out

    def disagreement(self) -> float:
        """Largest spread across seats on any dimension. High means look at it."""
        spreads = []
        for dimension in self.dimensions:
            values = [j.scores[dimension] for j in self.judgments if dimension in j.scores]
            if len(values) > 1:
                spreads.append(max(values) - min(values))
        return float(max(spreads)) if spreads else 0.0

    @property
    def contested(self) -> bool:
        """Worth a human's time. Two seats two points apart is a real split."""
        return self.disagreement() >= 2.0

    @property
    def usd(self) -> float:
        return sum(j.usd for j in self.judgments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "pipeline": self.pipeline,
            "scores": self.median_scores(),
            "per_judge": {j.judge_model: j.scores for j in self.judgments},
            "disagreement": self.disagreement(),
            "contested": self.contested,
            "dropped_seats": self.dropped_seats,
            "usd": round(self.usd, 8),
        }


class JudgeCache:
    """Content-addressed, on disk, so a re-run costs nothing."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}
        if path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(rubric: Rubric, judge_model: str, response: str, task_id: str) -> str:
        blob = json.dumps(
            {
                "rubric": rubric.name,
                "dimensions": rubric.names,
                "judge": judge_model,
                "response": response,
                "task": task_id,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def get(self, key: str) -> dict[str, Any] | None:
        found = self._data.get(key)
        if found is None:
            self.misses += 1
        else:
            self.hits += 1
        return found

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value

    def flush(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=0, sort_keys=True), encoding="utf-8")
        return self.path

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class JudgePanel:
    """Runs the seats, drops the conflicted ones, and records why."""

    def __init__(
        self,
        registry: Registry,
        factory: ProviderFactory,
        ledger: CostLedger,
        rubric: Rubric,
        seats: tuple[str, ...] = DEFAULT_PANEL,
        cache: JudgeCache | None = None,
    ) -> None:
        self.registry = registry
        self.factory = factory
        self.ledger = ledger
        self.rubric = rubric
        self.seats = seats
        self.cache = cache
        self.prompts = get_bundle("base")

    def eligible_seats(self, pipeline_models: set[str]) -> tuple[list[str], dict[str, str]]:
        """Seats that do not share a family with any model under test."""
        families = {
            self.registry.model(key).family
            for key in pipeline_models
            if key in self.registry.models
        }
        keep, dropped = [], {}
        for seat in self.seats:
            spec = self.registry.models.get(seat)
            if spec is None:
                dropped[seat] = "not in configs/models.yaml"
            elif spec.family in families:
                dropped[seat] = f"shares the {spec.family} family with a model under test"
            else:
                keep.append(seat)
        return keep, dropped

    def judge(self, record: RunRecord, task: Task) -> PanelVerdict:
        under_test = {v for v in record.models_used.values() if v}
        seats, dropped = self.eligible_seats(under_test)
        verdict = PanelVerdict(
            task_id=task.task_id, pipeline=record.pipeline, dropped_seats=dropped
        )

        for seat in seats:
            verdict.judgments.append(self._one_seat(seat, record, task))
        return verdict

    def _one_seat(self, seat: str, record: RunRecord, task: Task) -> Judgment:
        key = JudgeCache.key(self.rubric, seat, record.answer, task.task_id)
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return Judgment(seat, hit["scores"], hit.get("rationale", ""), 0.0, cached=True)

        spec = self.registry.model(seat)
        provider = self.factory.get(seat)
        rubric_text = "\n".join(
            f"- {d.name}: {d.question}"
            + (
                "".join(f"\n    {k} = {v}" for k, v in sorted(d.anchors.items()))
                if d.anchors
                else ""
            )
            for d in self.rubric.dimensions
        )
        request = LLMRequest(
            messages=[
                Message("system", self.prompts.judge),
                Message(
                    "user",
                    f"Rubric (score each {self.rubric.scale[0]}-{self.rubric.scale[1]}):\n"
                    f"{rubric_text}\n\nRequest:\n{task.prompt}\n\nResponse:\n{record.answer}",
                ),
            ],
            response_format="json_object",
            meta={
                "role": "judge",
                "sim": SimContext(
                    task_id=task.task_id,
                    oracle_answer=task.oracle_answer,
                    answer_keys=list(task.answer_keys),
                    executor_was_correct=_looks_correct(record, task),
                ),
            },
        )
        response = provider.complete(request)
        usd = spec.cost_usd(response.tokens_in, response.tokens_out, response.tokens_cached_in)
        self.ledger.record(
            LedgerEntry(
                run_id=record.run_id,
                task_id=task.task_id,
                pipeline=record.pipeline,
                role="judge",
                model_key=seat,
                provider=spec.provider,
                provenance=provider.provenance,
                tokens_in=response.tokens_in,
                tokens_cached_in=response.tokens_cached_in,
                tokens_out=response.tokens_out,
                usd=usd,
                latency_s=response.latency_s,
            )
        )
        scores, rationale = _parse_scores(response.text, self.rubric)
        if self.cache is not None:
            self.cache.put(key, {"scores": scores, "rationale": rationale})
        return Judgment(seat, scores, rationale, usd)


def _looks_correct(record: RunRecord, task: Task) -> bool:
    from toolsmith.runtime.behaviour import contains_answer_key

    return all(contains_answer_key(record.answer, k) for k in task.answer_keys) and (
        record.state_diff == task.oracle_state_diff
    )


def _parse_scores(text: str, rubric: Rubric) -> tuple[dict[str, int], str]:
    """Read a judge's JSON, clamped to the rubric's scale.

    A judge that returns nothing usable scores the midpoint on every dimension
    rather than zero. Treating a parse failure as a damning verdict would let a
    flaky judge quietly bury a configuration.
    """
    low, high = rubric.scale
    midpoint = (low + high) // 2
    try:
        payload = json.loads(text)
        raw = payload.get("scores", payload)
        rationale = str(payload.get("rationale", ""))[:400]
    except (json.JSONDecodeError, TypeError, AttributeError):
        return dict.fromkeys(rubric.names, midpoint), "unparsable judge output"

    scores: dict[str, int] = {}
    for name in rubric.names:
        value = raw.get(name, midpoint) if isinstance(raw, dict) else midpoint
        try:
            scores[name] = max(low, min(high, int(value)))
        except (TypeError, ValueError):
            scores[name] = midpoint
    return scores, rationale
