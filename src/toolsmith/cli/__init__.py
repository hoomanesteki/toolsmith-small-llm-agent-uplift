"""The ``toolsmith`` command line.

One entry point for the whole project, because a reviewer should be able to
reproduce any published number with a command they can read off the README
rather than a notebook they have to run in order.

    toolsmith doctor            what can this machine actually run right now
    toolsmith probe models      check configs/models.yaml against live catalogues
    toolsmith world build       build the sandboxed domains, print digests
    toolsmith tasks build       generate tasks, oracle programs, splits
    toolsmith matrix run        run the evaluation matrix
    toolsmith optimize <track>  run one improvement track
    toolsmith report build      regenerate every published artifact
    toolsmith demo              seed a clickable demo with zero keys
    toolsmith ci <gate>         the checks that fail the build
"""

from __future__ import annotations

import typer

from toolsmith import __version__

app = typer.Typer(
    name="toolsmith",
    help="Which model should run which part of your agent? This proves the answer.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"toolsmith {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """ToolSmith control plane."""


def _register() -> None:
    """Attach sub-apps. Kept in a function so import errors name the culprit."""
    from toolsmith.cli import ci, demo, doctor, matrix, optimize, probe, report, tasks, world

    app.command("doctor")(doctor.doctor)
    app.add_typer(probe.app, name="probe", help="Check configs against live sources.")
    app.add_typer(world.app, name="world", help="Build and inspect the sandboxed domains.")
    app.add_typer(tasks.app, name="tasks", help="Generate tasks, oracles, splits.")
    app.add_typer(matrix.app, name="matrix", help="Run the evaluation matrix.")
    app.add_typer(optimize.app, name="optimize", help="Run one improvement track.")
    app.add_typer(report.app, name="report", help="Regenerate published artifacts.")
    app.add_typer(ci.app, name="ci", help="The gates that fail the build.")
    app.command("demo")(demo.demo)


_register()

__all__ = ["app"]
