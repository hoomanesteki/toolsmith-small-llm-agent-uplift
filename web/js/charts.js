/* Charts.
 *
 * Hand-written SVG, on purpose. A charting library would be another dependency
 * this project would have to stand behind, and the forms here are five: a
 * scatter with a frontier, a bar with intervals, a stacked bar, a timeline and
 * a line. That is less code than the adapter would be.
 *
 * The rules these follow are not stylistic preferences, they are the ones that
 * make a chart readable:
 *
 *   - one axis, never two. Two measures of different scale get two charts.
 *   - colour follows the entity, never its rank, so filtering never repaints
 *     the survivors.
 *   - thin marks, hairline solid grid, generous padding. Never dashed grid.
 *   - a legend whenever there are two or more series; direct labels only where
 *     they earn their place (the frontier, the extreme, the reference row).
 *   - every chart has a table twin, because a tooltip must enhance a value and
 *     never be the only way to read it.
 *   - text wears ink tokens. A coloured mark beside a label carries identity;
 *     the label itself stays readable.
 *
 * The categorical slots come from a palette validated for colour-vision
 * deficiency in both light and dark modes. They are used in fixed order and
 * never cycled past four.
 */

import { fmt, h, s, shorten, tooltip } from "./ui.js";

const tip = tooltip();

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const SERIES = () => [css("--series-1"), css("--series-2"), css("--series-3"), css("--series-4")];
const INK = () => css("--chart-ink");
const INK2 = () => css("--chart-ink-2");
const MUTED = () => css("--chart-muted");
const GRID = () => css("--chart-grid");
const AXIS = () => css("--chart-axis");
const SURFACE = () => css("--chart-surface");

/* A figure wrapper: chart, caption, and the table twin behind a toggle. */
function figure(svg, { caption, legend, table } = {}) {
  const fig = h("figure", { class: "chart" });
  if (legend) fig.appendChild(legend);
  fig.appendChild(svg);
  const tableWrap = table
    ? h("div", { class: "scroll-x", hidden: true, style: { marginTop: "0.8rem" } }, table)
    : null;
  if (table) {
    const toggle = h(
      "button",
      {
        class: "btn ghost",
        style: { marginTop: "0.7rem" },
        onclick: () => {
          tableWrap.hidden = !tableWrap.hidden;
          toggle.textContent = tableWrap.hidden ? "Show the numbers" : "Hide the numbers";
        },
      },
      "Show the numbers",
    );
    fig.appendChild(toggle);
    fig.appendChild(tableWrap);
  }
  if (caption) fig.appendChild(h("figcaption", {}, caption));
  return fig;
}

function legendOf(items) {
  return h(
    "div",
    { class: "chart-legend" },
    items.map(([label, colour]) =>
      h("span", { style: { color: colour } }, h("i"), h("span", { style: { color: "var(--ink-secondary)" } }, label)),
    ),
  );
}

function dataTable(columns, rows) {
  return h(
    "table",
    { class: "data" },
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.num ? "num" : "" }, c.label)))),
    h(
      "tbody",
      {},
      rows.map((row) =>
        h("tr", {}, columns.map((c) => h("td", { class: c.num ? "num" : "" }, c.get(row)))),
      ),
    ),
  );
}

/* ====================================================== cost versus quality */

/**
 * The headline chart: dollars per success against pass@1, with the computed
 * Pareto frontier drawn through the configurations nothing dominates.
 *
 * One colour for every point and a second for the frontier. Fifteen categorical
 * hues would be fifteen hues in service of one story, which is the most common
 * way a chart misses its point. The story is the frontier.
 */
export function paretoChart(rows, { onSelect, selected } = {}) {
  const W = 760;
  const H = 400;
  const pad = { top: 18, right: 28, bottom: 52, left: 62 };
  const points = rows.filter((r) => r.pass_at_1 && r.usd_per_success > 0 && Number.isFinite(r.usd_per_success));
  if (!points.length) return h("div", { class: "empty" }, "No priced configurations yet.");

  const xs = points.map((p) => Math.log10(p.usd_per_success));
  const ys = points.map((p) => p.pass_at_1.estimate);
  const x0 = Math.min(...xs) - 0.12;
  const x1 = Math.max(...xs) + 0.12;
  const y0 = Math.max(0, Math.min(...ys) - 0.08);
  const y1 = Math.min(1.02, Math.max(...ys) + 0.06);

  const sx = (v) => pad.left + ((Math.log10(v) - x0) / (x1 - x0)) * (W - pad.left - pad.right);
  const sy = (v) => H - pad.bottom - ((v - y0) / (y1 - y0)) * (H - pad.top - pad.bottom);

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Cost per success against pass@1" });

  // Grid: solid hairlines, one shade off the surface. Never dashed.
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const v = y0 + ((y1 - y0) * i) / yTicks;
    svg.appendChild(s("line", { x1: pad.left, x2: W - pad.right, y1: sy(v), y2: sy(v), stroke: GRID(), "stroke-width": 1 }));
    svg.appendChild(
      s("text", { x: pad.left - 10, y: sy(v) + 4, "text-anchor": "end", fill: MUTED(), "font-size": 11 }, fmt.pct(v, 0)),
    );
  }
  for (let e = Math.ceil(x0); e <= Math.floor(x1); e++) {
    const v = Math.pow(10, e);
    svg.appendChild(s("line", { x1: sx(v), x2: sx(v), y1: pad.top, y2: H - pad.bottom, stroke: GRID(), "stroke-width": 1 }));
    svg.appendChild(
      s("text", { x: sx(v), y: H - pad.bottom + 18, "text-anchor": "middle", fill: MUTED(), "font-size": 11 }, `$${v}`),
    );
  }
  svg.appendChild(s("line", { x1: pad.left, x2: W - pad.right, y1: H - pad.bottom, y2: H - pad.bottom, stroke: AXIS(), "stroke-width": 1 }));
  svg.appendChild(s("line", { x1: pad.left, x2: pad.left, y1: pad.top, y2: H - pad.bottom, stroke: AXIS(), "stroke-width": 1 }));
  svg.appendChild(
    s("text", { x: (W + pad.left) / 2, y: H - 10, "text-anchor": "middle", fill: INK2(), "font-size": 12 }, "Dollars per SUCCESS (log scale) - lower is better"),
  );
  svg.appendChild(
    s("text", { x: 16, y: (H - pad.bottom + pad.top) / 2, "text-anchor": "middle", fill: INK2(), "font-size": 12, transform: `rotate(-90 16 ${(H - pad.bottom + pad.top) / 2})` }, "pass@1"),
  );

  // The frontier, drawn before the marks so it sits behind them.
  const frontier = points.filter((p) => p.on_pareto_frontier).sort((a, b) => a.usd_per_success - b.usd_per_success);
  if (frontier.length > 1) {
    const d = frontier.map((p, i) => `${i ? "L" : "M"}${sx(p.usd_per_success)},${sy(p.pass_at_1.estimate)}`).join(" ");
    svg.appendChild(s("path", { d, fill: "none", stroke: SERIES()[2], "stroke-width": 2, "stroke-linejoin": "round", opacity: 0.55 }));
  }

  for (const p of points) {
    const cx = sx(p.usd_per_success);
    const cy = sy(p.pass_at_1.estimate);
    const isFrontier = p.on_pareto_frontier;
    const isSelected = p.pipeline === selected;
    const colour = isFrontier ? SERIES()[2] : SERIES()[0];

    // The interval on the quality axis, so the reader sees the noise floor.
    svg.appendChild(
      s("line", { x1: cx, x2: cx, y1: sy(p.pass_at_1.ci_low), y2: sy(p.pass_at_1.ci_high), stroke: colour, "stroke-width": 2, opacity: 0.3, "stroke-linecap": "round" }),
    );
    // A 2px surface ring rather than a border, so overlapping marks separate.
    svg.appendChild(s("circle", { cx, cy, r: isSelected ? 9 : 7, fill: SURFACE() }));
    svg.appendChild(
      s("circle", {
        cx, cy, r: isSelected ? 7 : 5, fill: colour,
        opacity: isFrontier || isSelected ? 1 : 0.72,
        stroke: isSelected ? INK() : "none", "stroke-width": isSelected ? 2 : 0,
      }),
    );
    // A generous hit area: the mark is 10px, the target is 26px.
    //
    // Focusable, because selecting a point is the Lab's primary interaction and
    // it used to be reachable only with a mouse. The tooltip rows are flattened
    // into the aria-label so a screen reader gets the same numbers the sighted
    // reader gets on hover, rather than "circle".
    const rows = [
      ["pass@1", `${fmt.fixed(p.pass_at_1.estimate)} [${fmt.fixed(p.pass_at_1.ci_low)}, ${fmt.fixed(p.pass_at_1.ci_high)}]`],
      ["pass^k", fmt.fixed(p.pass_hat_k)],
      ["$/task", fmt.usd(p.usd_per_task)],
      ["$/success", fmt.usd(p.usd_per_success)],
      ["escalation", fmt.pct(p.escalation_rate, 0)],
      ...(isFrontier ? [["", "on the Pareto frontier"]] : []),
    ];
    svg.appendChild(
      s("circle", {
        cx, cy, r: 13, fill: "transparent", style: "cursor:pointer",
        tabindex: "0",
        role: "button",
        "data-pipeline": p.pipeline,
        "aria-label": `${p.label}. ${rows.map(([k, v]) => (k ? `${k} ${v}` : v)).join(", ")}`,
        onmouseenter: (e) => tip.show(e, p.label, rows),
        onmousemove: (e) => tip.move(e),
        onmouseleave: () => tip.hide(),
        onfocus: (e) => tip.show(e, p.label, rows),
        onblur: () => tip.hide(),
        onkeydown: (e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          onSelect?.(p.pipeline);
        },
        onclick: () => onSelect?.(p.pipeline),
      }),
    );

    // Direct labels only where they earn it: the frontier and the reference.
    if (isFrontier || p.pipeline === "frontier_all_opus") {
      svg.appendChild(
        s("text", { x: cx + 12, y: cy - 9, fill: INK2(), "font-size": 11, "font-weight": 560 }, shorten(p.label, 26)),
      );
    }
  }

  return figure(svg, {
    legend: legendOf([["On the Pareto frontier", SERIES()[2]], ["Dominated", SERIES()[0]]]),
    caption:
      "Each point is one configuration on identical tasks. The vertical bar is the 95% paired-bootstrap interval on pass@1. The frontier is computed, not drawn by eye: a configuration is on it when nothing else is both cheaper per success and at least as reliable. Unbilled rows (the local model, the controls) are excluded, because free at the margin is not free.",
    table: dataTable(
      [
        { label: "Configuration", get: (r) => r.label },
        { label: "pass@1", num: true, get: (r) => fmt.fixed(r.pass_at_1.estimate) },
        { label: "95% CI", num: true, get: (r) => `${fmt.fixed(r.pass_at_1.ci_low)} - ${fmt.fixed(r.pass_at_1.ci_high)}` },
        { label: "pass^k", num: true, get: (r) => fmt.fixed(r.pass_hat_k) },
        { label: "$/task", num: true, get: (r) => fmt.usd(r.usd_per_task) },
        { label: "$/success", num: true, get: (r) => fmt.usd(r.usd_per_success) },
        { label: "Frontier", get: (r) => (r.on_pareto_frontier ? "yes" : "") },
      ],
      [...points].sort((a, b) => a.usd_per_success - b.usd_per_success),
    ),
  });
}

/* ================================================= horizontal bars with CI */

export function barChart(rows, { value, low, high, label, format = fmt.fixed, caption, highlight } = {}) {
  const barH = 26;
  const gap = 8;
  const pad = { top: 6, right: 56, bottom: 30, left: 210 };
  const H = pad.top + rows.length * (barH + gap) + pad.bottom;
  const W = 760;
  const max = Math.max(...rows.map((r) => high?.(r) ?? value(r)), 0.0001);
  const sx = (v) => pad.left + (v / max) * (W - pad.left - pad.right);

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": label });

  for (let i = 0; i <= 4; i++) {
    const v = (max * i) / 4;
    svg.appendChild(s("line", { x1: sx(v), x2: sx(v), y1: pad.top, y2: H - pad.bottom, stroke: GRID(), "stroke-width": 1 }));
    svg.appendChild(s("text", { x: sx(v), y: H - pad.bottom + 17, "text-anchor": "middle", fill: MUTED(), "font-size": 11 }, format(v)));
  }
  svg.appendChild(s("line", { x1: pad.left, x2: pad.left, y1: pad.top, y2: H - pad.bottom, stroke: AXIS(), "stroke-width": 1 }));

  rows.forEach((row, i) => {
    const y = pad.top + i * (barH + gap);
    const v = value(row);
    const isHot = highlight?.(row);
    const colour = isHot ? SERIES()[2] : SERIES()[0];

    svg.appendChild(
      s("text", { x: pad.left - 12, y: y + barH / 2 + 4, "text-anchor": "end", fill: INK2(), "font-size": 12 },
        shorten(row.label, 30)),
    );
    // 4px rounded data-end, anchored to the baseline.
    svg.appendChild(
      s("rect", { x: pad.left, y: y + 4, width: Math.max(2, sx(v) - pad.left), height: barH - 8, rx: 4, fill: colour, opacity: isHot ? 1 : 0.82 }),
    );
    let labelX = sx(v) + 8;
    if (low && high) {
      const yc = y + barH / 2;
      svg.appendChild(s("line", { x1: sx(low(row)), x2: sx(high(row)), y1: yc, y2: yc, stroke: INK(), "stroke-width": 2, opacity: 0.42, "stroke-linecap": "round" }));
      // Clear the whisker: a value label with an interval drawn through it
      // reads as struck out.
      labelX = Math.max(labelX, sx(high(row)) + 8);
    }
    svg.appendChild(s("text", { x: labelX, y: y + barH / 2 + 4, fill: INK2(), "font-size": 11, "font-weight": 560 }, format(v)));
    svg.appendChild(
      s("rect", {
        x: pad.left, y, width: W - pad.left - pad.right, height: barH, fill: "transparent", style: "cursor:default",
        onmouseenter: (e) => tip.show(e, row.label, [
          [label ?? "value", format(v)],
          ...(low && high ? [["95% CI", `${format(low(row))} - ${format(high(row))}`]] : []),
        ]),
        onmousemove: (e) => tip.move(e),
        onmouseleave: () => tip.hide(),
      }),
    );
  });

  return figure(svg, {
    caption,
    table: dataTable(
      [
        { label: "Configuration", get: (r) => r.label },
        { label: label ?? "Value", num: true, get: (r) => format(value(r)) },
        ...(low && high ? [{ label: "95% CI", num: true, get: (r) => `${format(low(r))} - ${format(high(r))}` }] : []),
      ],
      rows,
    ),
  });
}

/* ================================================= stacked composition bar */

export function stackedBar(rows, keys, { caption, format = fmt.pct } = {}) {
  const barH = 24;
  const gap = 10;
  const pad = { top: 6, right: 20, bottom: 28, left: 210 };
  const W = 760;
  const H = pad.top + rows.length * (barH + gap) + pad.bottom;
  const inner = W - pad.left - pad.right;
  const palette = SERIES();

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Composition" });
  for (let i = 0; i <= 4; i++) {
    const x = pad.left + (inner * i) / 4;
    svg.appendChild(s("line", { x1: x, x2: x, y1: pad.top, y2: H - pad.bottom, stroke: GRID(), "stroke-width": 1 }));
    svg.appendChild(s("text", { x, y: H - pad.bottom + 17, "text-anchor": "middle", fill: MUTED(), "font-size": 11 }, `${i * 25}%`));
  }

  rows.forEach((row, i) => {
    const y = pad.top + i * (barH + gap);
    svg.appendChild(
      s("text", { x: pad.left - 12, y: y + barH / 2 + 4, "text-anchor": "end", fill: INK2(), "font-size": 12 },
        shorten(row.label, 30)),
    );
    let cursor = pad.left;
    keys.forEach((key, k) => {
      const share = row.values[key] ?? 0;
      if (share <= 0) return;
      const width = share * inner;
      // A 2px surface gap between segments, not a border.
      svg.appendChild(
        s("rect", {
          x: cursor, y: y + 3, width: Math.max(1, width - 2), height: barH - 6, rx: 3, fill: palette[k % palette.length],
          onmouseenter: (e) => tip.show(e, row.label, [[key, format(share)]]),
          onmousemove: (e) => tip.move(e),
          onmouseleave: () => tip.hide(),
          style: "cursor:default",
        }),
      );
      // A label only when it fits with padding; otherwise the tooltip carries it.
      if (width > 46) {
        svg.appendChild(
          s("text", { x: cursor + width / 2 - 1, y: y + barH / 2 + 4, "text-anchor": "middle", fill: SURFACE(), "font-size": 10, "font-weight": 620 }, format(share, 0)),
        );
      }
      cursor += width;
    });
  });

  return figure(svg, {
    legend: legendOf(keys.map((k, i) => [k, palette[i % palette.length]])),
    caption,
    table: dataTable(
      [{ label: "Configuration", get: (r) => r.label }, ...keys.map((k) => ({ label: k, num: true, get: (r) => format(r.values[k] ?? 0) }))],
      rows,
    ),
  });
}

/* ============================================================== timeline == */

/**
 * The trace waterfall: absolutely positioned spans on one shared time axis.
 * The highest impact-per-line in the whole UI, and the fastest way to see that
 * a run spent 80% of its wall-clock in one planner call.
 */
export function waterfall(spans, { caption } = {}) {
  if (!spans.length) return h("div", { class: "empty" }, "No timed spans in this run.");
  const rowH = 22;
  const pad = { top: 8, right: 24, bottom: 30, left: 172 };
  const W = 760;
  const H = pad.top + spans.length * rowH + pad.bottom;
  const total = Math.max(...spans.map((sp) => sp.start + sp.duration), 0.001);
  const sx = (v) => pad.left + (v / total) * (W - pad.left - pad.right);
  const palette = SERIES();
  const kinds = [...new Set(spans.map((sp) => sp.kind))];

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Trace timeline" });
  for (let i = 0; i <= 4; i++) {
    const v = (total * i) / 4;
    svg.appendChild(s("line", { x1: sx(v), x2: sx(v), y1: pad.top, y2: H - pad.bottom, stroke: GRID(), "stroke-width": 1 }));
    svg.appendChild(s("text", { x: sx(v), y: H - pad.bottom + 17, "text-anchor": "middle", fill: MUTED(), "font-size": 11 }, fmt.ms(v)));
  }

  spans.forEach((span, i) => {
    const y = pad.top + i * rowH;
    const colour = palette[kinds.indexOf(span.kind) % palette.length];
    svg.appendChild(
      s("text", { x: pad.left - 10, y: y + rowH / 2 + 4, "text-anchor": "end", fill: INK2(), "font-size": 11 },
        shorten(span.label, 24)),
    );
    svg.appendChild(
      s("rect", {
        x: sx(span.start), y: y + 4, width: Math.max(3, sx(span.start + span.duration) - sx(span.start) - 2),
        height: rowH - 8, rx: 3, fill: colour,
        onmouseenter: (e) => tip.show(e, span.label, [["stage", span.kind], ["start", fmt.ms(span.start)], ["duration", fmt.ms(span.duration)], ...(span.detail ? [["", span.detail]] : [])]),
        onmousemove: (e) => tip.move(e),
        onmouseleave: () => tip.hide(),
        style: "cursor:default",
      }),
    );
  });

  return figure(svg, {
    legend: legendOf(kinds.map((k, i) => [k, palette[i % palette.length]])),
    caption,
    table: dataTable(
      [
        { label: "Span", get: (r) => r.label },
        { label: "Stage", get: (r) => r.kind },
        { label: "Start", num: true, get: (r) => fmt.ms(r.start) },
        { label: "Duration", num: true, get: (r) => fmt.ms(r.duration) },
      ],
      spans,
    ),
  });
}
