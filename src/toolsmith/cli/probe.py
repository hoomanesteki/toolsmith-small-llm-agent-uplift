"""``toolsmith probe``: check the config against live sources."""

from __future__ import annotations

import typer

from toolsmith.cli._ui import console, kv, rule, table, verdict
from toolsmith.config import load_registry

app = typer.Typer(no_args_is_help=True)


@app.command("models")
def models(
    write: bool = typer.Option(False, "--write", help="Write configs/generated/models_probe.json."),
    strict: bool = typer.Option(
        False, "--strict", help="Exit non-zero on any drift, not just errors."
    ),
) -> None:
    """Diff configs/models.yaml against each provider's live catalogue."""
    from toolsmith.probes import probe, write_report

    registry = load_registry()
    report = probe(registry)

    rule("Model catalogue probe")
    kv("checked", ", ".join(report.checked) or "nothing (no keys)")
    for provider, why in sorted(report.skipped.items()):
        kv(f"skipped {provider}", f"[dim]{why}[/dim]")

    if report.drift:
        t = table("Severity", "Model", "Finding")
        for d in report.drift:
            style = {"error": "bad", "warn": "warn"}.get(d.severity, "dim")
            t.add_row(f"[{style}]{d.severity}[/{style}]", d.model_key, d.message)
        console.print(t)
    else:
        console.print("  [ok]no drift detected[/ok]")

    if write:
        path = write_report(report)
        kv("written", str(path), "key")

    failed = bool(report.errors) or (strict and bool(report.drift))
    verdict(not failed, "model registry matches live catalogues")
    if failed:
        raise typer.Exit(1)


@app.command("limits")
def limits(
    write: bool = typer.Option(False, "--write", help="Merge results into configs/limits.yaml."),
) -> None:
    """Read x-ratelimit headers from a one-token request per provider."""
    from toolsmith.probes import probe_all, write_limits

    registry = load_registry()
    outcomes = probe_all(registry)

    rule("Rate limit probe")
    t = table("Provider", "RPM", "TPM", "RPD", "TPD", "Note")
    for outcome in outcomes:
        limit = outcome.limit
        t.add_row(
            outcome.provider,
            str(limit.rpm if limit else "-"),
            str(limit.tpm if limit else "-"),
            str(limit.rpd if limit else "-"),
            str(limit.tpd if limit else "-"),
            f"[ok]{outcome.note}[/ok]" if outcome.note == "ok" else f"[dim]{outcome.note}[/dim]",
        )
    console.print(t)

    if write:
        probed = [o for o in outcomes if o.note == "ok"]
        if probed:
            kv("written", str(write_limits(outcomes, registry=registry)), "key")
        else:
            console.print("  [warn]nothing probed; configs/limits.yaml left untouched[/warn]")
