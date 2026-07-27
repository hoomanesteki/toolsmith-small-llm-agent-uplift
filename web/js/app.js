/* Router and shell. Four screens, real URLs, no framework.
 *
 * The screens used to be four islands. You could read that the naive role split
 * loses on both axes and have no way to open a run that shows it, which made
 * the interface a set of dashboards rather than an argument you can follow.
 * `go()` now carries a query string, so any screen can hand off to another with
 * the thing it was looking at: the Lab sends a configuration to Chat, the
 * failure gallery sends a run to Flow, and Flow opens on that run.
 */

import { api } from "./api.js";
import { chat } from "./screens/chat.js";
import { flow } from "./screens/flow.js";
import { lab } from "./screens/lab.js";
import { review } from "./screens/review.js";
import { clear, h } from "./ui.js";

const SCREENS = {
  lab: { label: "Lab", render: lab, title: "Cost versus quality" },
  flow: { label: "Flow", render: flow, title: "One run, watched" },
  chat: { label: "Chat", render: chat, title: "Ask, and watch it work" },
  review: { label: "Review", render: review, title: "What needs a person" },
};

const DEFAULT = "lab";
const root = document.getElementById("screen");
const tabs = document.getElementById("tabs");

/* Set once from /api/screens and read by every screen for its own subtitle, so
   the one-line explanation of what you are looking at lives in one place and is
   served by the API rather than duplicated into four files. */
export const blurbs = {};

/* Whatever the previous screen needs to undo: an open EventSource, a timer, a
   resize listener. Screens may return a function; those that do not are assumed
   to leave nothing running. */
let teardown = null;

/* Bumped on every navigation. A screen's render is asynchronous, so clicking
   Lab and then Review while the matrix request is still outstanding used to let
   both finish: Review would clear the root and draw, then Lab would append its
   own content underneath, and the URL agreed with neither. Each render checks
   that it is still the current one before it touches the DOM. */
let generation = 0;

function current() {
  const name = window.location.pathname.replace(/^\//, "") || DEFAULT;
  return SCREENS[name] ? name : DEFAULT;
}

export function go(name, { push = true, params = {} } = {}) {
  const query = new URLSearchParams(params).toString();
  const target = `/${name}${query ? `?${query}` : ""}`;
  if (push && window.location.pathname + window.location.search !== target) {
    window.history.pushState({}, "", target);
  }
  return render(name);
}

async function render(name) {
  const mine = ++generation;
  for (const link of tabs.querySelectorAll(".tab")) {
    const selected = link.dataset.screen === name;
    link.setAttribute("aria-current", selected ? "page" : "false");
  }
  document.title = `${SCREENS[name].title} · ToolSmith`;

  /* Before anything is torn down or drawn: a screen that left a stream open
     would otherwise keep pushing events into a detached DOM for as long as the
     run lasted. */
  try {
    teardown?.();
  } catch (error) {
    console.error("teardown failed", error);
  }
  teardown = null;

  clear(root);
  root.appendChild(
    h("div", { class: "empty" }, h("span", { class: "spinner" }), " loading"),
  );
  try {
    const stage = document.createElement("div");
    const undo = await SCREENS[name].render(stage);
    if (mine !== generation) {
      undo?.();
      return;
    }
    clear(root).appendChild(stage);
    teardown = undo ?? null;
  } catch (error) {
    if (mine !== generation) return;
    clear(root).appendChild(
      h(
        "div",
        { class: "empty" },
        h("p", {}, `That screen failed to load: ${error.message}`),
        h(
          "button",
          { class: "btn", onclick: () => render(name) },
          "Try again",
        ),
      ),
    );
    console.error(error);
  }
}

/* Anchors, not buttons. These change the URL, so a link is both the honest
   element and the one that already supports middle-click, copy-link and
   keyboard activation without any of it being reimplemented. */
for (const [name, screen] of Object.entries(SCREENS)) {
  tabs.appendChild(
    h(
      "a",
      {
        class: "tab",
        href: `/${name}`,
        dataset: { screen: name },
        "aria-current": "false",
        onclick: (event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey) return;
          event.preventDefault();
          go(name);
        },
      },
      screen.label,
    ),
  );
}

window.addEventListener("popstate", () => render(current()));

/* Theme: follow the system by default, remember an explicit choice.

   This used to re-render the whole screen, which threw away a running stream
   and any selection the visitor had made, to recolour some SVG. The charts read
   their colours from custom properties at draw time, so they are the only thing
   that needs to hear about it. */
const themeButton = document.getElementById("theme");
const stored = localStorage.getItem("toolsmith-theme");
if (stored) document.documentElement.dataset.theme = stored;
themeButton.onclick = () => {
  const dark =
    document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  const next = dark ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  themeButton.setAttribute("aria-label", `Switch to ${dark ? "dark" : "light"} theme`);
  localStorage.setItem("toolsmith-theme", next);
  window.dispatchEvent(new CustomEvent("themechange", { detail: { theme: next } }));
};

/* The health chip is the first honest thing a visitor sees, so it must never be
   the thing still saying "checking" a minute later. */
api
  .health()
  .then((health) => {
    const badge = document.getElementById("provenance");
    const live = health.live_capable.length;
    badge.className = `chip ${live ? "good" : "warn"}`;
    badge.textContent = live ? `live: ${health.live_capable.join(", ")}` : "simulated · $0 spent";
    badge.title = health.note;
  })
  .catch(() => {
    const badge = document.getElementById("provenance");
    badge.className = "chip bad";
    badge.textContent = "control plane unreachable";
    badge.title = "The API did not answer /api/health. Is `make serve` running?";
  });

api
  .screens()
  .then((copy) => {
    Object.assign(blurbs, copy);
    for (const link of tabs.querySelectorAll(".tab")) {
      if (copy[link.dataset.screen]) link.title = copy[link.dataset.screen];
    }
    render(current());
  })
  .catch(() => render(current()));
