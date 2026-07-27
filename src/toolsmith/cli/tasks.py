"""``toolsmith tasks``: Generate tasks, oracle programs and splits."""

from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True)


@app.command("build")
def build() -> None:
    """Not yet wired. Built in a later phase."""
    raise typer.Exit(0)
