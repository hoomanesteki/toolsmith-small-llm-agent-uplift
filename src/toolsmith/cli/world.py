"""``toolsmith world``: build and inspect the sandboxed domains."""

from __future__ import annotations

import json

import typer

from toolsmith.cli._ui import console, kv, rule, table, verdict
from toolsmith.worlds import Sandbox, all_worlds, build_world, get_world

app = typer.Typer(no_args_is_help=True)


@app.command("build")
def build(
    world: str = typer.Option("", "--world", "-w", help="One world key. Omit with --all."),
    build_all: bool = typer.Option(False, "--all", help="Build every registered world."),
    seed: int | None = typer.Option(None, "--seed", help="Override the world's default seed."),
    verify: bool = typer.Option(
        True, "--verify/--no-verify", help="Build twice and compare digests."
    ),
) -> None:
    """Materialise a world and print its digest.

    ``--verify`` builds each world a second time and asserts the digests match.
    A world that is not byte-reproducible cannot be ground truth, so this is a
    check rather than a nicety.
    """
    if not world and not build_all:
        raise typer.BadParameter("pass --world <key> or --all")
    targets = list(all_worlds().values()) if build_all else [get_world(world)]

    rule("Worlds")
    t = table("World", "Role", "Verbs", "# Rows", "Digest", "Deterministic")
    ok = True
    for spec in targets:
        first = build_world(spec, seed)
        deterministic = True
        if verify:
            deterministic = build_world(spec, seed).digest == first.digest
            ok = ok and deterministic
        t.add_row(
            f"{spec.title} [dim]({spec.key})[/dim]",
            spec.role,
            str(len(spec.tools)),
            f"{first.total_rows:,}",
            f"[dim]{first.digest[:16]}[/dim]",
            "[ok]yes[/ok]" if deterministic else "[bad]NO[/bad]",
        )
    console.print(t)
    verdict(ok, "every world rebuilds to an identical digest")
    if not ok:
        raise typer.Exit(1)


@app.command("show")
def show(world: str = typer.Argument(..., help="World key, for example 'ops'.")) -> None:
    """Describe a world: its entities, its verb bindings and its privileged tools."""
    spec = get_world(world)
    rule(f"{spec.title}  [dim]{spec.tagline}[/dim]")
    kv("key", spec.key, "key")
    kv("role", spec.role, "key")
    kv("default seed", spec.default_seed)
    kv("privileged tools", ", ".join(spec.privileged_tools()) or "none", "warn")

    rule("Entities")
    t = table("Table", "Label", "Key", "What it is")
    for entity in spec.entities:
        t.add_row(entity.table, entity.label, entity.primary_key, entity.description)
    console.print(t)

    rule("Verb bindings")
    v = table("Verb", "Tool", "Mutating", "Privileged")
    for verb in spec.verbs:
        tool = spec.tools[verb]
        v.add_row(
            verb.value,
            tool.name,
            "yes" if tool.mutating else "",
            "[warn]yes[/warn]" if tool.privileged else "",
        )
    console.print(v)
    console.print(f"\n  [dim]{spec.notes}[/dim]")


@app.command("call")
def call(
    world: str = typer.Argument(..., help="World key."),
    tool: str = typer.Argument(..., help="Tool name."),
    args: str = typer.Option("{}", "--args", "-a", help="JSON arguments."),
) -> None:
    """Run one tool call against a fresh sandbox. The fastest way to see a world."""
    spec = get_world(world)
    payload = json.loads(args)
    with Sandbox(spec, build_world(spec)) as sandbox:
        result = sandbox.call(tool, payload)
        rule(f"{spec.key}.{tool}")
        console.print_json(result.to_json())
        if sandbox.calls and sandbox.calls[-1].policy is not None:
            decision = sandbox.calls[-1].policy
            kv("policy", f"{decision.code}: {decision.reason}", "ok" if decision.allowed else "bad")
        kv("state diff", sandbox.state_diff().summary() or "none")


@app.command("list")
def list_worlds() -> None:
    """List every registered world."""
    rule("Registered worlds")
    t = table("Key", "Title", "Role", "Verbs", "Tagline")
    for key, spec in sorted(all_worlds().items()):
        t.add_row(key, spec.title, spec.role, str(len(spec.tools)), spec.tagline)
    console.print(t)
    console.print(
        "\n  [dim]A fourth domain is a folder under src/toolsmith/worlds/ plus one line in "
        "registry.py. Nothing in the runtime, harness or UI changes.[/dim]"
    )
