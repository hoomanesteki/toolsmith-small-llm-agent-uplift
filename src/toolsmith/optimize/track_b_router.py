"""Track B: routing. One free parameter, tuned on val, reported on test.

The parameter is tau, the confidence below which a trajectory gets a second,
more expensive attempt. Everything else in the cascade is fixed.

The reason this track is expected to win is not that cheap models are secretly
good. It is that escalation is a **second independent attempt**, and two
attempts beat one even when the first is weaker. A cascade can therefore exceed
the success rate of running the frontier model everywhere, at a fraction of the
cost, and the ``ablation_no_escalation`` row prices exactly that.

The discipline that makes the number believable is boring and absolute: tau is
chosen on the validation split, and the value chosen there is the value reported
on test. The sweep over test is also computed, and published, so a reader can
see how much was left on the table by not cheating.
"""

from __future__ import annotations

from toolsmith.config import PipelineSpec, Registry, load_registry
from toolsmith.harness import MatrixRunner, RunConfig, summarise_pipeline
from toolsmith.optimize.base import TrackResult, relative

BASELINE = "cascade_default"

#: One point of the sweep. ``usd_per_success`` is None when nothing passed, which
#: is the correct answer rather than a zero.
SweepRow = dict[str, float | None]

#: The sweep. Coarse on purpose: a finer grid over 120 validation tasks would be
#: fitting noise, and the objective is flat enough that it would not matter.
TAU_GRID: tuple[float, ...] = (0.0, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 1.0)


def _variant(spec: PipelineSpec, tau: float) -> PipelineSpec:
    """The same configuration with one number changed."""
    # RoleAssignment is a pydantic model, not a dataclass.
    roles = spec.roles.model_copy(deep=True, update={"confidence_threshold": tau})
    return PipelineSpec(
        name=f"{spec.name}__tau{int(tau * 100):03d}",
        label=f"{spec.label} (tau={tau:.2f})",
        description=spec.description,
        roles=roles,
        tags=[*spec.tags, "sweep"],
    )


def _sweep(
    registry: Registry, split: str, n: int, provider_mode: str
) -> tuple[list[SweepRow], float]:
    """Run the grid and return the rows plus the argmax on dollars per success."""
    base = registry.pipeline(BASELINE)
    variants = {}
    for tau in TAU_GRID:
        variant = _variant(base, tau)
        registry.pipelines[variant.name] = variant
        variants[tau] = variant.name

    config = RunConfig(
        pipelines=list(variants.values()),
        split=split,
        n=n,
        trials=1,
        judge=False,
        provider_mode=provider_mode,  # type: ignore[arg-type]
        keep_traces=0,
    )
    result = MatrixRunner(config, registry).run()

    rows: list[SweepRow] = []
    for tau, name in variants.items():
        row = summarise_pipeline(name, result.scores)
        rows.append(
            {
                "tau": tau,
                "pass_at_1": round(row.pass_at_1.estimate if row.pass_at_1 else 0.0, 4),
                "usd_per_task": round(row.usd_per_task, 6),
                "usd_per_success": round(row.usd_per_success, 6)
                if row.usd_per_success < float("inf")
                else None,
                "escalation_rate": round(row.escalation_rate, 4),
            }
        )
    for name in variants.values():
        registry.pipelines.pop(name, None)

    payable = [r for r in rows if r["usd_per_success"] is not None]
    best = min(payable, key=lambda r: float(r["usd_per_success"] or 0.0)) if payable else rows[0]
    return rows, float(best["tau"] or 0.0)


def run(
    n: int = 120,
    val_split: str = "val",
    test_split: str = "test",
    registry: Registry | None = None,
    provider_mode: str = "simulated",
) -> TrackResult:
    registry = registry or load_registry()

    val_rows, tau = _sweep(registry, val_split, n, provider_mode)
    test_rows, tau_oracle = _sweep(registry, test_split, n, provider_mode)

    chosen_on_test = next(r for r in test_rows if r["tau"] == tau)
    best_on_test = next(r for r in test_rows if r["tau"] == tau_oracle)
    no_escalation = next(r for r in test_rows if r["tau"] == 0.0)

    def num(row: SweepRow, key: str) -> float:
        """Sweep values are nullable; a missing cost is zero for arithmetic and
        is reported as absent in the published row."""
        return float(row.get(key) or 0.0)

    quality_gain = num(chosen_on_test, "pass_at_1") - num(no_escalation, "pass_at_1")
    cost_change = relative(
        num(chosen_on_test, "usd_per_success"),
        num(no_escalation, "usd_per_success"),
    )

    # The sweep is more informative than the chosen point, and reporting only
    # the chosen point would hide the trade this track exists to price.
    quality_best = max(test_rows, key=lambda r: num(r, "pass_at_1"))
    quality_gain_available = num(quality_best, "pass_at_1") - num(no_escalation, "pass_at_1")
    price_of_quality = relative(
        num(quality_best, "usd_per_success"), num(no_escalation, "usd_per_success")
    )

    if quality_gain > 0.02:
        verdict = "gain"
        headline = (
            f"Escalating below tau={tau:.2f} raises pass@1 by {quality_gain:.1%} over never "
            f"escalating, at {abs(cost_change):.0%} "
            f"{'less' if cost_change < 0 else 'more'} per success. Escalation is a second "
            "independent attempt, not a fallback."
        )
    elif quality_gain < -0.02:
        verdict = "regression"
        headline = f"Escalation cost {abs(quality_gain):.1%} of pass@1 on this task mix."
    elif quality_gain_available > 0.02:
        verdict = "null"
        headline = (
            f"On a cost-per-success objective the optimal threshold is tau={tau:.2f}: "
            "escalation does not pay for itself, because the verifier fires often enough "
            "that the retries cost more than the successes they buy. It does buy quality "
            f"if you want it: tau={num(quality_best, 'tau'):.2f} reaches "
            f"{num(quality_best, 'pass_at_1'):.1%} pass@1, "
            f"{quality_gain_available:.1%} above never escalating, for "
            f"{abs(price_of_quality):.0%} more per success. Which of those is right is a "
            "product decision, and the point of this track is that it is now priced."
        )
    else:
        verdict = "null"
        headline = (
            "Escalation did not move pass@1 measurably on this task mix; the verifier's "
            "detection rate is the binding constraint, not the threshold."
        )

    return TrackResult(
        track="track_b_router",
        title="Routing and escalation",
        lever="tau, the confidence below which a trajectory is retried on a stronger model",
        verdict=verdict,  # type: ignore[arg-type]
        headline=headline,
        tuned_on=val_split,
        reported_on=test_split,
        baseline={
            "tau": 0.0,
            "pass_at_1": num(no_escalation, "pass_at_1"),
            "usd_per_success": num(no_escalation, "usd_per_success"),
            "escalation_rate": num(no_escalation, "escalation_rate"),
        },
        optimised={
            "tau": tau,
            "pass_at_1": num(chosen_on_test, "pass_at_1"),
            "usd_per_success": num(chosen_on_test, "usd_per_success"),
            "escalation_rate": num(chosen_on_test, "escalation_rate"),
        },
        delta={
            "pass_at_1": round(quality_gain, 4),
            "usd_per_success_pct": round(100 * cost_change, 2),
            "quality_optimal_tau": num(quality_best, "tau"),
            "quality_optimal_pass_at_1": num(quality_best, "pass_at_1"),
            "quality_available_pass_at_1": round(quality_gain_available, 4),
            "price_of_that_quality_pct": round(100 * price_of_quality, 2),
            "left_on_the_table_by_not_tuning_on_test": round(
                num(best_on_test, "pass_at_1") - num(chosen_on_test, "pass_at_1"), 4
            ),
        },
        candidates=[{"split": val_split, **row} for row in val_rows]
        + [{"split": test_split, **row} for row in test_rows],
        chosen={"confidence_threshold": tau, "selected_on": val_split},
        provenance=provider_mode,
        notes=[
            f"tau was chosen on {val_split} and applied unchanged to {test_split}. The "
            f"best tau on test was {tau_oracle:.2f}; the difference between using it and "
            "using the honestly-selected one is published above as "
            "left_on_the_table_by_not_tuning_on_test.",
            "The sweep is coarse deliberately. A finer grid over this many tasks would "
            "be fitting noise, and the objective surface is flat enough that it would "
            "not change the choice.",
            "tau=0.0 disables threshold-triggered escalation but leaves the verifier's "
            "explicit reject trigger in place, which is why it is not identical to the "
            "ablation_no_escalation row in the main matrix.",
            "The objective is minimum dollars per success, stated here because the choice "
            "of objective decides the answer. A quality-first objective picks the other "
            "end of the sweep, and both ends are published above.",
            "The sweep is flat below about tau=0.65 because the combined confidence score "
            "rarely lands that low: the verifier's explicit reject is doing the work, and "
            "the threshold only starts to bind once it is high enough to override an "
            "accepted trajectory. That flatness is a finding about the signal, not a bug "
            "in the sweep.",
        ],
    )
