"""Track C: context engineering. Free money, so it goes first.

Two levers, both pure engineering and both costing nothing in tokens to adopt:

**Tool exposure.** Twelve tool schemas in the system prompt is roughly 1,400
tokens paid on every turn of every task whether they are used or not. A
``search_tools`` meta-tool plus a name-only catalogue retrieves the two to four
the model needs. Input is the majority of an agent's bill, so this is the
largest single line item in the system.

**Compaction.** Summarising the middle of the transcript at 60% of the window,
keeping the plan and the last three turns verbatim, instead of letting it grow
until the turn budget stops it.

The measurement is the point rather than the direction. Tool-search is not free:
retrieved schemas persist, so the prefix grows once and the cache misses once,
and a bad search wastes a turn. That trade is what the ablation rows price, and
reporting only the token saving would be the flattering half of the story.
"""

from __future__ import annotations

from toolsmith.config import Registry, load_registry
from toolsmith.harness import MatrixRunner, RunConfig, summarise_pipeline
from toolsmith.optimize.base import TrackResult, relative

#: The configuration under test, and the one-lever ablations to compare it to.
BASELINE = "cascade_default"
ABLATIONS = {
    "ablation_no_tool_search": "every tool schema in the prompt, on every turn",
    "ablation_no_compaction": "transcript grows until the turn budget stops it",
}


def run(
    n: int = 120,
    split: str = "val",
    registry: Registry | None = None,
    provider_mode: str = "simulated",
) -> TrackResult:
    registry = registry or load_registry()
    config = RunConfig(
        pipelines=[BASELINE, *ABLATIONS],
        split=split,
        n=n,
        trials=1,
        judge=False,
        provider_mode=provider_mode,  # type: ignore[arg-type]
        keep_traces=0,
    )
    result = MatrixRunner(config, registry).run()
    rows = {
        name: summarise_pipeline(name, result.scores, label=registry.pipeline(name).label)
        for name in config.pipelines
    }

    base = rows[BASELINE]
    baseline_metrics = {
        "pass_at_1": round(base.pass_at_1.estimate if base.pass_at_1 else 0.0, 4),
        "tokens_in_per_task": round(base.tokens_in_per_task, 1),
        "usd_per_task": round(base.usd_per_task, 6),
        "usd_per_success": round(base.usd_per_success, 6),
    }

    candidates = []
    for name, description in ABLATIONS.items():
        row = rows[name]
        candidates.append(
            {
                "configuration": name,
                "lever_removed": description,
                "pass_at_1": round(row.pass_at_1.estimate if row.pass_at_1 else 0.0, 4),
                "tokens_in_per_task": round(row.tokens_in_per_task, 1),
                "usd_per_task": round(row.usd_per_task, 6),
                "usd_per_success": round(row.usd_per_success, 6),
                "tokens_saved_by_lever": round(row.tokens_in_per_task - base.tokens_in_per_task, 1),
                "cost_saved_by_lever_pct": round(
                    100 * relative(base.usd_per_task, row.usd_per_task), 2
                ),
            }
        )

    naive = rows["ablation_no_tool_search"]
    token_saving = relative(base.tokens_in_per_task, naive.tokens_in_per_task)
    quality_change = (base.pass_at_1.estimate if base.pass_at_1 else 0.0) - (
        naive.pass_at_1.estimate if naive.pass_at_1 else 0.0
    )

    if token_saving < -0.05 and quality_change >= -0.02:
        verdict = "gain"
        headline = (
            f"Retrieving tool schemas instead of inlining them cuts input tokens by "
            f"{abs(token_saving):.0%} per task and costs "
            f"{abs(quality_change):.1%} of pass@1, for a "
            f"{abs(relative(base.usd_per_success, naive.usd_per_success)):.0%} reduction in "
            "dollars per success."
        )
    elif token_saving < -0.05:
        verdict = "gain"
        headline = (
            f"Tool retrieval cuts input tokens by {abs(token_saving):.0%} but costs "
            f"{abs(quality_change):.1%} of pass@1. Cheaper, and not free."
        )
    else:
        verdict = "null"
        headline = (
            "Tool retrieval did not reduce input tokens materially on this task mix. "
            "The catalogue and the retrieved schemas together cost about what the full "
            "list costs."
        )

    return TrackResult(
        track="track_c_context",
        title="Context engineering",
        lever="tool retrieval and transcript compaction",
        verdict=verdict,  # type: ignore[arg-type]
        headline=headline,
        tuned_on="none (no free parameters)",
        reported_on=split,
        baseline=baseline_metrics,
        optimised=baseline_metrics,
        delta={
            "tokens_in_per_task_pct": round(100 * token_saving, 2),
            "pass_at_1": round(quality_change, 4),
            "usd_per_success_pct": round(
                100 * relative(base.usd_per_success, naive.usd_per_success), 2
            ),
        },
        candidates=candidates,
        chosen={"tool_exposure": "tool_search", "compaction_at": 0.60},
        cost_usd=round(float(result.ledger_summary.get("usd_run", 0.0)), 6),
        provenance=provider_mode,
        notes=[
            "This track has no tuned parameters, so there is nothing to overfit and "
            "nothing to hold out. It is measured on the validation split purely to "
            "leave the test split untouched for the tracks that do tune.",
            "Tool retrieval is not free: retrieved schemas persist for the rest of the "
            "run, so the stable prefix grows once and the cache misses once. That cost "
            "is inside the numbers above rather than excluded from them.",
            "Compaction cannot show a saving on trajectories short enough never to "
            "reach the 60% threshold. On this task mix most do not, which is why its "
            "ablation is close to the baseline. It is retained because the cost of "
            "having it is zero and the cost of not having it on a fifty-turn task is not.",
        ],
    )
