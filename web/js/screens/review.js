/* The Review screen: the human-in-the-loop queue.
 *
 * Ordered by judge disagreement, because labelling items the panel already
 * agrees on measures nothing. A label written here feeds the calibration that
 * the report publishes, which is the honest answer to "how do you improve
 * without ground truth": you manufacture the labels where they are worth most.
 */

import { api } from "../api.js";
import { blurbs, go } from "../app.js";
import { card, chip, clear, empty, fmt, h, orientation, stat } from "../ui.js";

export async function review(host) {
  clear(host);
  host.appendChild(h("h1", {}, "What needs a person"));
  host.appendChild(orientation(blurbs.review));
  host.appendChild(
    h("p", { class: "lede", html:
      "Contested judgments first. Where the panel splits, a human label changes the calibration; where it agrees, " +
      "another label measures nothing. This is also the failure gallery: the worst transcripts, ranked by how much " +
      "the failure should worry you rather than by how often it happens." }),
  );

  const [queue, calibration, gallery, traces] = await Promise.all([
    api.reviewQueue(60).catch(() => ({ total: 0, queue: [] })),
    api.calibration().catch(() => null),
    api.gallery(12).catch(() => ({ total: 0, failures: [] })),
    api.traces().catch(() => ({ traces: [] })),
  ]);

  host.appendChild(h("div", { class: "grid", style: { marginBottom: "1.4rem" } },
    card(null, null, stat("Judgments", fmt.num(queue.total), "one per task per configuration")),
    card(null, null, stat("Contested", fmt.num(queue.contested ?? 0), "panel split by two points or more")),
    card(null, null, stat("Human labels", fmt.num(queue.labelled ?? 0), calibration?.sufficient ? "calibration is published" : `${calibration?.min_labels ?? 150} needed to calibrate`)),
    card(null, null, stat("Failures to inspect", fmt.num(gallery.total), "ranked by severity, not frequency")),
  ));

  if (calibration && !calibration.sufficient) {
    host.appendChild(h("div", { class: "banner" }, h("span", {}, "⚠️"),
      h("div", {}, h("strong", {}, "Uncalibrated. "), calibration.note)));
  }

  /* -- the gallery -------------------------------------------------------- */
  if (gallery.failures.length) {
    /* A failure you cannot open is a claim, not evidence. Rows whose trace was
       kept link straight into Flow with that run selected; the rest stay plain
       text rather than becoming a link that goes nowhere. */
    const recorded = new Set(traces.traces.map((t) => t.run_id));
    const rows = gallery.failures.map((f) => {
      const runId = `${f.pipeline}:${f.task_id}:0`;
      const title = recorded.has(runId)
        ? h("a", {
            href: `/flow?run=${encodeURIComponent(runId)}`,
            title: "Watch this run stage by stage",
            onclick: (event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey) return;
              event.preventDefault();
              go("flow", { params: { run: runId } });
            },
          }, h("b", {}, f.task_id))
        : h("b", {}, f.task_id);
      return h("tr", {},
        h("td", {}, h("div", {}, title), h("div", { class: "hint" }, f.prompt.slice(0, 90))),
        h("td", {}, f.pipeline),
        h("td", {}, f.tier),
        h("td", {}, h("div", { class: "row", style: { margin: 0, gap: "0.3rem" } },
          ...f.failure_modes.slice(0, 3).map((m) => chip(m.replace(/_/g, " "), f.severity >= 70 ? "bad" : "warn")))),
        h("td", { class: "num" }, `${f.calls_made}/${f.calls_oracle}`),
        h("td", { class: "num" }, fmt.usd(f.usd)),
      );
    });
    host.appendChild(card(
      "Where it loses",
      "An unsanctioned privileged action is one occurrence and outranks a hundred wrong parameters, so this list is ordered by severity.",
      h("div", { class: "scroll-x" },
        h("table", { class: "data" },
          h("thead", {}, h("tr", {}, ["Task", "Configuration", "Tier", "Diagnosis", "Calls", "Cost"].map((c) => h("th", { class: c === "Calls" || c === "Cost" ? "num" : "" }, c)))),
          h("tbody", {}, rows))),
    ));
  }

  /* -- the labelling queue ------------------------------------------------ */
  if (!queue.queue?.length) {
    host.appendChild(
      empty(
        "No judgments yet. Run ",
        h("code", {}, "uv run toolsmith matrix run"),
        " with the judge panel enabled.",
      ),
    );
    return;
  }

  const list = h("div");
  host.appendChild(card("The queue", "Highest disagreement first.", list));

  for (const item of queue.queue.slice(0, 24)) {
    const status = h("span", { class: "hint" }, item.labelled ? "labelled" : "");
    const behaviour = h("select", { "aria-label": "Behaviour" },
      ["answer", "abstain", "refuse", "clarify"].map((b) => h("option", { value: b }, b)));
    const correct = h("select", { "aria-label": "Correct" },
      h("option", { value: "true" }, "correct"), h("option", { value: "false" }, "incorrect"));
    const notes = h("input", { type: "text", placeholder: "why (optional)", style: { minWidth: "16rem" } });
    const save = h("button", { class: "btn ghost" }, "Save label");

    save.onclick = async () => {
      save.disabled = true;
      try {
        const result = await api.label({
          task_id: item.task_id, pipeline: item.pipeline,
          behaviour: behaviour.value, correct: correct.value === "true", notes: notes.value,
        });
        status.textContent = `saved (${result.total_labels} labels)`;
      } catch (error) {
        status.textContent = `failed: ${error.message}`;
      } finally {
        save.disabled = false;
      }
    };

    const perJudge = Object.entries(item.per_judge ?? {}).map(([seat, scores]) =>
      h("tr", {}, h("td", {}, seat), ...Object.entries(scores).map(([, v]) => h("td", { class: "num" }, v))));
    const dimensions = Object.keys(Object.values(item.per_judge ?? {})[0] ?? {});

    list.appendChild(h("div", { style: { padding: "0.9rem 0", borderBottom: "1px solid var(--hairline)" } },
      h("div", { class: "row", style: { marginBottom: "0.5rem" } },
        h("b", {}, item.task_id),
        chip(item.pipeline),
        item.contested ? chip(`disagreement ${item.disagreement}`, "warn") : chip("consensus", "good"),
        ...Object.entries(item.dropped_seats ?? {}).map(([seat]) => chip(`${seat} dropped: same family`, "warn")),
        status,
      ),
      h("p", { class: "hint", style: { marginBottom: "0.5rem" } }, item.prompt),
      dimensions.length
        ? h("div", { class: "scroll-x" }, h("table", { class: "data" },
            h("thead", {}, h("tr", {}, h("th", {}, "Judge"), ...dimensions.map((d) => h("th", { class: "num" }, d)))),
            h("tbody", {}, perJudge)))
        : null,
      h("div", { class: "row", style: { marginTop: "0.6rem", marginBottom: 0 } },
        h("label", { class: "field" }, "Behaviour", behaviour),
        h("label", { class: "field" }, "Verdict", correct),
        notes, save),
    ));
  }
}
