"""``toolsmith matrix``: Run the evaluation matrix."""

from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True)


@app.command("run")
def run() -> None:
    """Not yet wired. Built in a later phase."""
    raise typer.Exit(0)
