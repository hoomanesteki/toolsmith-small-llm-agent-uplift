/* The API client. Every endpoint reads a committed artifact, so the whole UI
   works on a fresh clone with no keys and no network beyond localhost. */

const cache = new Map();

async function get(path, { fresh = false } = {}) {
  if (!fresh && cache.has(path)) return cache.get(path);
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || detail.error || `${response.status} ${response.statusText}`);
  }
  const payload = await response.json();
  cache.set(path, payload);
  return payload;
}

export const api = {
  health: () => get("/api/health", { fresh: true }),
  config: () => get("/api/config"),
  matrix: () => get("/api/matrix"),
  optimize: () => get("/api/optimize"),
  screens: () => get("/api/screens"),
  tasks: (params = {}) => get(`/api/tasks?${new URLSearchParams(params)}`),
  task: (id) => get(`/api/task/${encodeURIComponent(id)}`),
  traces: () => get("/api/traces"),
  trace: (id) => get(`/api/trace/${encodeURIComponent(id)}`),
  gallery: (limit = 10) => get(`/api/gallery?limit=${limit}`),
  reviewQueue: (limit = 40) => get(`/api/review/queue?limit=${limit}`, { fresh: true }),
  calibration: () => get("/api/calibration", { fresh: true }),

  async label(payload) {
    const response = await fetch("/api/review/label", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(`${response.status}`);
    cache.delete("/api/review/queue");
    return response.json();
  },

  /* One run, streamed. Returns a stop function. */
  stream({ taskId, pipeline, provider = "simulated" }, onEvent, onDone, onError) {
    const query = new URLSearchParams({ task_id: taskId, pipeline, provider });
    const source = new EventSource(`/api/run?${query}`);
    const handle = (event) => {
      try {
        onEvent(JSON.parse(event.data));
      } catch {
        /* a malformed frame must not kill the stream */
      }
    };
    for (const type of [
      "run.started", "run.finished", "run.failed", "gate.input", "gate.tool_result",
      "gate.output", "plan.created", "turn.started", "turn.finished", "tool.searched",
      "tool.called", "tool.result", "review.verdict", "escalation.started",
      "model.call", "budget.update", "hitl.requested", "answer",
    ]) {
      source.addEventListener(type, handle);
    }
    source.addEventListener("run.finished", () => {
      source.close();
      onDone?.();
    });
    source.onerror = () => {
      source.close();
      onError?.(new Error("stream closed"));
    };
    return () => source.close();
  },
};

/* Replay a committed trace at the same pace as a live stream, so the Flow
   screen cannot tell the difference. It cannot, because there is none: the
   runtime's only output is events. */
export function replay(events, onEvent, onDone, delay = 55) {
  let index = 0;
  let stopped = false;
  const step = () => {
    if (stopped || index >= events.length) {
      if (!stopped) onDone?.();
      return;
    }
    onEvent(events[index++]);
    setTimeout(step, delay);
  };
  setTimeout(step, delay);
  return () => (stopped = true);
}
