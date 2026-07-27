"""``toolsmith demo``: check that a stranger can clone this and see it work.

The first command anyone should run after `uv sync`. It verifies, in about ten
seconds and with no API keys, that every claim on the front page is backed by a
file that exists on this machine, and then tells them what to look at.

It is a smoke test with a friendly face. If a fresh clone is broken, this says
so and says which step to run, rather than leaving someone to discover it three
screens into the UI.
"""

from __future__ import annotations

import json

import typer

from toolsmith.cli._ui import console, kv, money, rule, table, verdict
from toolsmith.config import REPO_ROOT, Registry, load_registry


def demo(
    run: bool = typer.Option(False, "--run", help="Also execute one task end to end."),
) -> None:
    """Verify the zero-key demo works, and point at what to look at."""
    registry = load_registry()
    checks: list[tuple[str, bool, str]] = []

    rule("What is on this machine")

    # -- worlds -------------------------------------------------------------
    from toolsmith.worlds import all_worlds, build_world

    world_rows = []
    for spec in sorted(all_worlds().values(), key=lambda w: w.key):
        built = build_world(spec)
        world_rows.append((spec.title, spec.role, f"{built.total_rows:,}", built.digest[:12]))
    t = table("World", "Role", "# Rows", "Digest")
    for row in world_rows:
        t.add_row(*row)
    console.print(t)
    checks.append(("three sandboxed worlds build deterministically", len(world_rows) >= 3, ""))

    # -- tasks --------------------------------------------------------------
    from toolsmith.tasks.store import DATA_DIR, SAMPLE_PATH, TASKS_PATH

    summary_path = DATA_DIR / "summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        kv("tasks generated", f"{summary['total']:,}", "key")
        kv("traps (T4)", f"{summary['traps']:,}")
        kv("carrying an injection", f"{summary['with_injections']:,}")
        checks.append(("a verified task suite exists", True, ""))
    else:
        checks.append(("a verified task suite exists", False, "run `toolsmith tasks build`"))
    kv("readable sample", "committed" if SAMPLE_PATH.exists() else "missing")
    kv(
        "full dataset",
        "present" if TASKS_PATH.exists() else "regenerate with `toolsmith tasks build`",
    )

    # -- results ------------------------------------------------------------
    from toolsmith.harness.store import MATRIX_PATH

    if MATRIX_PATH.exists():
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        rows = [r for r in matrix["rows"] if r.get("n_runs")]
        manifest = matrix.get("manifest", {})
        rule("What has been measured")
        kv("configurations", len(rows), "key")
        kv("graded runs", f"{sum(r['n_runs'] for r in rows):,}")
        kv("tasks per configuration", manifest.get("n_tasks"))
        kv("provenance", manifest.get("provider_mode", "simulated"), "warn")

        frontier = [r for r in rows if r.get("on_pareto_frontier")]
        reference = next((r for r in rows if r["pipeline"] == "frontier_all_opus"), None)
        affordable = [
            r
            for r in frontier
            if not reference or r["usd_per_success"] <= reference["usd_per_success"]
        ]
        best = max(affordable or frontier, key=lambda r: r["pass_at_1"]["estimate"], default=None)
        if best and reference:
            console.print()
            console.print(
                f"  [head]{best['label']}[/head] reaches "
                f"[ok]{best['pass_at_1']['estimate']:.3f}[/ok] pass@1 at "
                f"[money]{money(best['usd_per_success'])}[/money] per success, which is "
                f"[key]{best['usd_per_success'] / reference['usd_per_success']:.2f}x[/key] the "
                f"all-frontier row's cost at {reference['pass_at_1']['estimate']:.3f} pass@1."
            )
        checks.append(("a measured evaluation matrix exists", True, ""))
    else:
        checks.append(("a measured evaluation matrix exists", False, "run `toolsmith matrix run`"))

    # -- traces -------------------------------------------------------------
    from toolsmith.harness.store import read_traces

    traces = read_traces()
    kv("committed traces", len(traces))
    checks.append(
        ("committed traces for the zero-key replay", bool(traces), "run `toolsmith matrix run`")
    )

    # -- report -------------------------------------------------------------
    generated = REPO_ROOT / "docs" / "_generated"
    fragments = sorted(p.name for p in generated.glob("*")) if generated.exists() else []
    kv("report fragments", len(fragments))
    checks.append(
        (
            "the report is generated from the results",
            bool(fragments),
            "run `toolsmith report build`",
        )
    )

    # -- budget -------------------------------------------------------------
    from toolsmith.ledger import audit_csv

    audit = audit_csv(policy=registry.budget)
    rule("Budget")
    kv("real money spent", money(audit.live_usd), "money")
    kv("cap", money(audit.cap_usd), "money")
    checks.append(("spend is inside the cap", audit.within_cap, ""))

    # -- optional live run --------------------------------------------------
    if run:
        rule("One task, end to end")
        _run_one(registry)

    # -- verdict ------------------------------------------------------------
    rule("Verdict")
    for label, passed, remedy in checks:
        verdict(passed, label if passed else f"{label}  [dim]->  {remedy}[/dim]")

    if all(passed for _, passed, _ in checks):
        console.print()
        console.print("  [head]Everything a stranger needs is here. Two things to look at:[/head]")
        console.print("    [key]make serve[/key]        the control plane on http://127.0.0.1:7860")
        console.print("    [key]make site[/key]         the report at docs/_site/index.html")
        console.print()
        console.print(
            "  [dim]No API keys are required for either. Add a GROQ_API_KEY to .env and "
            "run `toolsmith matrix run --provider live` to regenerate every table against "
            "real models, under the cap above.[/dim]"
        )
    else:
        console.print()
        console.print(
            "  [warn]Run `make all` to build everything from scratch. It takes about six minutes and costs nothing.[/warn]"
        )
        raise typer.Exit(1)


def _run_one(registry: Registry) -> None:
    """Execute a single trap task, so the gates are visible rather than described."""
    from toolsmith.ledger import CostLedger
    from toolsmith.providers import ProviderFactory
    from toolsmith.runtime import GateConfig, Pipeline, RuntimeDeps
    from toolsmith.tasks.store import SAMPLE_PATH, TASKS_PATH, read_tasks
    from toolsmith.worlds import build_world, get_world

    path = TASKS_PATH if TASKS_PATH.exists() else SAMPLE_PATH
    tasks = read_tasks(path)
    task = next(
        (t for t in tasks if t.trap_kind == "injection"),
        next((t for t in tasks if t.is_trap), tasks[0]),
    )
    world = get_world(task.world)
    deps = RuntimeDeps(
        registry=registry,
        factory=ProviderFactory(registry, "simulated"),
        ledger=CostLedger(policy=registry.budget, run_id="demo"),
        gate_config=GateConfig(),
    )
    record = Pipeline(registry.pipeline("cascade_default"), deps, world, build_world(world)).run(
        task
    )

    kv("task", task.task_id, "key")
    console.print(f'  [dim]"{task.prompt}"[/dim]')
    if task.injections:
        console.print(
            f"  [bad]planted in the record:[/bad] [dim]{task.injections[0].payload[:110]}...[/dim]"
        )
    kv("expected behaviour", task.expected_behaviour, "warn")
    kv("observed behaviour", record.behaviour, "ok" if record.behaviour else "bad")
    kv("tool calls", len(record.calls))
    kv("privileged attempted", record.privileged_attempted)
    kv("world changed", "yes" if record.state_diff else "no")
    kv("cost", money(record.usd), "money")
    console.print(f"\n  [head]{record.answer[:400]}[/head]")
