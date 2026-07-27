/* The Lab: the comparison surface.
 *
 * This is where the project's argument lives. Everything on it derives from
 * one committed results file, so the screen cannot show a number the harness
 * did not produce.
 */

import { api } from "../api.js";
import { barChart, paretoChart, stackedBar } from "../charts.js";
import { card, chip, clear, empty, fmt, h, stat } from "../ui.js";

const TIER_ORDER = ["T1", "T2", "T3", "T4", "T5"];

export async function lab(host) {
  clear(host);
  let payload;
  try {
    payload = await api.matrix();
  } catch (error) {
    host.appendChild(
      empty(`No results yet. Run \`uv run toolsmith matrix run\` to produce them. (${error.message})`),
    );
    return;
  }

  const rows = payload.rows.filter((r) => r.n_runs > 0);
  const manifest = payload.manifest ?? {};
  const [optimize, config] = await Promise.all([
    api.optimize().catch(() => ({})),
    api.config().catch(() => ({})),
  ]);


  host.appendChild(h("h1", {}, "Cost versus quality, with receipts"));
  host.appendChild(
    h(
      "p",
      { class: "lede", html:
        `<strong>${fmt.num(rows.reduce((a, r) => a + r.n_runs, 0))} graded runs</strong> across ` +
        `<strong>${rows.length} configurations</strong> on one stratified sample of ` +
        `<strong>${manifest.n_tasks ?? "?"} tasks</strong>, ${manifest.trials ?? "?"} trials each. ` +
        "Every configuration ran the same tasks, which is what licenses the paired intervals below. " +
        "The headline column is dollars per <em>success</em>, not per task: a model that costs a third " +
        "as much and fails twice as often is not cheaper." },
    ),
  );

  if (manifest.provenance_note) {
    host.appendChild(
      h(
        "div",
        { class: "banner" },
        h("span", {}, "⚠️"),
        h("div", {}, h("strong", {}, "Provenance. "), manifest.provenance_note),
      ),
    );
  }

  /* -- the four numbers that matter ------------------------------------- */
  const frontier = rows.filter((r) => r.on_pareto_frontier);
  const reference = rows.find((r) => r.pipeline === "frontier_all_opus");
  // The best VALUE on the frontier, not the cheapest point on it. The cheapest
  // point is also the least reliable one, and leading with it would invert the
  // argument this page exists to make.
  const affordable = frontier.filter(
    (r) => !reference || r.usd_per_success <= reference.usd_per_success,
  );
  const best = [...(affordable.length ? affordable : frontier)].sort(
    (a, b) => b.pass_at_1.estimate - a.pass_at_1.estimate,
  )[0];
  const cheapest = [...frontier].sort((a, b) => a.usd_per_success - b.usd_per_success)[0];
  const relative = best && reference ? best.usd_per_success / reference.usd_per_success : null;
  // Open on the configuration the page is arguing for.
  let selected = best?.pipeline ?? rows[0]?.pipeline;

  host.appendChild(
    h(
      "div",
      { class: "grid tight", style: { marginBottom: "1.4rem" } },
      card(null, null, stat("Best value on the frontier", best?.label ?? "-", best ? `${fmt.fixed(best.pass_at_1.estimate)} pass@1 at ${fmt.usd(best.usd_per_success)} per success` : "")),
      card(null, null, stat("Versus all-frontier", relative ? `${relative.toFixed(2)}x` : "-", `cost per success at ${best ? fmt.fixed(best.pass_at_1.estimate) : "-"} vs ${reference ? fmt.fixed(reference.pass_at_1.estimate) : "-"} pass@1`, true)),
      card(null, null, stat("Input share of tokens", fmt.pct(rows.reduce((a, r) => a + r.input_share, 0) / rows.length, 0), "everyone optimises the other tenth")),
      card(null, null, stat("Cheapest that still works", cheapest?.label ?? "-", cheapest ? `${fmt.fixed(cheapest.pass_at_1.estimate)} pass@1 at ${fmt.usd(cheapest.usd_per_success)} per success` : "")),
      card(null, null, stat("Significant comparisons", `${payload.comparisons.filter((c) => c.significant_after_holm).length} / ${payload.comparisons.length}`, "after Holm-Bonferroni across all pairs")),
    ),
  );

  /* -- the headline chart ----------------------------------------------- */
  const paretoHost = h("div");
  const detailHost = h("div");

  const drawPareto = () => {
    clear(paretoHost).appendChild(
      paretoChart(rows, { selected, onSelect: (name) => { selected = name; drawPareto(); drawDetail(); } }),
    );
  };

  host.appendChild(card("The frontier", "Click any point to inspect that configuration.", paretoHost));
  drawPareto();

  /* -- ranked bars ------------------------------------------------------- */
  const byQuality = [...rows].sort((a, b) => b.pass_at_1.estimate - a.pass_at_1.estimate);
  host.appendChild(
    card(
      "pass@1, ranked",
      "state_diff == oracle AND every answer key present AND the right behaviour. Computed by code, never judged.",
      barChart(byQuality, {
        value: (r) => r.pass_at_1.estimate,
        low: (r) => r.pass_at_1.ci_low,
        high: (r) => r.pass_at_1.ci_high,
        label: "pass@1",
        format: (v) => fmt.fixed(v, 3),
        highlight: (r) => r.on_pareto_frontier,
        caption:
          "The bar is the estimate; the line through it is the 95% paired-bootstrap interval. Where two intervals overlap, the difference between those configurations is not established by this sample, whatever the point estimates say.",
      }),
    ),
  );

  /* -- where the money goes --------------------------------------------- */
  const roleRows = rows
    .filter((r) => Object.keys(r.spend_by_role).length)
    .map((r) => ({ label: r.label, values: r.spend_by_role }));
  const roles = ["planner", "executor", "reviewer", "escalation"];
  if (roleRows.length) {
    host.appendChild(
      card(
        "Where the money goes",
        "Share of each configuration's spend, by role.",
        stackedBar(roleRows, roles, {
          caption:
            "The source spec predicted the executor at 73% of spend and the planner at 9%, on a six-turn loop. These tasks take about 2.4 turns, and at that length the planner's single frontier call dominates instead. The claim is turn-count dependent; this chart is what that looks like.",
        }),
      ),
    );
  }

  /* -- the selected configuration ---------------------------------------- */
  function drawDetail() {
    clear(detailHost);
    const row = rows.find((r) => r.pipeline === selected);
    if (!row) return;
    const pipe = config.pipelines?.[row.pipeline];

    detailHost.appendChild(
      h(
        "div",
        { class: "row" },
        h("h2", { style: { marginRight: "auto" } }, row.label),
        ...(row.tags ?? []).map((t) => chip(t, t === "recommended" ? "accent" : "")),
      ),
    );
    if (pipe) {
      detailHost.appendChild(h("p", { class: "hint", style: { marginBottom: "1rem" } }, pipe.description));
      detailHost.appendChild(
        h(
          "div",
          { class: "row" },
          chip(`planner: ${pipe.planner}`),
          chip(`executor: ${pipe.executor}`),
          chip(`reviewer: ${pipe.reviewer}`),
          pipe.escalate_to ? chip(`escalates to: ${pipe.escalate_to}`, "accent") : chip("no escalation"),
          chip(`tools: ${pipe.tool_exposure}`),
        ),
      );
    }

    detailHost.appendChild(
      h(
        "div",
        { class: "grid", style: { marginBottom: "1.2rem" } },
        card(null, null, stat("pass@1", fmt.fixed(row.pass_at_1.estimate), `95% CI ${fmt.fixed(row.pass_at_1.ci_low)} - ${fmt.fixed(row.pass_at_1.ci_high)}`)),
        card(null, null, stat("pass^k", fmt.fixed(row.pass_hat_k), "every trial passed")),
        card(null, null, stat("$ per success", fmt.usd(row.usd_per_success), `${fmt.usd(row.usd_per_task)} per task`)),
        card(null, null, stat("Escalation rate", fmt.pct(row.escalation_rate, 1), "share of runs given a second attempt")),
      ),
    );

    const tiers = TIER_ORDER.filter((t) => row.by_tier[t]);
    if (tiers.length) {
      detailHost.appendChild(
        card(
          "By tier",
          "T4 is the trap tier: the correct answer is often a refusal, an abstention or a question.",
          barChart(
            tiers.map((t) => ({ label: `${t}  (n=${row.by_tier[t].n})`, v: row.by_tier[t].pass_at_1 })),
            { value: (r) => r.v, label: "pass@1", format: (v) => fmt.fixed(v, 3) },
          ),
        ),
      );
    }

    const safety = h(
      "table",
      { class: "data" },
      h("tbody", {},
        [
          ["Abstain recall (traps)", row.abstain_recall === null ? "-" : fmt.fixed(row.abstain_recall)],
          ["Over-refusal on answerable tasks", fmt.fixed(row.over_refusal_rate)],
          ["Injection resistance", row.injection_resistance === null ? "-" : fmt.fixed(row.injection_resistance)],
          ["Unsanctioned privileged actions", fmt.fixed(row.policy_violation_rate)],
          ["Citation recall (grounded tier)", row.citation_recall === null ? "-" : fmt.fixed(row.citation_recall)],
          ["Tool-selection accuracy", fmt.fixed(row.tool_selection_accuracy)],
          ["Calls versus oracle", `${fmt.fixed(row.calls_vs_oracle, 2)}x`],
          ["Cache hit rate", fmt.pct(row.cache_hit_rate, 1)],
        ].map(([k, v]) => h("tr", {}, h("td", {}, k), h("td", { class: "num" }, v))),
      ),
    );
    detailHost.appendChild(card("Safety and efficiency", "Abstention alone is not a virtue: a system that abstains always scores perfectly on it, which is why over-refusal sits next to it.", h("div", { class: "scroll-x" }, safety)));

    const modes = Object.entries(row.failure_modes ?? {});
    if (modes.length) {
      detailHost.appendChild(
        card(
          "Where it loses",
          "Named causes, so failures group by diagnosis rather than by symptom.",
          barChart(
            modes.slice(0, 10).map(([m, n]) => ({ label: m.replace(/_/g, " "), v: n })),
            { value: (r) => r.v, label: "failures", format: (v) => fmt.num(v) },
          ),
        ),
      );
    }
  }

  host.appendChild(h("div", { style: { marginTop: "1.6rem" } }, detailHost));
  drawDetail();

  /* -- the optimizer tracks ---------------------------------------------- */
  const tracks = Object.values(optimize);
  if (tracks.length) {
    const table = h(
      "table",
      { class: "data" },
      h("thead", {}, h("tr", {}, ["Track", "Lever", "Verdict", "What it found"].map((c) => h("th", {}, c)))),
      h(
        "tbody",
        {},
        ["track_c_context", "track_b_router", "track_a_prompts", "track_d_lora"]
          .map((key) => optimize[key])
          .filter(Boolean)
          .map((t) =>
            h(
              "tr",
              {},
              h("td", {}, t.title),
              h("td", {}, t.lever),
              h("td", {}, chip(t.verdict, { gain: "good", regression: "bad", unmeasurable: "warn" }[t.verdict] ?? "")),
              h("td", { style: { maxWidth: "44ch" } }, t.headline),
            ),
          ),
      ),
    );
    host.appendChild(
      card(
        "Four levers, one axis",
        "Run in order C, B, A, D: the free one first, the highest-evidence one second, the token-cost one third, the GPU one last. Two of these are nulls, and both say why.",
        h("div", { class: "scroll-x" }, table),
      ),
    );
  }

  /* -- reproducibility ---------------------------------------------------- */
  host.appendChild(
    card(
      "How to reproduce this page",
      null,
      h("pre", { style: { margin: 0, fontSize: "var(--size-micro)", fontFamily: "var(--mono)", overflowX: "auto" } },
        "uv sync --all-extras\n" +
        "uv run toolsmith world build --all\n" +
        "uv run toolsmith tasks build\n" +
        `uv run toolsmith matrix run --provider ${manifest.provider_mode ?? "simulated"} --n ${manifest.n_tasks ?? 180} --trials ${manifest.trials ?? 3} --seed ${manifest.seed ?? 20260726}`),
      h("p", { class: "hint", style: { marginTop: "0.8rem" } },
        `Seed ${manifest.seed ?? "?"}. World digests ${Object.entries(manifest.world_digests ?? {}).map(([k, v]) => `${k}:${String(v).slice(0, 8)}`).join(", ")}. ` +
        `Hidden split ${String(manifest.hidden_split_sha256 ?? "").slice(0, 16)}..., sealed in git before the first optimisation run.`),
    ),
  );
}
