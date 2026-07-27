"""``toolsmith report``: regenerate every published artifact."""

from __future__ import annotations

import json
import shutil
import subprocess

import typer

from toolsmith.cli._ui import console, kv, rule, table, verdict
from toolsmith.config import REPO_ROOT

app = typer.Typer(no_args_is_help=True)


@app.command("build")
def build_report() -> None:
    """Write docs/_generated/ and docs/data/ from the committed results."""
    from toolsmith.report import build

    rule("Report")
    try:
        written = build()
    except RuntimeError as error:
        console.print(f"  [bad]{error}[/bad]")
        raise typer.Exit(1) from error

    t = table("Artifact", "Path")
    for name, path in sorted(written.items()):
        t.add_row(name, str(path.relative_to(REPO_ROOT)))
    console.print(t)

    numbers = json.loads((REPO_ROOT / "docs" / "data" / "numbers.json").read_text(encoding="utf-8"))
    rule("What it says")
    kv("graded runs", f"{numbers['n_runs']:,}")
    kv("configurations", numbers["n_configs"])
    if numbers.get("best"):
        kv("best value", f"{numbers['best']['label']} at {numbers['best']['pass_at_1']:.3f} pass@1")
    if numbers.get("best_vs_reference"):
        kv("versus all-frontier", f"{numbers['best_vs_reference']:.2f}x cost per success", "key")
    kv(
        "significant comparisons",
        f"{numbers['comparisons_significant']}/{numbers['comparisons_total']}",
    )
    kv("real money spent", f"${numbers['live_usd']:.2f} of ${numbers['cap_usd']:.0f}", "money")
    verdict(True, "every published table and chart regenerated from results.jsonl")


@app.command("site")
def site(
    serve: bool = typer.Option(False, "--serve", help="Preview instead of rendering."),
) -> None:
    """Render the Quarto site into docs/_site (after regenerating the data)."""
    build_report()
    if shutil.which("quarto") is None:
        console.print(
            "  [warn]quarto is not installed.[/warn] The fragments are written; "
            "install Quarto to render the site, or read docs/_generated/ directly."
        )
        raise typer.Exit(1)

    rule("Quarto")
    command = ["quarto", "preview" if serve else "render", "docs"]
    kv("running", " ".join(command))
    result = subprocess.run(command, cwd=REPO_ROOT, check=False)
    verdict(result.returncode == 0, "site rendered into docs/_site")
    if result.returncode:
        raise typer.Exit(result.returncode)


@app.command("numbers")
def numbers() -> None:
    """Print the figures the report quotes, so a claim can be checked at a glance."""
    from toolsmith.report import key_numbers, load_context

    payload = key_numbers(load_context())
    console.print_json(json.dumps(payload, default=str))
