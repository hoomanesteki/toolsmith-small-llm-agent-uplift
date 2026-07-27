/* The Flow screen: one run, watched.
 *
 * The graph is derived from the event stream rather than declared, so a run
 * that escalated grows an escalation node and one that did not, does not. The
 * picture cannot drift from the behaviour.
 *
 * Live and replay use the same renderer because they are the same data. The
 * runtime's only output is events; a committed trace is that output, saved.
 */

import { api, replay } from "../api.js";
import { waterfall } from "../charts.js";
import { card, chip, clear, empty, fmt, h, orientation, stat, ticker } from "../ui.js";

const NODE_ORDER = ["gate_in", "planner", "executor", "reviewer", "escalation", "gate_out", "answer"];

const NODE_LABELS = {
  gate_in: ["Input gate", "gate"],
  planner: ["Planner", "role"],
  executor: ["Executor", "role"],
  reviewer: ["Reviewer", "role"],
  escalation: ["Escalation", "role"],
  gate_out: ["Output gate", "gate"],
  answer: ["Response", "answer"],
};

export async function flow(host) {
  clear(host);
  host.appendChild(h("h1", {}, "One run, watched"));
  host.appendChild(orientation(blurbs.flow));
  host.appendChild(
    h("p", { class: "lede", html:
      "Every stage emits a typed event, and this is the whole of them. A <strong>replayed</strong> run and a " +
      "<strong>live</strong> one look identical here because they are the same stream: the runtime's only output " +
      "is events. That is what makes the demo work on a fresh clone with no keys." }),
  );

  const [traces, health] = await Promise.all([api.traces(), api.health()]);
  const controls = h("div", { class: "row" });
  const stage = h("div");
  host.appendChild(controls);
  host.appendChild(stage);

  if (!traces.total) {
    stage.appendChild(
      empty(
        "No committed traces yet. Run ",
        h("code", {}, "uv run toolsmith matrix run"),
        " to record some.",
      ),
    );
    return;
  }

  /* Arrived from the Lab or the failure gallery? Open on the run they clicked,
     rather than on whichever trace happens to sort first. Before this the two
     screens could name a run and not reach it. */
  const wanted = new URLSearchParams(window.location.search).get("run");
  const traceSelect = h(
    "select",
    { id: "trace-pick", "aria-label": "Recorded run" },
    traces.traces.map((t) =>
      h("option", { value: t.run_id }, `${t.pipeline} · ${t.task_id} · ${t.tier}`),
    ),
  );
  if (wanted && traces.traces.some((t) => t.run_id === wanted)) traceSelect.value = wanted;

  const speed = h(
    "select",
    { "aria-label": "Playback speed" },
    h("option", { value: "90" }, "Slow"),
    h("option", { value: "55", selected: true }, "Normal"),
    h("option", { value: "12" }, "Fast"),
  );
  const play = h("button", { class: "btn" }, "Replay this run");
  const liveButton = h(
    "button",
    { class: "btn ghost", title: health.live_capable.length ? "Runs against a live provider" : "No keys present: runs through the simulator" },
    health.live_capable.length ? "Run live" : "Run now (simulated)",
  );

  controls.appendChild(h("label", { class: "field" }, "Recorded run", traceSelect));
  controls.appendChild(h("label", { class: "field" }, "Speed", speed));
  controls.appendChild(play);
  controls.appendChild(liveButton);

  let stop = null;

  play.onclick = async () => {
    stop?.();
    const payload = await api.trace(traceSelect.value);
    stop = render(stage, payload.events, Number(speed.value), { source: "replay" });
  };

  liveButton.onclick = async () => {
    stop?.();
    const chosen = traces.traces.find((t) => t.run_id === traceSelect.value);
    if (!chosen) return;
    const events = [];
    const view = render(stage, [], 0, { source: "live", streaming: true });
    stop = api.stream(
      { taskId: chosen.task_id, pipeline: chosen.pipeline, provider: health.live_capable.length ? "auto" : "simulated" },
      (event) => { events.push(event); view.push(event); },
      () => view.finish(),
      /* Not view.finish(). A stream that dies mid-run used to report "done"
         beside a transcript that stops in the middle, which is the one outcome
         a viewer cannot tell from success. */
      (error) => view.fail(error, events.length),
    );
  };

  play.click();

  /* Leaving this screen with a live run open used to keep an EventSource
     pushing events into a detached DOM until the run finished. The router calls
     this before it clears the host. */
  return () => stop?.();
}

/**
 * Render an event stream into the graph, the transcript and the timeline.
 * Returns a stop function when replaying, or a push/finish pair when live.
 */
function render(host, events, delay, { source, streaming = false } = {}) {
  clear(host);

  const summary = h("div", { class: "grid", style: { marginBottom: "1.1rem" } });
  const costEl = h("span", { class: "value ticker", dataset: { value: "0" } }, "$0.00000");
  const turnsEl = h("span", { class: "value" }, "0");
  const callsEl = h("span", { class: "value" }, "0");
  const statusEl = h("span", { class: "value" }, streaming ? "running" : "replaying");

  summary.appendChild(card(null, null, h("div", { class: "stat" }, h("span", { class: "label" }, "Cost so far"), costEl, h("span", { class: "sub" }, "billed per call, as it happens"))));
  summary.appendChild(card(null, null, h("div", { class: "stat" }, h("span", { class: "label" }, "Turns"), turnsEl, h("span", { class: "sub" }, "executor iterations"))));
  summary.appendChild(card(null, null, h("div", { class: "stat" }, h("span", { class: "label" }, "Tool calls"), callsEl, h("span", { class: "sub" }, "against the sandboxed world"))));
  summary.appendChild(card(null, null, h("div", { class: "stat" }, h("span", { class: "label" }, "Status"), statusEl, h("span", { class: "sub" }, source === "live" ? "live stream" : "committed trace"))));
  host.appendChild(summary);

  const graph = h("div", { class: "flow" });
  const nodes = new Map();
  for (const id of NODE_ORDER) {
    const [label, kind] = NODE_LABELS[id];
    const node = h(
      "div",
      { class: "node", dataset: { state: "idle", kind } },
      h("div", { class: "kind" }, kind),
      h("div", { class: "name" }, label),
      h("div", { class: "meta" }, ""),
    );
    nodes.set(id, node);
    graph.appendChild(node);
  }
  host.appendChild(card("The agent graph", "Nodes light up as they execute. Tool nodes appear only when a tool is actually called.", graph));

  const transcript = h("div");
  host.appendChild(card("Transcript", "Every gate verdict, every tool call, every model call.", transcript));

  const timelineHost = h("div");
  host.appendChild(card("Where the time went", "Modelled provider latency, not measured wall-clock.", timelineHost));

  const state = { usd: 0, turns: 0, calls: 0, spans: [], clock: 0, active: null };

  function activate(id, meta) {
    const node = nodes.get(id);
    if (!node) return;
    if (state.active && state.active !== id) nodes.get(state.active)?.setAttribute("data-state", "done");
    node.dataset.state = "active";
    state.active = id;
    if (meta) node.querySelector(".meta").textContent = meta;
  }

  function addTurn(kind, head, body) {
    const turn = h("div", { class: "turn", dataset: { kind } }, h("div", { class: "head" }, ...head));
    if (body) turn.appendChild(h("pre", {}, body));
    transcript.appendChild(turn);
    transcript.scrollTop = transcript.scrollHeight;
  }

  function push(event) {
    const d = event.data ?? {};
    switch (event.type) {
      case "run.started":
        addTurn("model", [h("b", {}, "Request"), chip(d.tier ?? ""), chip(d.world ?? "")], d.prompt);
        break;
      case "gate.input":
        activate("gate_in", d.action);
        addTurn("gate", [h("b", {}, "Input gate"), chip(d.action, d.action === "allow" ? "good" : "warn"), h("span", {}, d.reason || "clean")]);
        break;
      case "plan.created":
        activate("planner", d.model);
        addTurn("model", [h("b", {}, "Planner"), chip(d.model ?? "")], d.plan);
        break;
      case "turn.started":
        state.turns = Math.max(state.turns, (d.turn ?? 0) + 1);
        turnsEl.textContent = String(state.turns);
        activate("executor", `${d.model ?? ""} · turn ${d.turn}`);
        break;
      case "tool.searched":
        addTurn("tool", [h("b", {}, "search_tools"), h("span", {}, `"${d.query}"`), chip(`${(d.found ?? []).length} schemas`)], (d.found ?? []).join("\n"));
        break;
      case "tool.called": {
        const nodeId = `tool_${d.tool}`;
        if (!nodes.has(nodeId)) {
          const node = h(
            "div",
            { class: "node", dataset: { state: "idle", kind: "tool", privileged: String(!!d.privileged) } },
            h("div", { class: "kind" }, d.privileged ? "privileged" : "tool"),
            h("div", { class: "name" }, d.tool),
            h("div", { class: "meta" }, ""),
          );
          nodes.set(nodeId, node);
          graph.insertBefore(node, nodes.get("reviewer"));
        }
        activate(nodeId, d.privileged ? "server-authorised" : "");
        state.calls += 1;
        callsEl.textContent = String(state.calls);
        addTurn("tool", [h("b", {}, d.tool), d.privileged ? chip("PRIVILEGED", "warn") : null], JSON.stringify(d.arguments, null, 2));
        break;
      }
      case "tool.result": {
        const ms = Number(d.latency_ms ?? 0) / 1000;
        state.spans.push({ label: d.tool, kind: "tool", start: state.clock, duration: ms, detail: d.policy || (d.ok ? "ok" : d.error_code) });
        state.clock += ms;
        addTurn("tool", [h("b", {}, `${d.tool} →`), chip(d.ok ? "ok" : d.error_code || "failed", d.ok ? "good" : "bad"), d.mutated ? chip("mutated the world", "warn") : null], d.preview);
        break;
      }
      case "gate.tool_result":
        if ((d.rules ?? []).length) {
          addTurn("gate", [h("b", {}, "Tool-result gate"), chip("injection detected", "bad"), h("span", {}, (d.rules ?? []).join(", "))], d.reason);
        }
        break;
      case "model.call": {
        const seconds = Number(d.latency_s ?? 0);
        state.usd += Number(d.usd ?? 0);
        ticker(costEl, state.usd, fmt.usd);
        state.spans.push({ label: `${d.role}: ${d.model}`, kind: d.role, start: state.clock, duration: seconds, detail: `${d.tokens_in} in / ${d.tokens_out} out` });
        state.clock += seconds;
        break;
      }
      case "review.verdict":
        activate("reviewer", `${d.model} · ${d.verdict}`);
        addTurn("model", [h("b", {}, "Reviewer"), chip(d.verdict, d.verdict === "accept" ? "good" : "warn"), chip(d.cross_family ? "cross-family" : "same family", d.cross_family ? "" : "warn")]);
        break;
      case "escalation.started":
        activate("escalation", d.model);
        addTurn("model", [h("b", {}, "Escalation"), chip(d.trigger, "warn"), h("span", {}, d.reason)]);
        break;
      case "gate.output":
        activate("gate_out", d.action);
        addTurn("gate", [h("b", {}, "Output gate"), chip(d.action, d.action === "allow" ? "good" : "warn"), h("span", {}, d.reason || "grounded")]);
        break;
      case "answer":
        activate("answer", d.behaviour);
        transcript.appendChild(h("div", { class: "answer" }, h("div", { class: "row", style: { marginBottom: "0.4rem" } }, chip(d.behaviour ?? "answer", "accent"), ...(d.citations ?? []).map((c) => chip(c))), h("div", {}, d.text)));
        break;
      case "run.finished":
        statusEl.textContent = d.behaviour ?? "done";
        for (const node of nodes.values()) if (node.dataset.state === "active") node.dataset.state = "done";
        drawTimeline();
        break;
      case "run.failed":
        statusEl.textContent = "failed";
        addTurn("gate", [h("b", {}, "Run failed"), chip("error", "bad")], d.error);
        break;
      default:
        break;
    }
  }

  function drawTimeline() {
    if (!state.spans.length) return;
    clear(timelineHost).appendChild(
      waterfall(state.spans, {
        caption:
          "One shared time axis. The planner runs once and the executor runs N times, so this is also the picture of where a configuration's latency budget actually goes.",
      }),
    );
  }

  if (streaming) {
    return {
      push,
      finish: () => { statusEl.textContent = "done"; drawTimeline(); },
      fail: (error, seen) => {
        statusEl.textContent = seen ? `interrupted after ${seen} events` : "failed to start";
        statusEl.classList.add("bad");
        drawTimeline();
        stage.appendChild(
          h("div", { class: "banner bad" },
            h("span", {}, "!"),
            h("div", {},
              h("strong", {}, "The run stopped early. "),
              seen
                ? "What you see below is everything that arrived before the connection dropped, not a finished run."
                : `Nothing arrived. ${error?.message ?? "The server closed the connection."}`),
          ),
        );
      },
    };
  }
  return replay(events, push, drawTimeline, delay);
}
