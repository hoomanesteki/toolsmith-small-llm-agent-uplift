/* The Chat screen: pick a task, pick a configuration, watch it answer.
 *
 * The point of this screen is that the model selector changes the whole
 * pipeline live. Swapping the executor from an open model to a frontier one is
 * a dropdown here because it is a YAML edit in the repository, which is the
 * property the entire project is built to demonstrate.
 */

import { api } from "../api.js";
import { blurbs } from "../app.js";
import { card, chip, clear, empty, fmt, h, orientation, ticker } from "../ui.js";

export async function chat(host) {
  clear(host);
  host.appendChild(h("h1", {}, "Ask, and watch it work"));
  host.appendChild(orientation(blurbs.chat));
  host.appendChild(
    h("p", { class: "lede", html:
      "Pick any task from the generated suite and any configuration from the matrix. The selector changes the " +
      "<strong>whole pipeline</strong>: planner, executor, reviewer and escalation target, live. It is a dropdown " +
      "here because it is a YAML edit in the repository." }),
  );

  const [config, health] = await Promise.all([api.config(), api.health()]);
  const worlds = Object.keys(config.worlds);

  const worldPick = h("select", { "aria-label": "World" }, worlds.map((w) => h("option", { value: w }, config.worlds[w].title)));
  const tierPick = h("select", { "aria-label": "Tier" }, [["", "Any tier"], ...Object.keys(config.tiers).map((t) => [t, t])].map(([v, l]) => h("option", { value: v }, l)));
  const taskPick = h("select", { "aria-label": "Task", style: { maxWidth: "34rem" } });
  const pipePick = h("select", { "aria-label": "Configuration" },
    Object.entries(config.pipelines).map(([name, p]) => h("option", { value: name }, p.label)));
  /* Honour a configuration handed over from the Lab, so "try this one" arrives
     with that one selected rather than the default. */
  const asked = new URLSearchParams(window.location.search).get("pipeline");
  pipePick.value = config.pipelines?.[asked] ? asked : "cascade_default";
  const runButton = h("button", { class: "btn" }, "Run");

  host.appendChild(h("div", { class: "row" },
    h("label", { class: "field" }, "World", worldPick),
    h("label", { class: "field" }, "Tier", tierPick),
    h("label", { class: "field" }, "Task", taskPick),
  ));
  host.appendChild(h("div", { class: "row" },
    h("label", { class: "field" }, "Configuration", pipePick),
    runButton,
    health.live_capable.length
      ? chip(`live: ${health.live_capable.join(", ")}`, "good")
      : chip("no keys: simulated through the same code path", "warn"),
  ));

  const detail = h("div");
  const output = h("div");
  host.appendChild(detail);
  host.appendChild(output);

  let tasks = [];

  async function loadTasks() {
    const payload = await api.tasks({ world: worldPick.value, tier: tierPick.value, split: "test", limit: 120 });
    tasks = payload.tasks;
    clear(taskPick);
    for (const t of tasks) {
      taskPick.appendChild(h("option", { value: t.task_id }, `${t.tier} · ${t.prompt.slice(0, 74)}${t.prompt.length > 74 ? "..." : ""}`));
    }
    showTask();
  }

  function showTask() {
    clear(detail);
    const task = tasks.find((t) => t.task_id === taskPick.value);
    if (!task) return;
    detail.appendChild(card(
      "The question",
      null,
      h("p", { style: { fontSize: "var(--size-title)", letterSpacing: "var(--track-title)", margin: "0 0 0.7rem" } }, `"${task.prompt}"`),
      h("div", { class: "row", style: { marginBottom: 0 } },
        chip(task.task_id),
        chip(`${task.oracle_calls} oracle call${task.oracle_calls === 1 ? "" : "s"}`),
        task.is_trap ? chip(`trap: ${task.trap_kind}`, "warn") : null,
        chip(`expects: ${task.expected_behaviour}`, task.is_trap ? "warn" : ""),
        task.mutating ? chip("changes the world", "warn") : null,
      ),
    ));
  }

  worldPick.onchange = loadTasks;
  tierPick.onchange = loadTasks;
  taskPick.onchange = showTask;

  let stop = null;
  runButton.onclick = () => {
    stop?.();
    const taskId = taskPick.value;
    if (!taskId) return;
    runButton.disabled = true;
    runButton.textContent = "Running";

    clear(output);
    const costEl = h("span", { class: "value ticker", dataset: { value: "0" } }, "$0.00000");
    const stream = h("div");
    output.appendChild(h("div", { class: "grid", style: { marginBottom: "1rem" } },
      card(null, null, h("div", { class: "stat" }, h("span", { class: "label" }, "Cost"), costEl, h("span", { class: "sub" }, "this run, billed per call"))),
    ));
    output.appendChild(card("What it did", null, stream));

    let usd = 0;
    stop = api.stream(
      { taskId, pipeline: pipePick.value, provider: health.live_capable.length ? "auto" : "simulated" },
      (event) => {
        const d = event.data ?? {};
        if (event.type === "model.call") { usd += Number(d.usd ?? 0); ticker(costEl, usd, fmt.usd); }
        const line = describe(event.type, d);
        if (line) stream.appendChild(line);
        if (event.type === "answer") {
          stream.appendChild(h("div", { class: "answer" },
            h("div", { class: "row", style: { marginBottom: "0.4rem" } }, chip(d.behaviour ?? "answer", "accent")),
            h("div", {}, d.text)));
        }
      },
      () => { runButton.disabled = false; runButton.textContent = "Run"; },
      () => { runButton.disabled = false; runButton.textContent = "Run"; },
    );
  };

  function describe(type, d) {
    switch (type) {
      case "gate.input":
        return h("div", { class: "turn", dataset: { kind: "gate" } },
          h("div", { class: "head" }, h("b", {}, "Input gate"), chip(d.action, d.action === "allow" ? "good" : "warn"), h("span", {}, d.reason || "clean")));
      case "plan.created":
        return h("div", { class: "turn", dataset: { kind: "model" } },
          h("div", { class: "head" }, h("b", {}, "Planner"), chip(d.model ?? "")), h("pre", {}, d.plan));
      case "tool.searched":
        return h("div", { class: "turn", dataset: { kind: "tool" } },
          h("div", { class: "head" }, h("b", {}, "search_tools"), h("span", {}, `"${d.query}"`), chip(`${(d.found ?? []).length} schemas`)));
      case "tool.called":
        return h("div", { class: "turn", dataset: { kind: "tool" } },
          h("div", { class: "head" }, h("b", {}, d.tool), d.privileged ? chip("PRIVILEGED", "warn") : null),
          h("pre", {}, JSON.stringify(d.arguments, null, 2)));
      case "tool.result":
        return h("div", { class: "turn", dataset: { kind: "tool" } },
          h("div", { class: "head" }, h("b", {}, `${d.tool} →`), chip(d.ok ? "ok" : d.error_code || "failed", d.ok ? "good" : "bad")),
          h("pre", {}, d.preview ?? ""));
      case "gate.tool_result":
        return (d.rules ?? []).length
          ? h("div", { class: "turn", dataset: { kind: "gate" } },
              h("div", { class: "head" }, h("b", {}, "Tool-result gate"), chip("injection detected", "bad")), h("pre", {}, d.reason))
          : null;
      case "review.verdict":
        return h("div", { class: "turn", dataset: { kind: "model" } },
          h("div", { class: "head" }, h("b", {}, "Reviewer"), chip(d.verdict, d.verdict === "accept" ? "good" : "warn"),
            chip(d.cross_family ? "cross-family" : "same family", d.cross_family ? "" : "warn")));
      case "escalation.started":
        return h("div", { class: "turn", dataset: { kind: "model" } },
          h("div", { class: "head" }, h("b", {}, "Escalation"), chip(d.trigger, "warn"), h("span", {}, d.reason)));
      case "gate.output":
        return h("div", { class: "turn", dataset: { kind: "gate" } },
          h("div", { class: "head" }, h("b", {}, "Output gate"), chip(d.action, d.action === "allow" ? "good" : "warn"), h("span", {}, d.reason || "grounded")));
      default:
        return null;
    }
  }

  await loadTasks();
  if (!tasks.length) {
    output.appendChild(empty("No tasks yet. Run ", h("code", {}, "uv run toolsmith tasks build"), "."));
  }

  /* Same reason as Flow: leaving mid-run used to keep an EventSource pushing
     events into a DOM that is no longer on screen, for as long as the run
     lasted. */
  return () => stop?.();
}
