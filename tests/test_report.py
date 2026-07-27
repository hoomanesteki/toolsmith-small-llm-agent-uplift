"""The report.

The property under test: nothing published is typed by hand. Every table and
every number in the prose comes from the results file, so a stale sentence is
impossible rather than merely unlikely.
"""

from __future__ import annotations

import json
import re

import pytest

from toolsmith.config import REPO_ROOT
from toolsmith.report import build, key_numbers, load_context

DOCS = REPO_ROOT / "docs"
GENERATED = DOCS / "_generated"


@pytest.fixture(scope="module")
def ctx():
    context = load_context()
    if not context.rows:
        pytest.skip("no results committed")
    return context


@pytest.fixture(scope="module")
def numbers(ctx):
    return key_numbers(ctx)


def test_the_report_regenerates_from_results(tmp_path, ctx):
    written = build(generated=tmp_path / "gen", data=tmp_path / "data")
    assert "headline.md" in written
    assert "pareto.html" in written
    assert (tmp_path / "data" / "numbers.json").exists()


def test_regeneration_is_deterministic(tmp_path, ctx):
    """A published artifact that changes when nothing changed cannot be checked."""
    first = build(generated=tmp_path / "a", data=tmp_path / "ad")
    second = build(generated=tmp_path / "b", data=tmp_path / "bd")
    for name in first:
        if name.endswith(".json"):
            # Nothing is popped here any more. `generated_on` used to be, which
            # meant the test knew the artifact was volatile and agreed not to
            # look. The field is gone; if another one appears this fails, which
            # is the entire job.
            assert json.loads(first[name].read_text()) == json.loads(second[name].read_text()), name
        else:
            assert first[name].read_text() == second[name].read_text(), name


def test_every_fragment_the_site_includes_exists():
    """A missing include renders as a silent blank in Quarto."""
    referenced = set()
    for page in [*DOCS.glob("*.qmd"), *DOCS.glob("cards/*.qmd")]:
        for match in re.finditer(
            r"\{\{<\s*include\s+([^\s>]+)\s*>\}\}", page.read_text(encoding="utf-8")
        ):
            referenced.add(match.group(1).replace("../", ""))
    assert referenced, "the pages include nothing, which cannot be right"
    for target in sorted(referenced):
        assert (DOCS / target).exists(), f"{target} is included but not generated"


def test_no_page_hardcodes_a_dollar_figure_of_its_own(ctx, numbers):
    """Every priced claim in the prose must be one the results file still makes.

    This caught a real one. The governance page said the published matrix would
    have cost $852.16 to buy, hand-typed from a ledger that was later rewritten
    to hold live rows only. The figure survived a rename, a schema change and a
    full reproducibility pass, because nothing was watching it. A page arguing
    that no measured value may be typed by hand is the worst possible place for
    a measured value typed by hand.
    """
    allowed = {f"${numbers['live_usd']:.2f}", f"${numbers['simulated_usd']:,.2f}"}
    allowed |= {
        f"${row['usd_per_success']:.5f}"
        for row in ctx.rows
        if row.get("usd_per_success") is not None
    }
    pages = [*DOCS.glob("*.qmd"), *DOCS.glob("cards/*.qmd"), REPO_ROOT / "README.md"]
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for found in re.findall(r"\$[0-9][0-9,]*\.[0-9]+", text):
            assert found in allowed, (
                f"{page.name} quotes {found}, which no longer appears in the results. "
                "Move it into a generated fragment rather than correcting it by hand."
            )


def test_the_headline_table_ranks_by_cost_per_success(ctx):
    from toolsmith.report.build import headline_table

    lines = [line for line in headline_table(ctx).splitlines() if line.startswith("|")][2:]
    costs = []
    for line in lines:
        cell = line.split("|")[6].strip()
        if cell not in ("-", ""):
            costs.append(float(cell.replace("$", "").replace(",", "")))
    assert costs == sorted(costs)


def test_key_numbers_match_the_matrix(ctx, numbers):
    reference = ctx.row("frontier_all_opus")
    assert numbers["reference"]["pass_at_1"] == pytest.approx(reference["pass_at_1"]["estimate"])
    assert numbers["n_configs"] == len(ctx.rows)
    assert numbers["comparisons_total"] == len(ctx.matrix["comparisons"])


def test_the_report_never_claims_money_was_spent(numbers):
    assert numbers["live_usd"] == 0.0
    assert numbers["cap_usd"] >= numbers["live_usd"]


def test_provenance_is_carried_into_every_published_number(numbers):
    assert numbers["provenance"]
    assert "PROVENANCE" in numbers["provenance_note"]
    assert numbers["hidden_split"], "the seal must be reported alongside the results"


def test_charts_emit_palette_variables_not_literals():
    """One SVG serves both modes because colours resolve at view time."""
    for name in ("pareto.html", "quality-bars.html", "spend-by-role.html"):
        svg = (GENERATED / name).read_text(encoding="utf-8")
        assert "var(--series-1)" in svg or "var(--chart-grid)" in svg, name
        assert not re.search(r'fill="#[0-9a-fA-F]{6}"', svg), f"{name} hardcodes a colour"


def test_every_chart_ships_a_table_twin_or_a_caption():
    """A tooltip must enhance a value, never be the only way to read it."""
    for name in ("pareto.html", "quality-bars.html", "spend-by-role.html", "by-tier.html"):
        html = (GENERATED / name).read_text(encoding="utf-8")
        assert "<figcaption>" in html, name


def test_the_readme_headline_matches_the_results(numbers):
    """The most quotable numbers in the repository must not go stale."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    best = numbers["best"]
    assert f"{best['pass_at_1']:.3f}" in readme
    assert f"{numbers['best_vs_reference']:.2f}x" in readme
    assert f"{numbers['comparisons_significant']} of {numbers['comparisons_total']}" in readme


# The prose quotes a handful of figures for readability. Each one is listed here
# with the fact it must match, so a rebuild that moves a number cannot leave a
# sentence behind. Anything not on this list should not be a literal in prose.
PROSE_CLAIMS = {
    "index.qmd": [
        ("best pass@1", lambda n: f"{n['best']['pass_at_1']:.3f}"),
        ("reference pass@1", lambda n: f"{n['reference']['pass_at_1']:.3f}"),
        ("naive pass@1", lambda n: f"{n['naive']['pass_at_1']:.3f}"),
        ("best vs reference", lambda n: f"{n['best_vs_reference']:.2f}x"),
        ("input share", lambda n: f"{n['input_share'] * 100:.0f}%"),
    ],
    # An empty list here used to make this page's parametrised case pass while
    # asserting nothing, which is the shape of test that survives the change it
    # was written to catch. The comparison count is the number most likely to
    # move on this page: it changes whenever a configuration is added, removed
    # or found to be a duplicate of another.
    "results.qmd": [
        ("comparison count", lambda n: str(n["comparisons_total"])),
    ],
    "findings.qmd": [
        ("input share", lambda n: f"{n['input_share'] * 100:.0f}%"),
        ("escalation gain", lambda n: f"{n['escalation_gain'] * 100:.1f}"),
    ],
}


@pytest.mark.parametrize("page", sorted(PROSE_CLAIMS))
def test_the_prose_has_not_gone_stale(page, numbers):
    """A sentence that quotes a number must still agree with the results file.

    The tables are generated, so they cannot drift. The prose around them can,
    and a report whose headline paragraph disagrees with its own table is worse
    than one with no paragraph.
    """
    text = (DOCS / page).read_text(encoding="utf-8")
    for label, expected in PROSE_CLAIMS[page]:
        value = expected(numbers)
        assert value in text, f"{page} no longer contains the {label} ({value})"


def test_the_site_declares_its_pages():
    config = (DOCS / "_quarto.yml").read_text(encoding="utf-8")
    for page in (
        "index.qmd",
        "findings.qmd",
        "results.qmd",
        "method.qmd",
        "failures.qmd",
        "governance.qmd",
    ):
        assert page in config, page
