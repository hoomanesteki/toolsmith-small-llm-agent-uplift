"""``toolsmith tasks``: generate, inspect and audit the task suite."""

from __future__ import annotations

import json

import typer

from toolsmith.cli._ui import console, kv, rule, table, verdict
from toolsmith.tasks import store
from toolsmith.tasks.models import TIER_PURPOSE

app = typer.Typer(no_args_is_help=True)


@app.command("build")
def build(
    total: int = typer.Option(8000, "--total", "-n", help="Target task count across all worlds."),
    seed: int = typer.Option(20260726, "--seed", help="Generation seed."),
    seal: bool = typer.Option(
        True, "--seal/--no-seal", help="Write the hidden-split hash. Refuses to overwrite."
    ),
) -> None:
    """Generate tasks, execute every oracle program, split, and seal.

    Every task is executed before it enters the dataset. The rejection rate is
    printed and written to the summary, because a generator with a silent
    invalid rate is a generator whose numbers mean nothing.
    """
    from toolsmith.tasks import assign_splits, build_manifest, generate, seal_hidden_split
    from toolsmith.tasks.splits import write_manifest

    rule("Generating")
    suite, report = generate(total=total, seed=seed)
    suite = assign_splits(suite)

    kv("accepted", report.accepted, "ok")
    kv("rejected by oracle verification", report.rejected, "warn" if report.rejected else "dim")
    kv("rejection rate", f"{report.rejection_rate:.2%}")
    if report.reasons:
        t = table("Rejection reason", "# Tasks")
        for reason, count in list(report.reasons.items())[:8]:
            t.add_row(reason, str(count))
        console.print(t)

    rule("Composition")
    summary = store.summarise(suite)
    t = table("Tier", "# Tasks", "What it measures")
    for tier, count in summary.by_tier.items():
        t.add_row(tier, str(count), TIER_PURPOSE[tier])  # type: ignore[index]
    console.print(t)

    w = table("World", "# Tasks", "Tiers")
    for world, counts in summary.by_world.items():
        tiers = ", ".join(k for k in sorted(counts) if k != "total")
        w.add_row(world, str(counts["total"]), tiers)
    console.print(w)

    s = table("Split", "# Tasks", "Share")
    total_tasks = summary.total
    for split, count in summary.by_split.items():
        s.add_row(split, str(count), f"{count / total_tasks:.1%}")
    console.print(s)

    kv("traps (T4)", summary.traps)
    kv("tasks that mutate state", summary.mutating)
    kv("tasks carrying an injection", summary.with_injections)

    rule("Writing")
    kv("dataset", str(store.write_tasks(suite.tasks)), "key")
    kv("readable sample", str(store.write_sample(suite)), "key")
    summary.generation = report.to_dict()
    kv("summary", str(store.write_summary(summary)), "key")
    kv("manifest", str(write_manifest(build_manifest(suite), store.MANIFEST_PATH)), "key")

    if seal:
        if store.SEAL_PATH.exists():
            existing = store.SEAL_PATH.read_text(encoding="utf-8").strip()
            from toolsmith.tasks import hidden_digest

            fresh = hidden_digest(suite.tasks)
            if existing != fresh:
                console.print(
                    "\n  [bad]The hidden split changed.[/bad] The committed seal was written "
                    "before any optimisation run, and that timestamp is the evidence behind "
                    "the contamination claim."
                )
                console.print(
                    f"  [dim]committed {existing[:24]}...\n  regenerated {fresh[:24]}...[/dim]"
                )
                console.print(
                    "  [dim]If this change is intentional (new templates, new seed), delete "
                    "data/tasks/hidden_split.sha256 and re-seal in a separate commit that "
                    "says so.[/dim]"
                )
                raise typer.Exit(1)
            kv("seal", "unchanged", "ok")
        else:
            kv("seal", str(seal_hidden_split(suite, store.SEAL_PATH)), "key")

    verdict(True, f"{summary.total} verified tasks written")


@app.command("show")
def show(
    task_id: str = typer.Argument("", help="A task id. Omit to show a random one per tier."),
    tier: str = typer.Option("", "--tier", help="Filter by tier."),
    world: str = typer.Option("", "--world", help="Filter by world."),
) -> None:
    """Print a task with its oracle program, answer keys and expected behaviour."""
    tasks = store.read_tasks()
    if task_id:
        chosen = [t for t in tasks if t.task_id == task_id]
        if not chosen:
            console.print(f"  [bad]no task {task_id!r}[/bad]")
            raise typer.Exit(1)
    else:
        pool = [
            t for t in tasks if (not tier or t.tier == tier) and (not world or t.world == world)
        ]
        seen: set[str] = set()
        chosen = []
        for task in pool:
            if task.tier not in seen:
                seen.add(task.tier)
                chosen.append(task)

    for task in chosen:
        rule(f"{task.task_id}  [dim]{task.template}[/dim]")
        kv("world", task.world, "key")
        kv("tier", f"{task.tier}  [dim]{TIER_PURPOSE[task.tier]}[/dim]")
        kv("split", task.split)
        kv("difficulty", "*" * task.difficulty)
        console.print(f'\n  [head]"{task.prompt}"[/head]\n')
        p = table("#", "Verb", "Arguments", "Expect")
        for i, step in enumerate(task.program):
            p.add_row(
                str(i),
                step.verb.value,
                json.dumps(step.arguments, sort_keys=True)[:70],
                "ok" if step.expect_ok else "[warn]refused[/warn]",
            )
        console.print(p)
        kv("oracle answer", task.oracle_answer)
        kv("answer keys", ", ".join(task.answer_keys), "key")
        if task.expected_citations:
            kv("expected citations", ", ".join(task.expected_citations))
        if task.oracle_state_diff:
            kv("mutates state", "yes", "warn")
        if task.is_trap:
            kv("trap", f"{task.trap_kind} -> expect {task.expected_behaviour}", "warn")
        for injection in task.injections:
            kv("injection", f"[bad]{injection.payload[:90]}[/bad]")
            kv("  lure", injection.lure)


@app.command("verify")
def verify(
    limit: int = typer.Option(400, "--limit", help="How many tasks to re-execute."),
) -> None:
    """Re-execute a sample of oracle programs against freshly built worlds.

    The dataset was verified at generation time. This re-verifies it against a
    rebuild, which is what catches a world change that silently invalidated
    ground truth.
    """
    import random

    from toolsmith.tasks import verify_task
    from toolsmith.worlds import all_worlds, build_world, get_world

    tasks = store.read_tasks()
    builds = {k: build_world(w) for k, w in all_worlds().items()}
    sample = random.Random(11).sample(tasks, min(limit, len(tasks)))

    rule("Re-verifying oracle programs")
    kv("dataset", str(store.TASKS_PATH))
    kv("sampled", len(sample))
    failures = []
    for task in sample:
        result = verify_task(task, get_world(task.world), builds[task.world])
        if not result.valid:
            failures.append((task.task_id, result.reasons[0]))

    if failures:
        t = table("Task", "Why it no longer verifies")
        for task_id, reason in failures[:15]:
            t.add_row(task_id, reason)
        console.print(t)
    verdict(not failures, f"{len(sample) - len(failures)}/{len(sample)} oracle programs reproduce")
    if failures:
        raise typer.Exit(1)


@app.command("stats")
def stats() -> None:
    """Print the composition of the generated suite."""
    summary = json.loads((store.DATA_DIR / "summary.json").read_text(encoding="utf-8"))
    rule("Task suite")
    kv("total", summary["total"], "key")
    kv("seed", summary["seed"])
    for name in ("by_tier", "by_split"):
        t = table(name.replace("by_", "").title(), "# Tasks")
        for key, value in summary[name].items():
            t.add_row(key, str(value))
        console.print(t)
    t = table("Template", "# Tasks")
    for key, value in sorted(summary["by_template"].items(), key=lambda kv: -kv[1]):
        t.add_row(key, str(value))
    console.print(t)
