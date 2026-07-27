"""``toolsmith optimize``: run one improvement track, or all of them."""

from __future__ import annotations

import typer

from toolsmith.cli._ui import console, kv, rule, table, verdict

app = typer.Typer(no_args_is_help=True)

ORDER = ("c", "b", "a", "d")
VERDICT_STYLE = {
    "gain": "ok",
    "null": "dim",
    "regression": "bad",
    "unmeasurable": "warn",
}


@app.command("run")
def run(
    track: str = typer.Argument("all", help="c, b, a, d, or all."),
    n: int = typer.Option(120, "--n", help="Tasks per configuration."),
    provider: str = typer.Option("simulated", "--provider"),
) -> None:
    """Run an improvement track and write eval/optimize/<track>.json.

    Order is C, B, A, D on purpose: free first, highest-evidence second,
    token-cost third, GPU last.
    """
    from toolsmith.optimize import TRACKS

    wanted = ORDER if track == "all" else (track.lower(),)
    unknown = [t for t in wanted if t not in TRACKS]
    if unknown:
        raise typer.BadParameter(f"unknown track(s): {', '.join(unknown)}")

    results = []
    for key in wanted:
        module = TRACKS[key]
        rule(f"Track {key.upper()}")
        with console.status("[dim]running...[/dim]"):
            if key == "d":
                result = module.run(provider_mode=provider)
            elif key == "a":
                result = module.run(provider_mode=provider, n=n)
            else:
                result = module.run(n=n, provider_mode=provider)
        results.append(result)

        style = VERDICT_STYLE.get(result.verdict, "dim")
        kv("lever", result.lever)
        kv("verdict", f"[{style}]{result.verdict}[/{style}]")
        kv("tuned on", result.tuned_on)
        kv("reported on", result.reported_on)
        console.print(f"\n  [head]{result.headline}[/head]\n")
        if result.delta:
            t = table("Metric", "Change")
            for name, value in result.delta.items():
                t.add_row(name, f"{value:+.4g}" if isinstance(value, int | float) else str(value))
            console.print(t)
        kv("written", str(result.write()), "key")

    rule("Summary")
    t = table("Track", "Lever", "Verdict", "Headline")
    for result in results:
        style = VERDICT_STYLE.get(result.verdict, "dim")
        t.add_row(
            result.track.replace("track_", "").split("_")[0].upper(),
            result.lever[:38],
            f"[{style}]{result.verdict}[/{style}]",
            result.headline[:70] + ("..." if len(result.headline) > 70 else ""),
        )
    console.print(t)
    console.print(
        "\n  [dim]A track that shows no gain is a published result. Two of these are "
        "nulls, and both say why.[/dim]"
    )
    verdict(True, f"{len(results)} track(s) written to eval/optimize/")


@app.command("show")
def show(track: str = typer.Argument(..., help="c, b, a or d.")) -> None:
    """Print a track's full result, including its candidates and caveats."""
    from toolsmith.optimize import TRACKS, read_track

    if track.lower() not in TRACKS:
        raise typer.BadParameter(f"unknown track {track!r}")
    name = TRACKS[track.lower()].__name__.rsplit(".", 1)[-1]
    payload = read_track(name)
    if payload is None:
        console.print(
            f"  [warn]{name} has not been run. Try `toolsmith optimize run {track}`.[/warn]"
        )
        raise typer.Exit(1)

    rule(payload["title"])
    kv("lever", payload["lever"])
    kv("verdict", payload["verdict"], VERDICT_STYLE.get(payload["verdict"], "dim"))
    kv("tuned on", payload["tuned_on"])
    kv("reported on", payload["reported_on"])
    kv("provenance", payload["provenance"])
    console.print(f"\n  [head]{payload['headline']}[/head]")

    if payload["candidates"]:
        rule("Candidates")
        keys = [k for k in payload["candidates"][0] if k not in {"rationale", "accuracy_note"}]
        t = table(*keys)
        for row in payload["candidates"]:
            t.add_row(*[str(row.get(k, "")) for k in keys])
        console.print(t)

    if payload["notes"]:
        rule("Caveats")
        for note in payload["notes"]:
            console.print(f"  [dim]- {note}[/dim]")
