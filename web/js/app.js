/* Router and shell. Four screens, hash-free paths, no framework. */

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

function current() {
  const name = window.location.pathname.replace(/^\//, "") || DEFAULT;
  return SCREENS[name] ? name : DEFAULT;
}

async function go(name, push = true) {
  if (push && window.location.pathname !== `/${name}`) {
    window.history.pushState({}, "", `/${name}`);
  }
  for (const button of tabs.querySelectorAll(".tab")) {
    button.setAttribute("aria-selected", String(button.dataset.screen === name));
  }
  document.title = `${SCREENS[name].title} · ToolSmith`;
  clear(root);
  root.appendChild(h("div", { class: "empty" }, h("span", { class: "spinner" }), " loading"));
  try {
    await SCREENS[name].render(root);
  } catch (error) {
    clear(root).appendChild(
      h("div", { class: "empty" }, `That screen failed to load: ${error.message}`),
    );
    console.error(error);
  }
}

for (const [name, screen] of Object.entries(SCREENS)) {
  tabs.appendChild(
    h(
      "button",
      { class: "tab", dataset: { screen: name }, role: "tab", "aria-selected": "false", onclick: () => go(name) },
      screen.label,
    ),
  );
}

window.addEventListener("popstate", () => go(current(), false));

/* Theme: follow the system by default, remember an explicit choice. */
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
  localStorage.setItem("toolsmith-theme", next);
  go(current(), false);
};

/* The health chip is the first honest thing a visitor sees. */
api.health().then((health) => {
  const badge = document.getElementById("provenance");
  const live = health.live_capable.length;
  badge.className = `chip ${live ? "good" : "warn"}`;
  badge.textContent = live ? `live: ${health.live_capable.join(", ")}` : "simulated · $0 spent";
  badge.title = health.note;
});

go(current(), false);
