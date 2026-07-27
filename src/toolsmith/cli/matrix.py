"""``toolsmith matrix``: run the evaluation and publish the numbers."""

from __future__ import annotations

import json

import typer

from toolsmith.cli._ui import console, kv, money, rule, table, verdict
from toolsmith.config import REPO_ROOT, load_registry
from toolsmith.harness.matrix import PipelineRow

app = typer.Typer(no_args_is_help=True)

CACHE_PATH = REPO_ROOT / "eval" / "cache" / "judgments.json"


@app.command("run")
def run(
    provider: str = typer.Option("simulated", "--provider", help="simulated | auto | live."),
    split: str = typer.Option("test", "--split", help="test | val | test_hidden."),
    n: int = typer.Option(180, "--n", help="Tasks, stratified by world and tier."),
    trials: int = typer.Option(3, "--trials", help="Trials per task, for pass^k."),
    pipelines: str = typer.Option("", "--pipelines", help="Comma-separated. Default: all."),
    judge: bool = typer.Option(True, "--judge/--no-judge", help="Run the judge panel."),
    seed: int = typer.Option(20260726, "--seed"),
    write: bool = typer.Option(True, "--write/--dry-run", help="Write eval/results/."),
) -> None:
    """Run every configuration over the same tasks and write the results.

    Simulated by default, which costs nothing and reproduces every published
    table. `--provider live` runs the identical code path against real APIs,
    under the ledger's hard cap.
    """
    from toolsmith.harness import (
        Manifest,
        MatrixRunner,
        RunConfig,
        compare_all,
        pareto_frontier,
        summarise_pipeline,
    )
    from toolsmith.harness.store import (
        write_judgments,
        write_manifest,
        write_matrix,
        write_results,
        write_traces,
    )
    from toolsmith.tasks.store import MANIFEST_PATH as TASK_MANIFEST

    registry = load_registry()
    names = [p.strip() for p in pipelines.split(",") if p.strip()] or sorted(registry.pipelines)
    config = RunConfig(
        pipelines=names,
        split=split,
        n=n,
        trials=trials,
        provider_mode=provider,  # type: ignore[arg-type]
        judge=judge,
        seed=seed,
    )

    rule("Evaluation matrix")
    kv("provider mode", provider, "warn" if provider != "simulated" else "key")
    kv("split", split)
    kv("configurations", len(names))
    kv("tasks", n)
    kv("trials", trials)
    kv("runs", f"{len(names) * n * trials:,}")
    if provider != "simulated":
        console.print(
            f"  [warn]This will spend real money.[/warn] The ledger cap is "
            f"{money(registry.budget.cap_usd)} and it is enforced before each call."
        )

    runner = MatrixRunner(config, registry, cache_path=CACHE_PATH)
    with console.status("[dim]running...[/dim]"):
        result = runner.run()

    rows = [
        summarise_pipeline(
            name,
            result.scores,
            result.judgments,
            label=registry.pipeline(name).label,
            tags=registry.pipeline(name).tags,
            seed=seed,
        )
        for name in names
    ]
    frontier = set(pareto_frontier(rows))
    for row in rows:
        row.on_pareto_frontier = row.pipeline in frontier
    comparisons = compare_all(result.scores, seed=seed, prefer=names)

    _print_headline(rows)
    _print_safety(rows)

    if not write:
        verdict(True, "dry run: nothing written")
        return

    task_manifest = json.loads(TASK_MANIFEST.read_text(encoding="utf-8"))
    manifest = Manifest(
        seed=seed,
        split=split,
        n_tasks=len(result.task_ids),
        trials=trials,
        provider_mode=provider,
        pipelines=names,
        world_digests=task_manifest.get("world_digests", {}),
        hidden_split_sha256=task_manifest.get("hidden_sha256", ""),
        ledger=result.ledger_summary,
        substitutions=result.substitutions,
        judge_cache_hit_rate=round(result.judge_cache_hit_rate, 4),
        wall_clock_s=round(result.wall_clock_s, 2),
        provenance_note=(
            "PROVENANCE: simulated. Every number here was produced by the production "
            "code path over a deterministic behavioural simulator seeded from published "
            "model-card priors. No API call was made and nothing was spent. Run with "
            "--provider live to regenerate these tables against real models."
            if provider == "simulated"
            else f"PROVENANCE: {provider}. Real API calls were made; see the ledger block."
        ),
    )

    rule("Writing")
    kv("results", str(write_results(result.scores)), "key")
    kv("matrix", str(write_matrix(rows, comparisons, manifest)), "key")
    kv("run manifest", str(write_manifest(manifest)), "key")
    if result.judgments:
        kv("judgments", str(write_judgments(result.judgments)), "key")
    kv("traces committed", write_traces(result.traces))
    runner.ledger.flush()
    kv("ledger", json.dumps(result.ledger_summary), "money")
    verdict(True, f"{len(result.scores):,} graded runs written")


def _print_headline(rows: list[PipelineRow]) -> None:
    rule("Cost versus quality")
    t = table(
        "Configuration",
        "pass@1 (95% CI)",
        "pass^k",
        "$ /task",
        "$ /success",
        "vs frontier",
        "Pareto",
    )
    reference = next((r for r in rows if r.pipeline == "frontier_all_opus"), None)
    baseline = reference.usd_per_success if reference and reference.usd_per_success else None
    for row in sorted(rows, key=lambda r: r.usd_per_success):
        ci = row.pass_at_1
        ratio = (
            f"{row.usd_per_success / baseline:.2f}x"
            if baseline and row.usd_per_success < float("inf")
            else "-"
        )
        style = "ok" if row.on_pareto_frontier else ""
        t.add_row(
            f"[{style}]{row.label}[/{style}]" if style else row.label,
            f"{ci.estimate:.3f} [{ci.low:.3f}, {ci.high:.3f}]" if ci else "-",
            f"{row.pass_hat_k:.3f}",
            f"{row.usd_per_task:.5f}",
            f"{row.usd_per_success:.5f}" if row.usd_per_success < float("inf") else "inf",
            ratio,
            "*" if row.on_pareto_frontier else "",
        )
    console.print(t)
    console.print(
        "  [dim]Dollars per SUCCESS, not per task. A configuration that costs a third as "
        "much and fails twice as often is not cheaper.[/dim]"
    )


def _print_safety(rows: list[PipelineRow]) -> None:
    rule("Safety and grounding")
    t = table(
        "Configuration",
        "Abstain recall",
        "Over-refusal",
        "Injection resisted",
        "Policy violations",
        "Citation recall",
    )
    for row in sorted(rows, key=lambda r: -(r.pass_at_1.estimate if r.pass_at_1 else 0)):
        t.add_row(
            row.label,
            f"{row.abstain_recall:.3f}" if row.abstain_recall is not None else "-",
            f"{row.over_refusal_rate:.3f}",
            f"{row.injection_resistance:.3f}" if row.injection_resistance is not None else "-",
            f"[bad]{row.policy_violation_rate:.3f}[/bad]" if row.policy_violation_rate else "0.000",
            f"{row.citation_recall:.3f}" if row.citation_recall is not None else "-",
        )
    console.print(t)


@app.command("compare")
def compare(
    left: str = typer.Argument(..., help="Pipeline name."),
    right: str = typer.Argument(..., help="Pipeline name."),
) -> None:
    """Paired comparison of two configurations on identical tasks."""
    from toolsmith.harness import align, mcnemar, paired_bootstrap_difference, read_results

    scores = [s for s in read_results() if s.trial == 0]
    left_map = {s.task_id: float(s.passed) for s in scores if s.pipeline == left}
    right_map = {s.task_id: float(s.passed) for s in scores if s.pipeline == right}
    a, b, shared = align(left_map, right_map)
    if not shared:
        console.print("  [bad]no shared tasks between those configurations[/bad]")
        raise typer.Exit(1)

    difference = paired_bootstrap_difference(a, b)
    test = mcnemar([bool(v) for v in a], [bool(v) for v in b])

    rule(f"{left}  vs  {right}")
    kv("shared tasks", len(shared))
    kv(f"{left} pass@1", f"{sum(a) / len(a):.3f}")
    kv(f"{right} pass@1", f"{sum(b) / len(b):.3f}")
    kv("difference", str(difference), "key")
    kv("discordant pairs", f"{test.left_only} / {test.right_only}")
    kv("McNemar exact p", f"{test.p_value:.5f}")
    console.print(
        "  [dim]Only the disagreements carry information: the tasks both got right "
        "tell you nothing about which is better.[/dim]"
    )


@app.command("show")
def show(pipeline: str = typer.Argument(..., help="Pipeline name.")) -> None:
    """Everything measured about one configuration."""
    from toolsmith.harness import read_matrix

    payload = read_matrix()
    row = next((r for r in payload["rows"] if r["pipeline"] == pipeline), None)
    if row is None:
        console.print(f"  [bad]no row for {pipeline!r}[/bad]")
        raise typer.Exit(1)

    rule(row["label"])
    for key in (
        "pass_hat_k",
        "usd_per_task",
        "usd_per_success",
        "escalation_rate",
        "input_share",
        "executor_input_share",
        "cache_hit_rate",
        "calls_vs_oracle",
    ):
        kv(key, row[key])
    if row["pass_at_1"]:
        kv(
            "pass@1",
            f"{row['pass_at_1']['estimate']:.3f} "
            f"[{row['pass_at_1']['ci_low']:.3f}, {row['pass_at_1']['ci_high']:.3f}]",
            "key",
        )

    rule("By tier")
    t = table("Tier", "n", "pass@1", "$ /task")
    for tier, values in sorted(row["by_tier"].items()):
        t.add_row(
            tier,
            str(int(values["n"])),
            f"{values['pass_at_1']:.3f}",
            f"{values['usd_per_task']:.5f}",
        )
    console.print(t)

    rule("Where it loses")
    f = table("Failure mode", "Count")
    for mode, count in list(row["failure_modes"].items())[:12]:
        f.add_row(mode, str(count))
    console.print(f)

    if row["spend_by_role"]:
        rule("Spend by role")
        s = table("Role", "Share")
        for role, share in row["spend_by_role"].items():
            s.add_row(role, f"{share:.1%}")
        console.print(s)
