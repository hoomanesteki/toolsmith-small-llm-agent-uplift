"""Calibrating the judges, and calibrating the behaviour classifier.

A judge with an unpublished kappa is a judge nobody should believe, including
the person who built it. So two things are calibrated here against human labels,
and both numbers are published with their confusion matrices.

**The behaviour classifier.** ``classify_behaviour`` decides whether a response
answered, abstained, refused or asked. That decision feeds pass@1 on the entire
T4 tier, which makes it the most load-bearing piece of non-executed logic in the
project. Its agreement with human labels is measured, not assumed.

**The judge panel.** Agreement between each seat and the human labels, and
between the cheap seat and the frontier seats, which is the ablation that
answers "can a $0.075/M model grade as well" with a number.

The label file is a plain JSONL a person edits, or that the review UI writes.
When it does not exist, the report says the judges are uncalibrated rather than
quietly omitting the section.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from toolsmith.config import REPO_ROOT
from toolsmith.harness.stats import cohens_kappa, confusion_matrix
from toolsmith.runtime.behaviour import classify_behaviour

LABELS_PATH = REPO_ROOT / "eval" / "labels" / "human_labels.jsonl"

#: The spec's floor. Below this a calibration claim is decoration.
MIN_LABELS = 150

#: Landis and Koch, the convention this field quotes without citing.
KAPPA_BANDS: tuple[tuple[float, str], ...] = (
    (0.81, "almost perfect"),
    (0.61, "substantial"),
    (0.41, "moderate"),
    (0.21, "fair"),
    (0.0, "slight"),
)


def kappa_band(kappa: float) -> str:
    for threshold, label in KAPPA_BANDS:
        if kappa >= threshold:
            return label
    return "poor"


@dataclass(slots=True)
class HumanLabel:
    """One person's verdict on one response."""

    task_id: str
    pipeline: str
    behaviour: str
    correct: bool
    notes: str = ""
    labeller: str = "owner"

    @classmethod
    def from_json(cls, line: str) -> HumanLabel:
        row = json.loads(line)
        return cls(
            task_id=row["task_id"],
            pipeline=row.get("pipeline", ""),
            behaviour=row["behaviour"],
            correct=bool(row.get("correct", False)),
            notes=row.get("notes", ""),
            labeller=row.get("labeller", "owner"),
        )


@dataclass
class CalibrationReport:
    n_labels: int = 0
    sufficient: bool = False
    behaviour_kappa: float = 0.0
    behaviour_band: str = ""
    behaviour_confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    behaviour_accuracy: float = 0.0
    judge_kappa: dict[str, float] = field(default_factory=dict)
    cheap_judge_kappa: float | None = None
    cheap_judge_gap: float | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_labels": self.n_labels,
            "sufficient": self.sufficient,
            "min_labels": MIN_LABELS,
            "behaviour_kappa": round(self.behaviour_kappa, 4),
            "behaviour_band": self.behaviour_band,
            "behaviour_accuracy": round(self.behaviour_accuracy, 4),
            "behaviour_confusion": self.behaviour_confusion,
            "judge_kappa": {k: round(v, 4) for k, v in sorted(self.judge_kappa.items())},
            "cheap_judge_kappa": self.cheap_judge_kappa,
            "cheap_judge_gap": self.cheap_judge_gap,
            "note": self.note,
        }


def read_labels(path: Path | None = None) -> list[HumanLabel]:
    path = path or LABELS_PATH
    if not path.exists():
        return []
    return [
        HumanLabel.from_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def calibrate_behaviour(
    labels: Sequence[HumanLabel], answers: Mapping[tuple[str, str], str]
) -> CalibrationReport:
    """Agreement between the classifier and the humans, per label.

    ``answers`` maps (task_id, pipeline) to the response text, so the classifier
    is re-run on exactly what the person read rather than on a stored verdict.
    """
    report = CalibrationReport(n_labels=len(labels))
    if not labels:
        report.note = (
            "No human labels found. The behaviour classifier and the judge panel are "
            "UNCALIBRATED, and every judged number in this report should be read as "
            f"provisional. Add at least {MIN_LABELS} labels to eval/labels/human_labels.jsonl "
            "or through the review screen."
        )
        return report

    human, machine = [], []
    for label in labels:
        text = answers.get((label.task_id, label.pipeline))
        if text is None:
            continue
        human.append(label.behaviour)
        machine.append(classify_behaviour(text))

    if not human:
        report.note = "Labels exist but none matched a run in this results file."
        return report

    report.n_labels = len(human)
    report.sufficient = len(human) >= MIN_LABELS
    report.behaviour_kappa = cohens_kappa(list(human), list(machine))
    report.behaviour_band = kappa_band(report.behaviour_kappa)
    report.behaviour_confusion = confusion_matrix(list(human), list(machine))
    report.behaviour_accuracy = sum(a == b for a, b in zip(human, machine, strict=True)) / len(
        human
    )
    if not report.sufficient:
        report.note = (
            f"Only {len(human)} labels; the published threshold is {MIN_LABELS}. "
            "The kappa below is indicative, not a calibration."
        )
    return report


def calibrate_judges(
    labels: Sequence[HumanLabel],
    panel_scores: Mapping[tuple[str, str], Mapping[str, Mapping[str, int]]],
    cheap_scores: Mapping[tuple[str, str], Mapping[str, Mapping[str, int]]] | None = None,
    threshold: int = 4,
) -> tuple[dict[str, float], float | None]:
    """Agreement between each seat and the human correct/incorrect verdict.

    A judge's five-point score is binarised at ``threshold`` on the correctness
    dimension, because that is the axis a human labelled. Comparing a scalar to a
    binary would be a different and much easier question.
    """
    human_by_key = {(label.task_id, label.pipeline): label.correct for label in labels}
    per_seat: dict[str, tuple[list[str], list[str]]] = {}

    def collect(
        store: Mapping[tuple[str, str], Mapping[str, Mapping[str, int]]], tag: str = ""
    ) -> None:
        for key, seats in store.items():
            truth = human_by_key.get(key)
            if truth is None:
                continue
            for seat, scores in seats.items():
                name = f"{tag}{seat}"
                human_list, machine_list = per_seat.setdefault(name, ([], []))
                human_list.append("correct" if truth else "incorrect")
                machine_list.append(
                    "correct" if scores.get("correctness", 0) >= threshold else "incorrect"
                )

    collect(panel_scores)
    if cheap_scores:
        collect(cheap_scores, tag="cheap:")

    kappas = {
        seat: cohens_kappa(human, machine) for seat, (human, machine) in per_seat.items() if human
    }
    frontier = [v for k, v in kappas.items() if not k.startswith("cheap:")]
    cheap = [v for k, v in kappas.items() if k.startswith("cheap:")]
    gap = (sum(frontier) / len(frontier) - sum(cheap) / len(cheap)) if frontier and cheap else None
    return kappas, gap


def write_label_template(
    rows: list[tuple[str, str, str, str]], path: Path | None = None, limit: int = MIN_LABELS
) -> Path:
    """Write an unlabelled file for a person to fill in.

    Chosen to be worth a human's time: contested judgments and disagreements
    first, easy consensus last. Labelling 150 items a panel already agrees on
    measures nothing.
    """
    path = path or LABELS_PATH.with_name("to_label.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for task_id, pipeline, prompt, answer in rows[:limit]:
            fh.write(
                json.dumps(
                    {
                        "task_id": task_id,
                        "pipeline": pipeline,
                        "prompt": prompt,
                        "answer": answer,
                        "behaviour": "<answer|abstain|refuse|clarify>",
                        "correct": None,
                        "notes": "",
                        "labeller": "owner",
                    }
                )
                + "\n"
            )
    return path
