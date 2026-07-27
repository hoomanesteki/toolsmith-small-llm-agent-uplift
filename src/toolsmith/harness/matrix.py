"""Aggregation: from thousands of graded runs to the table people read.

Two decisions in here carry the whole report.

**The headline is dollars per SUCCESS, not per task.** They rank models
differently, and only one of them is honest. A model that costs a third as much
per call and fails twice as often is not cheaper; you paid for the failure and
then paid again. ``E[cost] = C / p`` makes that visible in one column, and it is
the column that reverses the naive ranking.

**Every cell carries a paired-bootstrap interval.** Point estimates on 150 tasks
have intervals wide enough to swallow most of the differences people report as
findings. Publishing the interval next to the estimate is the difference between
a result and a claim.

The Pareto frontier is computed rather than eyeballed: a configuration is on it
when nothing else is both cheaper per success and at least as reliable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any

from toolsmith.harness.grading import TaskScore, pass_at_k
from toolsmith.harness.judges import PanelVerdict
from toolsmith.harness.stats import (
    Interval,
    align,
    bootstrap_mean,
    holm_bonferroni,
    mcnemar,
    wilson_interval,
)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


@dataclass
class PipelineRow:
    """One row of the published matrix."""

    pipeline: str
    label: str = ""
    tags: list[str] = field(default_factory=list)
    n_tasks: int = 0
    n_runs: int = 0
    trials: int = 1

    # -- the headline -------------------------------------------------------
    pass_at_1: Interval | None = None
    pass_hat_k: float = 0.0
    usd_per_task: float = 0.0
    usd_per_success: float = 0.0
    """C / p. Infinite when nothing passed, which is the correct answer."""

    # -- components ---------------------------------------------------------
    state_ok_rate: float = 0.0
    answer_ok_rate: float = 0.0
    behaviour_ok_rate: float = 0.0
    tool_selection_accuracy: float = 0.0
    param_accuracy: float = 0.0
    calls_vs_oracle: float = 1.0
    schema_invalid_rate: float = 0.0

    # -- safety -------------------------------------------------------------
    abstain_recall: float | None = None
    abstain_recall_ci: tuple[float, float] | None = None
    over_refusal_rate: float = 0.0
    injection_resistance: float | None = None
    injection_resistance_ci: tuple[float, float] | None = None
    policy_violation_rate: float = 0.0

    # -- grounding ----------------------------------------------------------
    citation_precision: float | None = None
    citation_recall: float | None = None
    unsupported_claims_per_task: float = 0.0

    # -- economics ----------------------------------------------------------
    tokens_in_per_task: float = 0.0
    tokens_out_per_task: float = 0.0
    cache_hit_rate: float = 0.0
    input_share: float = 0.0
    executor_input_share: float = 0.0
    spend_by_role: dict[str, float] = field(default_factory=dict)
    latency_p50: float = 0.0
    latency_p95: float = 0.0
    escalation_rate: float = 0.0

    # -- judged -------------------------------------------------------------
    judged_scores: dict[str, float] = field(default_factory=dict)
    judge_disagreement: float = 0.0

    # -- breakdowns ---------------------------------------------------------
    by_tier: dict[str, dict[str, float]] = field(default_factory=dict)
    by_world: dict[str, dict[str, float]] = field(default_factory=dict)
    failure_modes: dict[str, int] = field(default_factory=dict)
    provenance: str = "simulated"
    on_pareto_frontier: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pass_at_1"] = self.pass_at_1.to_dict() if self.pass_at_1 else None
        return payload


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarise_pipeline(
    pipeline: str,
    scores: list[TaskScore],
    judgments: list[PanelVerdict] | None = None,
    label: str = "",
    tags: list[str] | None = None,
    seed: int = 20260726,
) -> PipelineRow:
    rows = [s for s in scores if s.pipeline == pipeline]
    if not rows:
        return PipelineRow(pipeline=pipeline, label=label or pipeline, tags=tags or [])

    first_trial = [s for s in rows if s.trial == 0]
    passed = [s.passed for s in first_trial]
    row = PipelineRow(
        pipeline=pipeline,
        label=label or pipeline,
        tags=tags or [],
        n_tasks=len({s.task_id for s in rows}),
        n_runs=len(rows),
        trials=max(s.trial for s in rows) + 1,
        pass_at_1=bootstrap_mean([float(p) for p in passed], seed=seed),
        pass_hat_k=pass_at_k(rows),
        state_ok_rate=_rate([s.state_ok for s in first_trial]),
        answer_ok_rate=_rate([s.answer_ok for s in first_trial]),
        behaviour_ok_rate=_rate([s.behaviour_ok for s in first_trial]),
        tool_selection_accuracy=_mean([s.tool_selection_accuracy for s in first_trial]),
        param_accuracy=_mean([s.param_accuracy for s in first_trial]),
        calls_vs_oracle=_mean([s.calls_vs_oracle for s in first_trial]),
        schema_invalid_rate=_rate([s.schema_invalid for s in first_trial]),
        over_refusal_rate=_rate([s.over_refused for s in first_trial]),
        policy_violation_rate=_rate([s.policy_violation for s in first_trial]),
        unsupported_claims_per_task=_mean([float(s.unsupported_claims) for s in first_trial]),
        tokens_in_per_task=_mean([float(s.tokens_in) for s in first_trial]),
        tokens_out_per_task=_mean([float(s.tokens_out) for s in first_trial]),
        input_share=_mean([s.input_share for s in first_trial]),
        executor_input_share=_mean([s.executor_input_share for s in first_trial]),
        latency_p50=_percentile([s.latency_s for s in first_trial], 0.50),
        latency_p95=_percentile([s.latency_s for s in first_trial], 0.95),
        escalation_rate=_rate([s.escalated for s in first_trial]),
        provenance=first_trial[0].provenance if first_trial else "simulated",
    )

    row.usd_per_task = _mean([s.usd for s in first_trial])
    rate = row.pass_at_1.estimate if row.pass_at_1 else 0.0
    row.usd_per_success = row.usd_per_task / rate if rate > 0 else float("inf")

    tokens_in = sum(s.tokens_in for s in first_trial)
    row.cache_hit_rate = (
        sum(s.tokens_cached_in for s in first_trial) / tokens_in if tokens_in else 0.0
    )

    spend: dict[str, float] = {}
    for score in first_trial:
        for role, amount in score.spend_by_role.items():
            spend[role] = spend.get(role, 0.0) + amount
    total_spend = sum(spend.values()) or 1.0
    row.spend_by_role = {k: round(v / total_spend, 4) for k, v in sorted(spend.items())}

    # -- safety, with Wilson intervals because these rates live near 1.0 ----
    traps = [s for s in first_trial if s.is_trap]
    if traps:
        hits = sum(bool(s.abstained_correctly) for s in traps)
        row.abstain_recall = hits / len(traps)
        row.abstain_recall_ci = wilson_interval(hits, len(traps))

    injected = [s for s in first_trial if s.injection_present]
    if injected:
        resisted = sum(bool(s.injection_resisted) for s in injected)
        row.injection_resistance = resisted / len(injected)
        row.injection_resistance_ci = wilson_interval(resisted, len(injected))

    grounded = [s for s in first_trial if s.citation_recall is not None]
    if grounded:
        row.citation_precision = _mean([s.citation_precision or 0.0 for s in grounded])
        row.citation_recall = _mean([s.citation_recall or 0.0 for s in grounded])

    # -- breakdowns ---------------------------------------------------------
    for key, group in _group(first_trial, lambda s: s.tier).items():
        row.by_tier[key] = {
            "n": len(group),
            "pass_at_1": round(_rate([s.passed for s in group]), 4),
            "usd_per_task": round(_mean([s.usd for s in group]), 6),
        }
    for key, group in _group(first_trial, lambda s: s.world).items():
        row.by_world[key] = {
            "n": len(group),
            "pass_at_1": round(_rate([s.passed for s in group]), 4),
            "usd_per_task": round(_mean([s.usd for s in group]), 6),
        }

    for score in first_trial:
        if score.passed:
            continue
        for mode in score.failure_modes:
            row.failure_modes[mode] = row.failure_modes.get(mode, 0) + 1
    row.failure_modes = dict(sorted(row.failure_modes.items(), key=lambda kv: -kv[1]))

    if judgments:
        mine = [j for j in judgments if j.pipeline == pipeline]
        if mine:
            dimensions = sorted({d for j in mine for d in j.median_scores()})
            row.judged_scores = {
                d: round(_mean([j.median_scores().get(d, 0.0) for j in mine]), 3)
                for d in dimensions
            }
            row.judge_disagreement = round(_mean([j.disagreement() for j in mine]), 3)
    return row


def _group(scores: list[TaskScore], key) -> dict[str, list[TaskScore]]:
    out: dict[str, list[TaskScore]] = {}
    for score in scores:
        out.setdefault(key(score), []).append(score)
    return dict(sorted(out.items()))


# ------------------------------------------------------------- comparisons --


@dataclass
class Comparison:
    left: str
    right: str
    difference: Interval
    mcnemar_p: float
    discordant: int
    significant: bool = False
    holm_threshold: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left,
            "right": self.right,
            "difference": self.difference.to_dict(),
            "mcnemar_p": round(self.mcnemar_p, 8),
            "discordant": self.discordant,
            "significant_after_holm": self.significant,
            "holm_threshold": round(self.holm_threshold, 8),
        }


def compare_all(
    scores: list[TaskScore], alpha: float = 0.05, seed: int = 20260726
) -> list[Comparison]:
    """Every pairwise comparison, paired and multiplicity-corrected."""
    by_pipeline: dict[str, dict[str, bool]] = {}
    for score in scores:
        if score.trial == 0:
            by_pipeline.setdefault(score.pipeline, {})[score.task_id] = score.passed

    comparisons: list[Comparison] = []
    p_values: dict[str, float] = {}
    for left, right in combinations(sorted(by_pipeline), 2):
        left_values, right_values, shared = align(
            {k: float(v) for k, v in by_pipeline[left].items()},
            {k: float(v) for k, v in by_pipeline[right].items()},
        )
        if not shared:
            continue
        from toolsmith.harness.stats import paired_bootstrap_difference

        difference = paired_bootstrap_difference(left_values, right_values, seed=seed)
        test = mcnemar([bool(v) for v in left_values], [bool(v) for v in right_values])
        key = f"{left}|{right}"
        p_values[key] = test.p_value
        comparisons.append(
            Comparison(
                left=left,
                right=right,
                difference=difference,
                mcnemar_p=test.p_value,
                discordant=test.discordant,
            )
        )

    corrected = holm_bonferroni(p_values, alpha)
    for comparison in comparisons:
        entry = corrected.get(f"{comparison.left}|{comparison.right}", {})
        comparison.significant = bool(entry.get("significant", False))
        comparison.holm_threshold = float(entry.get("threshold", 0.0))
    return comparisons


def pareto_frontier(rows: list[PipelineRow]) -> list[str]:
    """Configurations nothing else dominates on both cost-per-success and quality.

    Two exclusions, both deliberate.

    **Controls.** The oracle is free and perfect and would sit alone on the
    frontier, telling nobody anything.

    **Unbilled configurations.** A local model has no marginal token cost, so on
    a dollars axis it dominates everything by construction, at 44% pass@1 and
    several times the wall-clock. "Free at the margin" is not free, and a
    frontier that ranks it first is a frontier nobody can act on. Local rows are
    reported in the matrix with their latency; they are just not on this curve.
    """
    candidates = [
        r
        for r in rows
        if "control" not in r.tags
        and r.pass_at_1 is not None
        and 0.0 < r.usd_per_success < float("inf")
    ]
    frontier = []
    for row in candidates:
        dominated = any(
            other.usd_per_success <= row.usd_per_success
            and (other.pass_at_1.estimate if other.pass_at_1 else 0)
            >= (row.pass_at_1.estimate if row.pass_at_1 else 0)
            and other.pipeline != row.pipeline
            and (
                other.usd_per_success < row.usd_per_success
                or (other.pass_at_1.estimate if other.pass_at_1 else 0)
                > (row.pass_at_1.estimate if row.pass_at_1 else 0)
            )
            for other in candidates
        )
        if not dominated:
            frontier.append(row.pipeline)
    return sorted(frontier)
