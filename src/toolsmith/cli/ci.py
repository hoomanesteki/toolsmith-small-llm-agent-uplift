"""``toolsmith ci``: the gates that fail the build.

Each of these is a claim the README makes, turned into an exit code. A claim
that is not a gate is a claim that decays.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from toolsmith.cli._ui import console, kv, money, rule, table, verdict
from toolsmith.config import REPO_ROOT, load_registry

app = typer.Typer(no_args_is_help=True)


@app.command("firewall")
def firewall(
    json_out: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """No forbidden model may appear as the generator of a training row."""
    import json as jsonlib

    from toolsmith.governance import allowed_generators, scan

    registry = load_registry()
    report = scan(registry=registry)

    if json_out:
        console.print_json(jsonlib.dumps(report.to_dict()))
    else:
        rule("License firewall")
        kv("files scanned", report.files_scanned)
        kv("rows scanned", report.rows_scanned)
        kv("rows missing provenance", report.rows_without_provenance)
        if report.generators_seen:
            t = table("Generator", "# Rows")
            for name, count in sorted(report.generators_seen.items()):
                t.add_row(name, str(count))
            console.print(t)
        if report.violations:
            t = table("File", "Line", "Generator", "Reason")
            for v in report.violations:
                t.add_row(v.path, str(v.line), f"[bad]{v.generator_model}[/bad]", v.reason)
            console.print(t)
        if report.files_scanned == 0:
            console.print(
                "  [dim]No training files present. The firewall is armed and has nothing "
                "to scan, which is the expected state until Track D runs.[/dim]"
            )
        console.print(
            f"  [dim]training-legal generators: {', '.join(allowed_generators(registry))}[/dim]"
        )

    verdict(report.clean, "no forbidden model appears in any training row")
    if not report.clean:
        raise typer.Exit(1)


@app.command("decontam")
def decontam(
    threshold: float = typer.Option(0.6, help="MinHash Jaccard threshold."),
) -> None:
    """No test task may overlap a training task."""
    dataset = REPO_ROOT / "data" / "tasks" / "tasks.jsonl"
    if not dataset.exists():
        rule("Decontamination")
        console.print(
            "  [dim]No dataset at data/tasks/tasks.jsonl. Run `toolsmith tasks build` first.[/dim]"
        )
        verdict(True, "nothing to decontaminate yet")
        return

    from toolsmith.tasks.decontam import check_leakage

    report = check_leakage(dataset, threshold=threshold)
    rule("Decontamination")
    kv("tasks", report.n_tasks)
    kv("method", report.method)
    kv("threshold", threshold)
    kv("near-duplicate pairs across splits", len(report.collisions))
    if report.collisions:
        t = table("Train task", "Test task", "Jaccard", "8-gram overlap")
        for c in report.collisions[:20]:
            t.add_row(c.left, c.right, f"{c.jaccard:.3f}", f"{c.ngram_overlap:.3f}")
        console.print(t)
    verdict(report.clean, "no train/test leakage above threshold")
    if not report.clean:
        raise typer.Exit(1)


@app.command("budget")
def budget() -> None:
    """Cumulative live spend must stay under the configured cap."""
    from toolsmith.ledger import audit_csv

    registry = load_registry()
    audit = audit_csv(policy=registry.budget)

    rule("Budget")
    kv("ledger", audit.path)
    kv("rows", audit.rows)
    for provenance, amount in audit.by_provenance.items():
        kv(f"spend ({provenance})", money(amount), "money")
    kv("cap", money(audit.cap_usd), "money")
    kv("remaining", money(audit.remaining_usd), "money")
    verdict(audit.within_cap, "live spend is within the cap")
    if not audit.within_cap:
        raise typer.Exit(1)


@app.command("hidden-split")
def hidden_split() -> None:
    """The hidden test split must match the hash committed before any run."""
    manifest = REPO_ROOT / "data" / "tasks" / "hidden_split.sha256"
    dataset = REPO_ROOT / "data" / "tasks" / "tasks.jsonl"
    rule("Hidden split integrity")
    if not manifest.exists() or not dataset.exists():
        console.print("  [dim]Hidden split not yet sealed. Run `toolsmith tasks build`.[/dim]")
        verdict(True, "nothing sealed yet")
        return

    from toolsmith.tasks.splits import verify_hidden_split

    ok, expected, actual = verify_hidden_split(dataset, manifest)
    kv("committed hash", expected[:32] + "...")
    kv("current hash", actual[:32] + "...")
    verdict(ok, "hidden split is byte-identical to the sealed commit")
    if not ok:
        raise typer.Exit(1)


@app.command("model-agnostic")
def model_agnostic() -> None:
    """No model identifier may be hardcoded in Python source.

    This is the gate behind the claim that swapping a model is a YAML edit. If
    a model id ever appears in ``src/``, the claim is false and the build fails.
    """
    from toolsmith.governance.agnostic import scan_source

    offenders, files, ids = scan_source(registry=load_registry())

    rule("Model agnosticism")
    kv("model ids checked", ids)
    kv("python files scanned", files)
    if offenders:
        t = table("File", "Line", "Hardcoded id", "Context")
        for hit in offenders:
            t.add_row(hit.path, str(hit.line), f"[bad]{hit.model_id}[/bad]", hit.context)
        console.print(t)
    verdict(not offenders, "no model identifier is hardcoded in src/")
    if offenders:
        raise typer.Exit(1)


@app.command("all")
def run_all() -> None:
    """Run every gate. What the Makefile and CI call.

    Each gate is invoked with explicit arguments rather than relying on Typer's
    defaults, because those defaults are ``OptionInfo`` objects until Typer
    binds them at the command line.
    """
    from toolsmith.tasks.decontam import JACCARD_THRESHOLD

    gates: list[tuple[str, Callable[[], None]]] = [
        ("firewall", lambda: firewall(json_out=False)),
        ("model-agnostic", model_agnostic),
        ("decontam", lambda: decontam(threshold=JACCARD_THRESHOLD)),
        ("hidden-split", hidden_split),
        ("budget", budget),
    ]
    failures: list[str] = []
    for name, fn in gates:
        try:
            fn()
        except typer.Exit as exc:
            if exc.exit_code:
                failures.append(name)
    rule("Gate summary")
    verdict(not failures, f"{5 - len(failures)}/5 gates passed")
    if failures:
        console.print(f"  [bad]failed:[/bad] {', '.join(failures)}")
        raise typer.Exit(1)
