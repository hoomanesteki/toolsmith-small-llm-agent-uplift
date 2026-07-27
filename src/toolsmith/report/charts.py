"""Static SVG for the published site.

The site is rendered by Quarto and served as static files, so its charts cannot
depend on a running server. They are generated here from the same committed
results the UI reads, with the same validated palette and the same rules.

The colours are emitted as CSS custom properties rather than literals, so one
SVG serves both light and dark without a second render and without an automatic
flip: each mode has its own selected steps, declared once in the site stylesheet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from typing import Any

#: Roles, resolved from the stylesheet at view time.
INK = "var(--chart-ink-2)"
MUTED = "var(--chart-muted)"
GRID = "var(--chart-grid)"
AXIS = "var(--chart-axis)"
SURFACE = "var(--chart-surface)"
SERIES = ("var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)")


def _svg(width: int, height: int, body: str, label: str) -> str:
    return (
        f'<figure class="chart">\n'
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}" '
        f'style="width:100%;height:auto;overflow:visible">\n{body}\n</svg>\n'
    )


def _close(caption: str, table: str = "") -> str:
    parts = []
    if table:
        parts.append(
            f'<details class="chart-table"><summary>Show the numbers</summary>\n\n{table}\n\n</details>'
        )
    if caption:
        parts.append(f"<figcaption>{caption}</figcaption>")
    return "\n".join(parts) + "\n</figure>\n"


def _text(
    x: float,
    y: float,
    content: str,
    *,
    anchor: str = "start",
    fill: str = INK,
    size: int = 11,
    weight: int = 400,
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{fill}" '
        f'font-size="{size}" font-weight="{weight}" font-family="system-ui,-apple-system,sans-serif">'
        f"{escape(content)}</text>"
    )


def _line(
    x1: float, y1: float, x2: float, y2: float, stroke: str, width: float = 1, opacity: float = 1
) -> str:
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" '
        f'stroke-width="{width}" stroke-linecap="round" opacity="{opacity}"/>'
    )


def _legend(items: list[tuple[str, str]]) -> str:
    spans = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:.35rem;color:var(--ink-secondary)">'
        f'<i style="width:10px;height:10px;border-radius:3px;background:{colour};display:inline-block"></i>'
        f"{escape(label)}</span>"
        for label, colour in items
    )
    return f'<div class="chart-legend" style="display:flex;gap:.85rem;flex-wrap:wrap;margin-bottom:.6rem;font-size:.8125rem">{spans}</div>'


# ================================================================== pareto ==


def pareto(rows: list[dict[str, Any]], caption: str = "", table: str = "") -> str:
    """Cost per success against pass@1, with the computed frontier.

    One colour for the points and one for the frontier. Fifteen categorical hues
    would be fifteen hues in service of one story, which is the most common way
    a chart misses its point. The story is the frontier.
    """
    points = [
        r
        for r in rows
        if r.get("pass_at_1")
        and r.get("usd_per_success", 0) > 0
        and math.isfinite(r["usd_per_success"])
    ]
    if not points:
        return ""

    width, height = 780, 420
    pad = {"top": 20, "right": 30, "bottom": 56, "left": 66}
    xs = [math.log10(p["usd_per_success"]) for p in points]
    ys = [p["pass_at_1"]["estimate"] for p in points]
    x0, x1 = min(xs) - 0.12, max(xs) + 0.12
    y0, y1 = max(0.0, min(ys) - 0.08), min(1.03, max(ys) + 0.06)

    def sx(v: float) -> float:
        return pad["left"] + ((math.log10(v) - x0) / (x1 - x0)) * (
            width - pad["left"] - pad["right"]
        )

    def sy(v: float) -> float:
        return (
            height - pad["bottom"] - ((v - y0) / (y1 - y0)) * (height - pad["top"] - pad["bottom"])
        )

    body = []
    for i in range(6):
        v = y0 + (y1 - y0) * i / 5
        body.append(_line(pad["left"], sy(v), width - pad["right"], sy(v), GRID))
        body.append(_text(pad["left"] - 10, sy(v) + 4, f"{v * 100:.0f}%", anchor="end", fill=MUTED))
    for e in range(math.ceil(x0), math.floor(x1) + 1):
        v = 10.0**e
        body.append(_line(sx(v), pad["top"], sx(v), height - pad["bottom"], GRID))
        body.append(
            _text(sx(v), height - pad["bottom"] + 18, f"${v:g}", anchor="middle", fill=MUTED)
        )
    body.append(
        _line(
            pad["left"], height - pad["bottom"], width - pad["right"], height - pad["bottom"], AXIS
        )
    )
    body.append(_line(pad["left"], pad["top"], pad["left"], height - pad["bottom"], AXIS))
    body.append(
        _text(
            (width + pad["left"]) / 2,
            height - 12,
            "Dollars per SUCCESS (log scale), lower is better",
            anchor="middle",
            size=12,
        )
    )
    body.append(
        f'<text x="18" y="{(height - pad["bottom"] + pad["top"]) / 2:.1f}" text-anchor="middle" fill="{INK}" '
        f'font-size="12" font-family="system-ui,sans-serif" '
        f'transform="rotate(-90 18 {(height - pad["bottom"] + pad["top"]) / 2:.1f})">pass@1</text>'
    )

    frontier = sorted(
        (p for p in points if p.get("on_pareto_frontier")), key=lambda p: p["usd_per_success"]
    )
    if len(frontier) > 1:
        d = " ".join(
            f"{'M' if i == 0 else 'L'}{sx(p['usd_per_success']):.1f},{sy(p['pass_at_1']['estimate']):.1f}"
            for i, p in enumerate(frontier)
        )
        body.append(
            f'<path d="{d}" fill="none" stroke="{SERIES[2]}" stroke-width="2" opacity="0.55"/>'
        )

    for p in points:
        cx, cy = sx(p["usd_per_success"]), sy(p["pass_at_1"]["estimate"])
        on = bool(p.get("on_pareto_frontier"))
        colour = SERIES[2] if on else SERIES[0]
        body.append(
            _line(
                cx, sy(p["pass_at_1"]["ci_low"]), cx, sy(p["pass_at_1"]["ci_high"]), colour, 2, 0.3
            )
        )
        body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="7" fill="{SURFACE}"/>')
        body.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="{colour}" opacity="{1 if on else 0.72}"><title>{escape(p["label"])}</title></circle>'
        )
        if on or p["pipeline"] == "frontier_all_opus":
            name = p["label"] if len(p["label"]) <= 26 else p["label"][:25] + "..."
            body.append(_text(cx + 12, cy - 9, name, size=11, weight=560))

    return (
        _legend([("On the Pareto frontier", SERIES[2]), ("Dominated", SERIES[0])])
        + _svg(width, height, "\n".join(body), "Cost per success against pass@1")
        + _close(caption, table)
    )


# ==================================================================== bars ==


@dataclass(slots=True)
class Bar:
    label: str
    value: float
    low: float | None = None
    high: float | None = None
    highlight: bool = False


def bars(
    rows: list[Bar], *, unit: str = "", caption: str = "", table: str = "", digits: int = 3
) -> str:
    if not rows:
        return ""
    bar_h, gap = 26, 8
    pad = {"top": 6, "right": 70, "bottom": 32, "left": 230}
    width = 780
    height = pad["top"] + len(rows) * (bar_h + gap) + pad["bottom"]
    top = max(max((r.high or r.value) for r in rows), 1e-6)

    def sx(v: float) -> float:
        return pad["left"] + (v / top) * (width - pad["left"] - pad["right"])

    body = []
    for i in range(5):
        v = top * i / 4
        body.append(_line(sx(v), pad["top"], sx(v), height - pad["bottom"], GRID))
        body.append(
            _text(
                sx(v),
                height - pad["bottom"] + 18,
                f"{v:.{digits}f}".rstrip("0").rstrip(".") or "0",
                anchor="middle",
                fill=MUTED,
            )
        )
    body.append(_line(pad["left"], pad["top"], pad["left"], height - pad["bottom"], AXIS))

    for i, row in enumerate(rows):
        y = pad["top"] + i * (bar_h + gap)
        colour = SERIES[2] if row.highlight else SERIES[0]
        name = row.label if len(row.label) <= 32 else row.label[:31] + "..."
        body.append(_text(pad["left"] - 12, y + bar_h / 2 + 4, name, anchor="end", size=12))
        body.append(
            f'<rect x="{pad["left"]:.1f}" y="{y + 4:.1f}" width="{max(2, sx(row.value) - pad["left"]):.1f}" '
            f'height="{bar_h - 8}" rx="4" fill="{colour}" opacity="{1 if row.highlight else 0.82}"><title>'
            f"{escape(row.label)}: {row.value:.{digits}f}</title></rect>"
        )
        label_x = sx(row.value) + 8
        if row.low is not None and row.high is not None:
            yc = y + bar_h / 2
            body.append(_line(sx(row.low), yc, sx(row.high), yc, INK, 2, 0.42))
            label_x = max(label_x, sx(row.high) + 8)
        body.append(
            _text(label_x, y + bar_h / 2 + 4, f"{row.value:.{digits}f}{unit}", size=11, weight=560)
        )

    return _svg(width, height, "\n".join(body), caption or "bars") + _close(caption, table)


# ================================================================= stacked ==


def stacked(
    rows: list[tuple[str, dict[str, float]]], keys: list[str], *, caption: str = "", table: str = ""
) -> str:
    if not rows:
        return ""
    bar_h, gap = 24, 10
    pad = {"top": 6, "right": 24, "bottom": 30, "left": 230}
    width = 780
    height = pad["top"] + len(rows) * (bar_h + gap) + pad["bottom"]
    inner = width - pad["left"] - pad["right"]

    body = []
    for i in range(5):
        x = pad["left"] + inner * i / 4
        body.append(_line(x, pad["top"], x, height - pad["bottom"], GRID))
        body.append(
            _text(x, height - pad["bottom"] + 18, f"{i * 25}%", anchor="middle", fill=MUTED)
        )

    for i, (label, values) in enumerate(rows):
        y = pad["top"] + i * (bar_h + gap)
        name = label if len(label) <= 32 else label[:31] + "..."
        body.append(_text(pad["left"] - 12, y + bar_h / 2 + 4, name, anchor="end", size=12))
        cursor = float(pad["left"])
        for k, key in enumerate(keys):
            share = values.get(key, 0.0)
            if share <= 0:
                continue
            segment = share * inner
            # A 2px surface gap between segments, not a border.
            body.append(
                f'<rect x="{cursor:.1f}" y="{y + 3:.1f}" width="{max(1, segment - 2):.1f}" height="{bar_h - 6}" '
                f'rx="3" fill="{SERIES[k % len(SERIES)]}"><title>{escape(key)}: {share * 100:.1f}%</title></rect>'
            )
            if segment > 46:
                body.append(
                    _text(
                        cursor + segment / 2 - 1,
                        y + bar_h / 2 + 4,
                        f"{share * 100:.0f}%",
                        anchor="middle",
                        fill=SURFACE,
                        size=10,
                        weight=620,
                    )
                )
            cursor += segment

    return (
        _legend([(k, SERIES[i % len(SERIES)]) for i, k in enumerate(keys)])
        + _svg(width, height, "\n".join(body), "Composition")
        + _close(caption, table)
    )


# ================================================================ grouped ===


def grouped_lines(
    series: list[tuple[str, list[tuple[float, float]]]],
    *,
    x_label: str,
    y_label: str,
    caption: str = "",
    table: str = "",
) -> str:
    if not series:
        return ""
    width, height = 780, 320
    pad = {"top": 18, "right": 96, "bottom": 48, "left": 66}
    all_points = [p for _, pts in series for p in pts]
    xmax = max(p[0] for p in all_points) or 1
    ymax = max(p[1] for p in all_points) * 1.08 or 1

    def sx(v: float) -> float:
        return pad["left"] + (v / xmax) * (width - pad["left"] - pad["right"])

    def sy(v: float) -> float:
        return height - pad["bottom"] - (v / ymax) * (height - pad["top"] - pad["bottom"])

    body = []
    for i in range(5):
        v = ymax * i / 4
        body.append(_line(pad["left"], sy(v), width - pad["right"], sy(v), GRID))
        body.append(_text(pad["left"] - 10, sy(v) + 4, f"{v:,.0f}", anchor="end", fill=MUTED))
    body.append(
        _line(
            pad["left"], height - pad["bottom"], width - pad["right"], height - pad["bottom"], AXIS
        )
    )
    body.append(_text((width + pad["left"]) / 2, height - 10, x_label, anchor="middle", size=12))

    for i, (name, points) in enumerate(series):
        colour = SERIES[i % len(SERIES)]
        d = " ".join(
            f"{'M' if k == 0 else 'L'}{sx(p[0]):.1f},{sy(p[1]):.1f}" for k, p in enumerate(points)
        )
        body.append(
            f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2" stroke-linejoin="round"/>'
        )
        for p in points:
            body.append(f'<circle cx="{sx(p[0]):.1f}" cy="{sy(p[1]):.1f}" r="5" fill="{SURFACE}"/>')
            body.append(
                f'<circle cx="{sx(p[0]):.1f}" cy="{sy(p[1]):.1f}" r="3.5" fill="{colour}"/>'
            )
        last = points[-1]
        body.append(_text(sx(last[0]) + 8, sy(last[1]) + 4, name, size=11, weight=560))

    return _svg(width, height, "\n".join(body), y_label) + _close(caption, table)
