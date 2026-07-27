"""``toolsmith doctor``: what can this machine actually run, right now.

The first command anyone should type after cloning. It answers three questions
in one screen: is the config valid, which providers do I hold keys for, and how
much of the budget is left.
"""

from __future__ import annotations

import shutil

import typer

from toolsmith import __version__
from toolsmith.cli._ui import console, kv, money, rule, table, verdict
from toolsmith.config import load_registry
from toolsmith.ledger import audit_csv
from toolsmith.providers import describe_availability


def doctor(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="List every model."),
) -> None:
    """Report configuration health, provider availability and remaining budget."""
    registry = load_registry()

    rule(f"ToolSmith {__version__}")
    kv("models declared", len(registry.models), "key")
    kv("pipelines declared", len(registry.pipelines), "key")
    kv("rubrics declared", len(registry.rubrics), "key")

    # -- providers ----------------------------------------------------------
    rule("Providers")
    availability = describe_availability(registry)
    t = table("Provider", "Key", "# Models", "Limits")
    for status in availability.providers:
        limits = status.limits or {}
        summary = (
            ", ".join(f"{k}={v}" for k, v in limits.items() if k in {"rpm", "tpd"} and v)
            or "not probed"
        )
        t.add_row(
            status.provider,
            "[ok]yes[/ok]" if status.key_present else "[dim]no[/dim]",
            str(len(status.models)),
            f"[dim]{summary}[/dim]",
        )
    console.print(t)

    live = availability.live_capable
    if live:
        console.print(f"  [ok]live-capable:[/ok] {', '.join(live)}")
    else:
        console.print(
            "  [warn]no provider keys found.[/warn] Everything still runs: "
            "[key]--provider simulated[/key] reproduces every published table at $0."
        )

    # -- unverified rows ----------------------------------------------------
    unverified = [k for k, s in registry.models.items() if s.verified_on is None]
    if unverified:
        rule("Unverified")
        console.print(
            f"  [warn]{len(unverified)} model(s) have verified_on: null[/warn] "
            f"({', '.join(sorted(unverified))})"
        )
        console.print("  [dim]Their prices are estimates. Run `toolsmith probe models`.[/dim]")

    # -- reviewer independence ---------------------------------------------
    offenders = registry.assert_reviewer_independence()
    rule("Reviewer independence")
    if offenders:
        console.print(
            f"  [warn]{len(offenders)} pipeline(s) let the executor's family review itself:[/warn] "
            f"{', '.join(offenders)}"
        )
        console.print(
            "  [dim]Kept deliberately and tagged self-review: the penalty is a measurement, "
            "not an accident.[/dim]"
        )
    else:
        console.print("  [ok]every pipeline uses a family-disjoint reviewer.[/ok]")

    # -- budget -------------------------------------------------------------
    rule("Budget")
    audit = audit_csv(policy=registry.budget)
    kv("cap", money(audit.cap_usd), "money")
    kv("live spend to date", money(audit.live_usd), "money")
    kv("remaining", money(audit.remaining_usd), "money")
    kv("ledger rows", audit.rows)
    verdict(audit.within_cap, "cumulative live spend is within the configured cap")

    # -- toolchain ----------------------------------------------------------
    rule("Toolchain")
    for binary, why in (("quarto", "renders the report site"), ("git", "version pins and tags")):
        found = shutil.which(binary)
        kv(binary, f"[ok]{found}[/ok]" if found else f"[warn]missing[/warn]  ({why})")

    if verbose:
        rule("Models")
        m = table("Key", "Provider", "Tier", "$ in/M", "$ out/M", "Cache", "Training use")
        for key, spec in sorted(registry.models.items()):
            m.add_row(
                key,
                spec.provider,
                spec.tier,
                f"{spec.price_in_per_m:.3f}",
                f"{spec.price_out_per_m:.3f}",
                f"{spec.cache_read_discount:.0%}",
                "[bad]forbidden[/bad]"
                if spec.training_data_use == "forbidden"
                else f"[ok]{spec.training_data_use}[/ok]",
            )
        console.print(m)
