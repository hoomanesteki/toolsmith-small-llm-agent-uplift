"""Shared console styling for the CLI.

One place for colour and table shape, so that every command looks like it came
from the same program. The palette matches the web UI's tokens, which is a
small thing that makes a project feel built rather than assembled.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table
from rich.theme import Theme

THEME = Theme(
    {
        "ok": "bold green",
        "warn": "bold yellow",
        "bad": "bold red",
        "dim": "grey58",
        "key": "bold cyan",
        "money": "bold magenta",
        "head": "bold white",
    }
)

console = Console(theme=THEME)


def rule(title: str) -> None:
    console.rule(f"[head]{title}[/head]", style="grey35")


def kv(label: str, value: Any, style: str = "") -> None:
    body = f"[{style}]{value}[/{style}]" if style else str(value)
    console.print(f"  [dim]{label:<26}[/dim] {body}")


def table(*columns: str, title: str | None = None) -> Table:
    t = Table(title=title, header_style="head", border_style="grey35", title_style="head")
    for column in columns:
        justify = "right" if column.startswith("#") or column.startswith("$") else "left"
        t.add_column(column.lstrip("#$ "), justify=justify)  # type: ignore[arg-type]
    return t


def verdict(passed: bool, message: str) -> None:
    mark = "PASS" if passed else "FAIL"
    style = "ok" if passed else "bad"
    console.print(f"[{style}]{mark}[/{style}]  {message}")


def skipped(message: str) -> None:
    """A gate that had nothing to check.

    Kept visually distinct from PASS on purpose. Two gates used to print PASS
    when the dataset was absent, which meant the `gates` job in CI went green
    on every push without ever checking decontamination or the hidden-split
    seal: the checkout has no dataset, so both took the empty branch. A gate
    that cannot run has not passed, and the word on the screen should say so.
    """
    console.print(f"[warn]SKIP[/warn]  {message}")


def money(amount: float) -> str:
    return f"${amount:,.4f}" if amount < 1 else f"${amount:,.2f}"
