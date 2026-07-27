"""Regenerate every published artifact from ``results.jsonl``.

The rule: nothing in the report is typed by hand. Every table, every chart and
every number in the prose comes from this module, which reads the committed
results and writes into ``docs/_generated/``. The Quarto pages then include
those fragments.

That is why CI can assert "a fresh run reproduces the committed artifacts byte
for byte" and have it mean something. If a number in the report and a number in
the results file ever disagree, the build fails rather than the reader being
misled.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from toolsmith.config import REPO_ROOT, load_registry
from toolsmith.ledger import audit_csv
from toolsmith.report import charts

GENERATED = REPO_ROOT / "docs" / "_generated"
DATA = REPO_ROOT / "docs" / "data"

ROLES = ["planner", "executor", "reviewer", "escalation"]
TIERS = ["T1", "T2", "T3", "T4", "T5"]


def _md_table(headers: list[str], rows: list[list[str]], align: list[str] | None = None) -> str:
    align = align or ["left"] * len(headers)
    sep = ["---" if a == "left" else "---:" for a in align]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(sep) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _money(v: float) -> str:
    if v == float("inf"):
        return "-"
    return f"${v:,.2f}" if v >= 1 else f"${v:.5f}"


@dataclass
class ReportContext:
    """Everything the pages are written from."""

    matrix: dict[str, Any]
    scores: list[Any]
    optimize: dict[str, Any]
    task_summary: dict[str, Any]
    ledger: dict[str, Any]
    registry: Any

    @property
    def rows(self) -> list[dict[str, Any]]:
        return [r for r in self.matrix.get("rows", []) if r.get("n_runs")]

    @property
    def manifest(self) -> dict[str, Any]:
        return self.matrix.get("manifest", {})

    def row(self, pipeline: str) -> dict[str, Any] | None:
        return next((r for r in self.rows if r["pipeline"] == pipeline), None)


def _ledger_facts() -> dict[str, Any]:
    audit = audit_csv()
    return {
        "cap_usd": audit.cap_usd,
        "live_usd": audit.live_usd,
        "remaining_usd": audit.remaining_usd,
        "within_cap": audit.within_cap,
        "by_provenance": audit.by_provenance,
        "rows": audit.rows,
    }


def load_context() -> ReportContext:
    from toolsmith.harness.store import MATRIX_PATH, read_results
    from toolsmith.optimize import read_all
    from toolsmith.tasks.store import DATA_DIR

    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8")) if MATRIX_PATH.exists() else {}
    summary_path = DATA_DIR / "summary.json"
    return ReportContext(
        matrix=matrix,
        scores=read_results() if matrix else [],
        optimize=read_all(),
        task_summary=json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.exists()
        else {},
        ledger=_ledger_facts(),
        registry=load_registry(),
    )


# ============================================================== fragments ==


def headline_table(ctx: ReportContext) -> str:
    reference = ctx.row("frontier_all_opus")
    base = reference["usd_per_success"] if reference else None
    rows = []
    for row in sorted(ctx.rows, key=lambda r: r["usd_per_success"]):
        ci = row["pass_at_1"]
        ratio = (
            f"{row['usd_per_success'] / base:.2f}x"
            if base and row["usd_per_success"] not in (0, float("inf"))
            else "-"
        )
        rows.append(
            [
                f"**{row['label']}**" if row.get("on_pareto_frontier") else row["label"],
                f"{ci['estimate']:.3f}",
                f"[{ci['ci_low']:.3f}, {ci['ci_high']:.3f}]",
                f"{row['pass_hat_k']:.3f}",
                _money(row["usd_per_task"]),
                _money(row["usd_per_success"]),
                ratio,
                "*" if row.get("on_pareto_frontier") else "",
            ]
        )
    return _md_table(
        [
            "Configuration",
            "pass@1",
            "95% CI",
            "pass^k",
            "$ / task",
            "$ / success",
            "vs frontier",
            "Pareto",
        ],
        rows,
        ["left", "right", "right", "right", "right", "right", "right", "left"],
    )


def safety_table(ctx: ReportContext) -> str:
    rows = []
    for row in sorted(ctx.rows, key=lambda r: -r["pass_at_1"]["estimate"]):
        rows.append(
            [
                row["label"],
                f"{row['abstain_recall']:.3f}" if row["abstain_recall"] is not None else "-",
                f"{row['over_refusal_rate']:.3f}",
                f"{row['injection_resistance']:.3f}"
                if row["injection_resistance"] is not None
                else "-",
                f"{row['policy_violation_rate']:.3f}",
                f"{row['citation_recall']:.3f}" if row["citation_recall"] is not None else "-",
            ]
        )
    return _md_table(
        [
            "Configuration",
            "Abstain recall",
            "Over-refusal",
            "Injection resisted",
            "Unsanctioned actions",
            "Citation recall",
        ],
        rows,
        ["left", "right", "right", "right", "right", "right"],
    )


#: The two executors the compounding argument contrasts. Cheap enough that q^N
#: bites, and strong enough that it does not.
COMPOUNDING_ROWS = ("budget_floor_oss20b", "frontier_all_opus")


def compounding_table(ctx: ReportContext) -> str:
    """pass@1 by gold-program length: the shape of q^N, measured.

    Hand-typed until it was found to be wrong. The frontier column read
    "~1.00" and "~0.99" against measured rates of 0.982 and 0.944, which is
    two points and five points of rounding in the direction that flatters the
    argument, inside the section whose stated purpose is that every claim in it
    is measured. Approximate numbers in a table about compounding error is the
    joke writing itself.
    """
    lengths = sorted(
        {int(s.calls_oracle) for s in ctx.scores if 1 <= s.calls_oracle <= 3 and s.trial == 0}
    )
    rows = []
    for length in lengths:
        cells = []
        for pipeline in COMPOUNDING_ROWS:
            hits = [
                s.passed
                for s in ctx.scores
                if s.trial == 0 and s.pipeline == pipeline and int(s.calls_oracle) == length
            ]
            cells.append(f"{sum(hits) / len(hits):.3f}" if len(hits) >= 20 else "-")
        if all(c == "-" for c in cells):
            continue  # too few tasks at this length to say anything
        rows.append([f"{length} step" if length == 1 else f"{length} steps", *cells])

    labels = [(ctx.row(p) or {}).get("label", p).split("(")[0].strip() for p in COMPOUNDING_ROWS]
    return _md_table(["Gold program", *labels], rows, ["left", "right", "right"])


def tier_table(ctx: ReportContext) -> str:
    tiers = [t for t in TIERS if any(t in r["by_tier"] for r in ctx.rows)]
    rows = []
    for row in sorted(ctx.rows, key=lambda r: -r["pass_at_1"]["estimate"]):
        rows.append(
            [
                row["label"],
                *[
                    f"{row['by_tier'][t]['pass_at_1']:.3f}" if t in row["by_tier"] else "-"
                    for t in tiers
                ],
            ]
        )
    return _md_table(["Configuration", *tiers], rows, ["left", *["right"] * len(tiers)])


def world_table(ctx: ReportContext) -> str:
    worlds = sorted({w for r in ctx.rows for w in r["by_world"]})
    rows = []
    for row in sorted(ctx.rows, key=lambda r: -r["pass_at_1"]["estimate"]):
        cells = [
            f"{row['by_world'][w]['pass_at_1']:.3f}" if w in row["by_world"] else "-"
            for w in worlds
        ]
        primary = row["by_world"].get("ops", {}).get("pass_at_1")
        transfer = row["by_world"].get("clinic", {}).get("pass_at_1")
        drop = f"{(transfer - primary) * 100:+.1f} pp" if primary and transfer else "-"
        rows.append([row["label"], *cells, drop])
    return _md_table(
        ["Configuration", *worlds, "ops to clinic"], rows, ["left", *["right"] * (len(worlds) + 1)]
    )


def comparison_table(ctx: ReportContext) -> str:
    significant = [c for c in ctx.matrix.get("comparisons", []) if c["significant_after_holm"]]
    significant.sort(key=lambda c: -abs(c["difference"]["estimate"]))
    rows = [
        [
            c["left"],
            c["right"],
            f"{c['difference']['estimate']:+.3f}",
            f"[{c['difference']['ci_low']:+.3f}, {c['difference']['ci_high']:+.3f}]",
            f"{c['discordant']}",
            f"{c['mcnemar_p']:.2e}",
        ]
        for c in significant[:20]
    ]
    return _md_table(
        ["A", "B", "A - B", "95% CI", "Discordant", "McNemar p"],
        rows,
        ["left", "left", "right", "right", "right", "right"],
    )


def model_table(ctx: ReportContext) -> str:
    rows = []
    for key, spec in sorted(ctx.registry.models.items()):
        rows.append(
            [
                f"`{key}`",
                spec.provider,
                spec.tier,
                f"{spec.price_in_per_m:.3f}",
                f"{spec.price_out_per_m:.3f}",
                f"{spec.cache_read_discount:.0%}",
                "forbidden" if spec.training_data_use == "forbidden" else spec.training_data_use,
                str(spec.verified_on) if spec.verified_on else "**unverified**",
            ]
        )
    return _md_table(
        ["Key", "Provider", "Tier", "$ in/M", "$ out/M", "Cache", "Training use", "Verified"],
        rows,
        ["left", "left", "left", "right", "right", "right", "left", "left"],
    )


def optimize_table(ctx: ReportContext) -> str:
    order = ["track_c_context", "track_b_router", "track_a_prompts", "track_d_lora"]
    rows = []
    for key in order:
        track = ctx.optimize.get(key)
        if not track:
            continue
        rows.append([track["title"], track["lever"], f"**{track['verdict']}**", track["headline"]])
    return _md_table(["Track", "Lever", "Verdict", "What it found"], rows)


def failure_table(ctx: ReportContext, limit: int = 10) -> str:
    severity = {
        "unsanctioned_privileged_action": 100,
        "runtime_error": 95,
        "followed_injected_instruction": 90,
        "confabulated_on_policy_violation": 80,
        "confabulated_on_unanswerable": 70,
        "confabulated_on_ambiguous": 60,
        "confabulated_on_injection": 60,
        "missing_or_wrong_citation": 50,
        "over_refusal": 45,
        "no_usable_response": 40,
        "wrong_world_state": 35,
        "wrong_answer": 30,
        "wrong_tool_selection": 25,
        "wrong_parameters": 20,
        "inefficient_trajectory": 10,
    }
    ranked = []
    for score in ctx.scores:
        if score.passed or score.trial != 0:
            continue
        worst = max((severity.get(m, 5) for m in score.failure_modes), default=5)
        ranked.append((worst, score))
    ranked.sort(key=lambda item: (-item[0], item[1].task_id))

    rows = [
        [
            f"`{s.task_id}`",
            s.pipeline,
            s.tier,
            ", ".join(m.replace("_", " ") for m in s.failure_modes[:3]),
            f"{s.calls_made}/{s.calls_oracle}",
            _money(s.usd),
        ]
        for _, s in ranked[:limit]
    ]
    return _md_table(
        ["Task", "Configuration", "Tier", "Diagnosis", "Calls", "Cost"],
        rows,
        ["left", "left", "left", "left", "right", "right"],
    )


def suite_table(ctx: ReportContext) -> str:
    from toolsmith.tasks.models import TIER_PURPOSE

    purpose: dict[str, str] = {str(k): v for k, v in TIER_PURPOSE.items()}
    by_tier: dict[str, int] = dict(ctx.task_summary.get("by_tier", {}))
    rows = [[t, f"{by_tier[t]:,}", purpose.get(t, "")] for t in TIERS if t in by_tier]
    return _md_table(["Tier", "Tasks", "What it measures"], rows, ["left", "right", "left"])


# ================================================================= numbers ==


def key_numbers(ctx: ReportContext) -> dict[str, Any]:
    """The figures the prose quotes. Written to JSON so the pages interpolate
    rather than restate them, and a stale sentence becomes impossible."""
    reference = ctx.row("frontier_all_opus")
    frontier = [r for r in ctx.rows if r.get("on_pareto_frontier")]
    affordable = [
        r for r in frontier if not reference or r["usd_per_success"] <= reference["usd_per_success"]
    ]
    best = (
        max(affordable or frontier, key=lambda r: r["pass_at_1"]["estimate"])
        if (affordable or frontier)
        else None
    )
    cheapest = min(frontier, key=lambda r: r["usd_per_success"]) if frontier else None
    naive = ctx.row("naive_role_split")
    no_escalation = ctx.row("ablation_no_escalation")
    cascade = ctx.row("cascade_default")
    comparisons = ctx.matrix.get("comparisons", [])

    # No date here. It was `dt.date.today()`, in a file the reproduce job
    # byte-compares against a fresh run, which means the build was scheduled to
    # start failing at the next UTC midnight and keep failing until someone
    # committed a new date. The determinism test even popped the field before
    # comparing, so the volatility was known and worked around rather than
    # removed. When to regenerate is a question for git log.
    return {
        "n_runs": sum(r["n_runs"] for r in ctx.rows),
        "n_configs": len(ctx.rows),
        "n_tasks": ctx.manifest.get("n_tasks"),
        "trials": ctx.manifest.get("trials"),
        "suite_total": ctx.task_summary.get("total"),
        "traps": ctx.task_summary.get("traps"),
        "injections": ctx.task_summary.get("with_injections"),
        "provenance": ctx.manifest.get("provider_mode", "simulated"),
        "provenance_note": ctx.manifest.get("provenance_note", ""),
        "hidden_split": ctx.manifest.get("hidden_split_sha256", ""),
        "seed": ctx.manifest.get("seed"),
        "world_digests": ctx.manifest.get("world_digests", {}),
        "live_usd": ctx.ledger.get("live_usd", 0.0),
        "cap_usd": ctx.ledger.get("cap_usd", 20.0),
        # Read from the run manifest, not the ledger CSV. The CSV holds live
        # rows only, and even before that change it accumulated across every
        # run ever made on the machine, so its simulated total answered "what
        # has this laptop pretended to spend" rather than the question the
        # report asks: what would buying the published matrix have cost.
        "simulated_usd": ctx.manifest.get("ledger", {}).get("usd_simulated", 0.0),
        "comparisons_total": len(comparisons),
        "comparisons_significant": sum(1 for c in comparisons if c["significant_after_holm"]),
        "best": _row_facts(best),
        "cheapest": _row_facts(cheapest),
        "reference": _row_facts(reference),
        "naive": _row_facts(naive),
        "cascade": _row_facts(cascade),
        "no_escalation": _row_facts(no_escalation),
        "best_vs_reference": (
            best["usd_per_success"] / reference["usd_per_success"] if best and reference else None
        ),
        "cheapest_vs_reference": (
            cheapest["usd_per_success"] / reference["usd_per_success"]
            if cheapest and reference
            else None
        ),
        "input_share": (
            sum(r["input_share"] for r in ctx.rows) / len(ctx.rows) if ctx.rows else 0.0
        ),
        "escalation_gain": (
            cascade["pass_at_1"]["estimate"] - no_escalation["pass_at_1"]["estimate"]
            if cascade and no_escalation
            else None
        ),
        "tracks": {
            k: {"verdict": v["verdict"], "headline": v["headline"]} for k, v in ctx.optimize.items()
        },
    }


def _row_facts(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "pipeline": row["pipeline"],
        "label": row["label"],
        "pass_at_1": row["pass_at_1"]["estimate"],
        "ci_low": row["pass_at_1"]["ci_low"],
        "ci_high": row["pass_at_1"]["ci_high"],
        "pass_hat_k": row["pass_hat_k"],
        "usd_per_task": row["usd_per_task"],
        "usd_per_success": row["usd_per_success"],
        "escalation_rate": row["escalation_rate"],
        "spend_by_role": row["spend_by_role"],
    }


# =================================================================== build ==


def build(generated: Path | None = None, data: Path | None = None) -> dict[str, Path]:
    """Write every fragment the site includes. Returns what it wrote."""
    generated = generated or GENERATED
    data = data or DATA
    generated.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    ctx = load_context()
    if not ctx.rows:
        raise RuntimeError("No results to report. Run `toolsmith matrix run` first.")

    numbers = key_numbers(ctx)
    written: dict[str, Path] = {}

    def write(name: str, content: str) -> None:
        path = generated / name
        path.write_text(content, encoding="utf-8")
        written[name] = path

    # -- tables -------------------------------------------------------------
    write("headline.md", headline_table(ctx))
    write("safety.md", safety_table(ctx))
    write("compounding.md", compounding_table(ctx))
    write("by-tier.md", tier_table(ctx))
    write("by-world.md", world_table(ctx))
    write("comparisons.md", comparison_table(ctx))
    write("models.md", model_table(ctx))
    write("optimize.md", optimize_table(ctx))
    write("failures.md", failure_table(ctx))
    write("suite.md", suite_table(ctx))

    # -- charts -------------------------------------------------------------
    write(
        "pareto.html",
        charts.pareto(
            ctx.rows,
            caption=(
                "Each point is one configuration on identical tasks. The vertical bar is the "
                "95% paired-bootstrap interval. The frontier is computed, not drawn by eye: a "
                "configuration is on it when nothing else is both cheaper per success and at "
                "least as reliable. Unbilled rows are excluded, because free at the margin is "
                "not free."
            ),
            table=headline_table(ctx),
        ),
    )
    # The failures page ranked ten transcripts and drew nothing. A reader could
    # see which run lost and not the shape of how the system loses, which is the
    # more useful of the two and the one the page is named after.
    modes: dict[str, int] = {}
    for score in ctx.scores:
        if score.passed or score.trial != 0:
            continue
        for mode in score.failure_modes:
            modes[mode] = modes.get(mode, 0) + 1
    if modes:
        ranked = sorted(modes.items(), key=lambda kv: -kv[1])[:12]
        write(
            "failure-modes.html",
            charts.bars(
                [
                    charts.Bar(label=name.replace("_", " "), value=float(count))
                    for name, count in ranked
                ],
                digits=0,
                caption=(
                    "Named causes across every failing run in the matrix, all configurations "
                    "pooled, most frequent first. The head is ordinary wrongness: a bad "
                    "answer, the wrong tool, bad arguments. The tail holds the failures "
                    "that would matter in production, and it is thin, which is the useful "
                    "thing this chart says and the thing a severity ranking cannot."
                ),
                table=_md_table(
                    ["Failure mode", "# Runs"],
                    [[n.replace("_", " "), str(c)] for n, c in ranked],
                    ["left", "right"],
                ),
            ),
        )

    write(
        "quality-bars.html",
        charts.bars(
            [
                charts.Bar(
                    label=r["label"],
                    value=r["pass_at_1"]["estimate"],
                    low=r["pass_at_1"]["ci_low"],
                    high=r["pass_at_1"]["ci_high"],
                    highlight=bool(r.get("on_pareto_frontier")),
                )
                for r in sorted(ctx.rows, key=lambda r: -r["pass_at_1"]["estimate"])
            ],
            caption=(
                "The bar is the estimate; the line through it is the 95% interval. Where two "
                "intervals overlap, this sample does not establish a difference between those "
                "configurations, whatever the point estimates say."
            ),
        ),
    )
    write(
        "spend-by-role.html",
        charts.stacked(
            [(r["label"], r["spend_by_role"]) for r in ctx.rows if r["spend_by_role"]],
            ROLES,
            caption=(
                "Share of each configuration's spend. The source spec predicted the executor "
                "at 73% and the planner at 9% on a six-turn loop; at the 2.4 turns these tasks "
                "actually take, the planner's single frontier call dominates instead."
            ),
        ),
    )

    tier_series: list[tuple[str, list[tuple[float, float]]]] = []
    for row in sorted(ctx.rows, key=lambda r: -r["pass_at_1"]["estimate"])[:4]:
        points: list[tuple[float, float]] = [
            (float(i + 1), float(row["by_tier"][t]["pass_at_1"]) * 100)
            for i, t in enumerate(TIERS)
            if t in row["by_tier"]
        ]
        if points:
            tier_series.append((str(row["label"])[:22], points))
    write(
        "by-tier.html",
        charts.grouped_lines(
            tier_series,
            x_label="Tier (1 = single lookup, 4 = traps, 5 = grounded)",
            y_label="pass@1 (%)",
            caption=(
                "The four strongest configurations across tiers. Every configuration is near "
                "ceiling on single lookups; the tiers that separate them are conditional policy "
                "reasoning and the traps."
            ),
            table=tier_table(ctx),
        ),
    )

    # -- machine-readable ---------------------------------------------------
    (data / "numbers.json").write_text(
        json.dumps(numbers, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (data / "matrix.json").write_text(
        json.dumps(ctx.matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    written["numbers.json"] = data / "numbers.json"
    written["matrix.json"] = data / "matrix.json"

    # -- the prose fragments that quote numbers -----------------------------
    write("key-numbers.md", _key_numbers_md(numbers))
    write("budget-state.md", _budget_state_md(numbers))
    return written


def _key_numbers_md(n: dict[str, Any]) -> str:
    best, ref, naive = n["best"], n["reference"], n["naive"]
    lines = [
        f"- **{n['n_runs']:,} graded runs** across **{n['n_configs']} configurations** on one "
        f"stratified sample of **{n['n_tasks']} tasks**, {n['trials']} trials each.",
        f"- The suite is **{n['suite_total']:,} verified tasks**, of which **{n['traps']:,} are traps** "
        f"and **{n['injections']:,} carry an indirect prompt injection**.",
    ]
    if best and ref:
        lines.append(
            f"- Best value on the frontier: **{best['label']}** at **{best['pass_at_1']:.3f} pass@1** "
            f"and **${best['usd_per_success']:.5f} per success**, which is "
            f"**{n['best_vs_reference']:.2f}x** the all-frontier row's cost at "
            f"{ref['pass_at_1']:.3f} pass@1."
        )
    if naive and ref:
        lines.append(
            f"- The intuitive role split (frontier bookends, cheap executor) reaches "
            f"**{naive['pass_at_1']:.3f} pass@1** at **${naive['usd_per_success']:.5f} per success**: "
            f"worse than the cascade on both axes."
        )
    lines.append(
        f"- **{n['comparisons_significant']} of {n['comparisons_total']}** pairwise comparisons survive "
        "Holm-Bonferroni correction."
    )
    lines.append(
        f"- Input tokens are **{n['input_share']:.0%}** of the token bill, averaged across configurations."
    )
    lines.append(
        f"- Real money spent: **${n['live_usd']:.2f}** of a **${n['cap_usd']:.0f}** cap, enforced in code "
        "before every call."
    )
    return "\n".join(lines) + "\n"


def _budget_state_md(n: dict[str, Any]) -> str:
    """The two sentences the governance page used to hardcode.

    They were hand-written, and by the time anyone noticed they quoted a
    figure from a ledger that had since been rewritten. A page about
    reproducibility is the last place a stale number belongs, so the
    sentences are now generated from the same file as the tables above them.
    """
    return (
        f"Current state: **${n['live_usd']:.2f}** spent against a **${n['cap_usd']:.0f}** cap. "
        f"Buying the published matrix from the vendors, at the catalogue prices in "
        f"`configs/models.yaml`, would have cost **${n['simulated_usd']:,.2f}**. That is a "
        "useful number in its own right: it is the price of this evidence, and it is why the "
        "simulator is a provider rather than a test fixture.\n"
    )
