/* Tiny DOM helpers. No framework: the interactions here are a router, a chart
   layer and an event stream, which is genuinely less code without one. */

export const svgNS = "http://www.w3.org/2000/svg";

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  apply(el, props);
  append(el, children);
  return el;
}

export function s(tag, props = {}, ...children) {
  const el = document.createElementNS(svgNS, tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else {
      el.setAttribute(key, String(value));
    }
  }
  append(el, children);
  return el;
}

function apply(el, props) {
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "html") el.innerHTML = value;
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key in el && key !== "list") {
      el[key] = value;
    } else {
      el.setAttribute(key, String(value));
    }
  }
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

/* ------------------------------------------------------------ formatting -- */

export const fmt = {
  pct: (v, digits = 1) => `${(v * 100).toFixed(digits)}%`,
  usd: (v) => (v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(5)}`),
  num: (v, digits = 0) => Number(v).toLocaleString(undefined, { maximumFractionDigits: digits }),
  fixed: (v, digits = 3) => Number(v).toFixed(digits),
  ms: (v) => (v < 1 ? `${(v * 1000).toFixed(0)}ms` : `${v.toFixed(2)}s`),
  compact: (v) => Number(v).toLocaleString(undefined, { notation: "compact" }),
};

/* One shared tooltip. Positioned on the viewport so it never clips a card. */

let tip;
export function tooltip() {
  if (!tip) {
    tip = h("div", { class: "tooltip", role: "status" });
    document.body.appendChild(tip);
  }
  return {
    show(event, title, rows) {
      tip.innerHTML = "";
      tip.appendChild(h("b", {}, title));
      if (rows?.length) {
        const dl = h("dl");
        for (const [key, value] of rows) {
          dl.appendChild(h("dt", {}, key));
          dl.appendChild(h("dd", {}, value));
        }
        tip.appendChild(dl);
      }
      tip.dataset.show = "true";
      this.move(event);
    },
    move(event) {
      const pad = 14;
      const box = tip.getBoundingClientRect();
      let x = event.clientX + pad;
      let y = event.clientY + pad;
      if (x + box.width > window.innerWidth - 8) x = event.clientX - box.width - pad;
      if (y + box.height > window.innerHeight - 8) y = event.clientY - box.height - pad;
      tip.style.left = `${Math.max(8, x)}px`;
      tip.style.top = `${Math.max(8, y)}px`;
    },
    hide() {
      tip.dataset.show = "false";
    },
  };
}

/* A number that animates its value without moving the layout. */

export function ticker(el, to, format = fmt.usd, ms = 420) {
  const from = Number(el.dataset.value || 0);
  if (from === to) return;
  el.dataset.value = String(to);
  el.dataset.bumped = "true";
  const started = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - started) / ms);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = format(from + (to - from) * eased);
    if (t < 1) requestAnimationFrame(step);
    else setTimeout(() => (el.dataset.bumped = "false"), 200);
  };
  requestAnimationFrame(step);
}

export function chip(text, kind = "") {
  return h("span", { class: `chip ${kind}`.trim() }, kind ? h("i", { class: "dot" }) : null, text);
}

export function stat(label, value, sub, hero = false) {
  return h(
    "div",
    { class: "stat" },
    h("span", { class: "label" }, label),
    h("span", { class: `value${hero ? " hero" : ""}` }, value),
    sub ? h("span", { class: "sub" }, sub) : null,
  );
}

export function card(title, hint, ...body) {
  const header = h(
    "header",
    {},
    h("h3", {}, title),
    hint ? h("span", { class: "hint" }, hint) : null,
  );
  return h("section", { class: "card" }, title ? header : null, ...body);
}

export function empty(message) {
  return h("div", { class: "empty" }, message);
}

export function escape(text) {
  return String(text ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}
