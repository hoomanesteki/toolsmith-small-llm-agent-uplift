"""The control plane: one FastAPI process serving the API, the stream and the UI.

One process, one port, one URL, no CORS. That is a deployment decision as much
as an architectural one: the target is a free container that sleeps after two
days of inactivity, and a portfolio link that 502s when someone clicks it has
negative value.

WHAT IT SERVES
--------------
``/``            the four screens, as static files with no build step
``/api/*``       JSON, all of it derived from committed artifacts
``/api/run``     server-sent events, one frame per stage of a live run

WHY THERE IS NO BUILD STEP
--------------------------
The UI is hand-written ES modules and CSS. No bundler, no framework, no
node_modules. Three reasons, in order of how much they matter here:

1. The demo has to work on a fresh clone with no keys and no network. A build
   step is another thing that can be broken when someone is looking at it.
2. Every dependency is a claim this project would then have to stand behind,
   and the whole argument is about counting costs honestly.
3. The interactions here are a router, a chart layer and an event stream. That
   is genuinely less code without a framework than with one.

REPLAY
------
Every endpoint reads from ``eval/``. With no API keys the Flow screen replays a
committed event stream frame by frame and looks identical to a live run, because
it *is* the same stream: the runtime's only output is events, so a replayed run
and a live one are indistinguishable downstream.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from toolsmith import __version__
from toolsmith.config import REPO_ROOT, Registry, load_registry
from toolsmith.env import available_providers
from toolsmith.ledger import CostLedger, audit_csv
from toolsmith.providers import ProviderFactory
from toolsmith.runtime import (
    Event,
    EventBus,
    GateConfig,
    Pipeline,
    RuntimeDeps,
    graph_from_events,
)
from toolsmith.tasks.models import TIER_PURPOSE
from toolsmith.worlds import all_worlds, build_world, get_world

WEB_DIR = REPO_ROOT / "web"
EVAL_DIR = REPO_ROOT / "eval"

app = FastAPI(
    title="ToolSmith control plane",
    version=__version__,
    description="Which model should run which part of your agent? This proves the answer.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


# ------------------------------------------------------------------- state --


class Store:
    """Lazily-loaded artifacts, cached for the process lifetime.

    Everything here is a committed file. The server never computes a published
    number; it reads the one the harness wrote, which is the same discipline the
    report follows.
    """

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def _load(self, key: str, loader: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = loader()
        return self._cache[key]

    @property
    def registry(self) -> Registry:
        registry: Registry = self._load("registry", load_registry)
        return registry

    @property
    def matrix(self) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            path = EVAL_DIR / "results" / "matrix.json"
            if not path.exists():
                return {"rows": [], "comparisons": [], "manifest": {}}
            return json.loads(path.read_text(encoding="utf-8"))

        return self._load("matrix", load)

    @property
    def traces(self) -> dict[str, str]:
        from toolsmith.harness.store import read_traces

        return self._load("traces", read_traces)

    @property
    def tasks(self) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            from toolsmith.tasks.store import SAMPLE_PATH, TASKS_PATH, read_tasks

            path = TASKS_PATH if TASKS_PATH.exists() else SAMPLE_PATH
            if not path.exists():
                return {}
            return {t.task_id: t for t in read_tasks(path)}

        return self._load("tasks", load)

    @property
    def optimize(self) -> dict[str, Any]:
        from toolsmith.optimize import read_all

        return self._load("optimize", read_all)

    @property
    def judgments(self) -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]]:
            import gzip

            path = EVAL_DIR / "results" / "judgments.jsonl.gz"
            if not path.exists():
                return []
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]

        return self._load("judgments", load)

    @property
    def worlds(self) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            return {key: (spec, build_world(spec)) for key, spec in all_worlds().items()}

        return self._load("worlds", load)


store = Store()


def _budget_snapshot(registry: Registry) -> dict[str, Any]:
    """Cap, spend to date, and headroom. Read from the ledger, never asserted."""
    audit = audit_csv(policy=registry.budget)
    return {
        "cap_usd": audit.cap_usd,
        "live_usd": audit.live_usd,
        "remaining_usd": audit.remaining_usd,
        "within_cap": audit.within_cap,
        "by_provenance": audit.by_provenance,
        "rows": audit.rows,
    }


# --------------------------------------------------------------------- api --


@app.get("/api/health")
def health() -> dict[str, Any]:
    keys = available_providers()
    return {
        "ok": True,
        "version": __version__,
        "live_capable": sorted(k for k, v in keys.items() if v and k not in {"simulated", "mlx"}),
        "has_results": bool(store.matrix.get("rows")),
        "traces": len(store.traces),
        "note": (
            "No provider keys are present, so live runs are simulated through the "
            "identical code path. Every published table reproduces at $0."
            if not any(v for k, v in keys.items() if k not in {"simulated", "mlx"})
            else "At least one provider key is present; live runs are available."
        ),
    }


@app.get("/api/config")
def config() -> dict[str, Any]:
    registry = store.registry
    return {
        "models": {
            key: {
                "provider": spec.provider,
                "model_id": spec.model_id,
                "tier": spec.tier,
                "family": spec.family,
                "price_in_per_m": spec.price_in_per_m,
                "price_out_per_m": spec.price_out_per_m,
                "cache_read_discount": spec.cache_read_discount,
                "training_data_use": spec.training_data_use,
                "verified_on": str(spec.verified_on) if spec.verified_on else None,
                "notes": spec.notes,
            }
            for key, spec in sorted(registry.models.items())
        },
        "pipelines": {
            name: {
                "label": pipe.label,
                "description": pipe.description,
                "tags": pipe.tags,
                "planner": pipe.roles.planner,
                "executor": pipe.roles.executor,
                "reviewer": pipe.roles.reviewer,
                "escalate_to": pipe.roles.escalate_to,
                "tool_exposure": pipe.roles.tool_exposure,
            }
            for name, pipe in sorted(registry.pipelines.items())
        },
        "worlds": {
            key: {
                "title": spec.title,
                "tagline": spec.tagline,
                "role": spec.role,
                "tools": spec.tool_names,
                "privileged": spec.privileged_tools(),
                "entities": [
                    {"table": e.table, "label": e.label, "description": e.description}
                    for e in spec.entities
                ],
            }
            for key, spec in sorted(all_worlds().items())
        },
        "tiers": dict(TIER_PURPOSE),
        "budget": _budget_snapshot(registry),
    }


@app.get("/api/matrix")
def matrix() -> dict[str, Any]:
    payload = store.matrix
    if not payload.get("rows"):
        raise HTTPException(404, "No results yet. Run `toolsmith matrix run`.")
    return payload


@app.get("/api/optimize")
def optimize() -> dict[str, Any]:
    return store.optimize


@app.get("/api/tasks")
def tasks(
    world: str = Query("", description="World key."),
    tier: str = Query("", description="T1..T5."),
    split: str = Query("", description="train | val | test | test_hidden."),
    trap: bool = Query(False, description="Only trap tasks."),
    limit: int = Query(60, ge=1, le=500),
) -> dict[str, Any]:
    rows = []
    for task in store.tasks.values():
        if world and task.world != world:
            continue
        if tier and task.tier != tier:
            continue
        if split and task.split != split:
            continue
        if trap and not task.is_trap:
            continue
        rows.append(
            {
                "task_id": task.task_id,
                "world": task.world,
                "tier": task.tier,
                "template": task.template,
                "split": task.split,
                "prompt": task.prompt,
                "difficulty": task.difficulty,
                "is_trap": task.is_trap,
                "trap_kind": task.trap_kind,
                "expected_behaviour": task.expected_behaviour,
                "oracle_calls": len(task.program),
                "mutating": task.mutating,
            }
        )
    rows.sort(key=lambda r: r["task_id"])
    return {"total": len(rows), "tasks": rows[:limit]}


@app.get("/api/task/{task_id}")
def task_detail(task_id: str) -> dict[str, Any]:
    task = store.tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"no task {task_id}")
    return {
        **json.loads(task.model_dump_json()),
        "program_rendered": [
            {
                "verb": step.verb.value,
                "tool": get_world(task.world).resolve(step.verb),
                "arguments": step.arguments,
                "expect_ok": step.expect_ok,
            }
            for step in task.program
        ],
    }


@app.get("/api/traces")
def traces() -> dict[str, Any]:
    out = []
    for run_id, jsonl in store.traces.items():
        first = json.loads(jsonl.splitlines()[0]) if jsonl.strip() else {}
        data = first.get("data", {})
        out.append(
            {
                "run_id": run_id,
                "pipeline": data.get("pipeline", ""),
                "task_id": data.get("task", first.get("task_id", "")),
                "tier": data.get("tier", ""),
                "world": data.get("world", ""),
                "prompt": data.get("prompt", ""),
                "frames": len(jsonl.splitlines()),
            }
        )
    out.sort(key=lambda r: (r["pipeline"], r["task_id"]))
    return {"total": len(out), "traces": out}


@app.get("/api/trace/{run_id}")
def trace(run_id: str) -> dict[str, Any]:
    jsonl = store.traces.get(run_id)
    if jsonl is None:
        raise HTTPException(404, f"no trace {run_id}")
    bus = EventBus.from_jsonl(jsonl, run_id=run_id)
    events = [e.to_dict() for e in bus.history()]
    return {"run_id": run_id, "events": events, "graph": graph_from_events(bus.history())}


@app.get("/api/gallery")
def gallery(limit: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    """The failure gallery: the worst transcripts, with their diagnosis.

    Ranked by how much the failure should worry you rather than by how common it
    is. An unsanctioned privileged action is one occurrence and outranks a
    hundred wrong parameters.
    """
    from toolsmith.harness.store import read_results

    severity = {
        "unsanctioned_privileged_action": 100,
        "followed_injected_instruction": 90,
        "confabulated_on_policy_violation": 80,
        "confabulated_on_unanswerable": 70,
        "confabulated_on_ambiguous": 60,
        "confabulated_on_injection": 60,
        "missing_or_wrong_citation": 50,
        "over_refusal": 45,
        "no_usable_response": 40,
        "wrong_world_state": 35,
        "wrong_answer": 30,
        "wrong_tool_selection": 25,
        "wrong_parameters": 20,
        "inefficient_trajectory": 10,
        "runtime_error": 95,
    }
    try:
        scores = read_results()
    except FileNotFoundError:
        return {"total": 0, "failures": []}

    rows: list[dict[str, Any]] = []
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for score in scores:
        if score.passed or score.trial != 0:
            continue
        worst = max((severity.get(m, 5) for m in score.failure_modes), default=5)
        task = store.tasks.get(score.task_id)
        row = {
            "task_id": score.task_id,
            "pipeline": score.pipeline,
            "world": score.world,
            "tier": score.tier,
            "template": score.template,
            "severity": worst,
            "failure_modes": score.failure_modes,
            "prompt": task.prompt if task else "",
            "expected_behaviour": task.expected_behaviour if task else "",
            "oracle_answer": task.oracle_answer if task else "",
            "usd": score.usd,
            "calls_made": score.calls_made,
            "calls_oracle": score.calls_oracle,
            "state_ok": score.state_ok,
            "answer_ok": score.answer_ok,
            "behaviour_ok": score.behaviour_ok,
        }
        ranked.append((worst, score.task_id, row))

    # Severity first, then id, so the ordering is stable across runs.
    ranked.sort(key=lambda item: (-item[0], item[1]))
    rows = [row for _, _, row in ranked]
    return {"total": len(rows), "failures": rows[:limit]}


@app.get("/api/review/queue")
def review_queue(limit: int = Query(40, ge=1, le=200)) -> dict[str, Any]:
    """What is worth a person's time: contested judgments first.

    Labelling items the panel already agrees on measures nothing. The queue is
    ordered by judge disagreement, because that is where a human label changes
    the calibration.
    """
    rows = [
        {
            "task_id": j["task_id"],
            "pipeline": j["pipeline"],
            "scores": j["scores"],
            "per_judge": j["per_judge"],
            "disagreement": j["disagreement"],
            "contested": j["contested"],
            "dropped_seats": j["dropped_seats"],
            "prompt": (store.tasks[j["task_id"]].prompt if j["task_id"] in store.tasks else ""),
        }
        for j in store.judgments
    ]
    rows.sort(key=lambda r: (-float(r["disagreement"]), str(r["task_id"])))
    labelled = _read_labels()
    for row in rows:
        row["labelled"] = (row["task_id"], row["pipeline"]) in labelled
    return {
        "total": len(rows),
        "contested": sum(r["contested"] for r in rows),
        "labelled": len(labelled),
        "queue": rows[:limit],
    }


def _read_labels() -> set[tuple[str, str]]:
    path = EVAL_DIR / "labels" / "human_labels.jsonl"
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out.add((row["task_id"], row.get("pipeline", "")))
    return out


@app.post("/api/review/label")
def add_label(payload: dict[str, Any]) -> dict[str, Any]:
    """Append one human label. Feeds judge calibration on the next run."""
    required = {"task_id", "behaviour", "correct"}
    missing = required - set(payload)
    if missing:
        raise HTTPException(422, f"missing fields: {', '.join(sorted(missing))}")

    path = EVAL_DIR / "labels" / "human_labels.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "task_id": payload["task_id"],
        "pipeline": payload.get("pipeline", ""),
        "behaviour": payload["behaviour"],
        "correct": bool(payload["correct"]),
        "notes": payload.get("notes", ""),
        "labeller": payload.get("labeller", "owner"),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return {"ok": True, "written": str(path), "total_labels": len(_read_labels())}


def _answers_by_run() -> dict[tuple[str, str], str]:
    """The answer text a labeller actually read, keyed by (task, pipeline).

    Read back from the committed traces rather than from a stored verdict, so
    the classifier is re-run on the same words the person saw. Filenames are
    ``{pipeline}__{task_id}__{trial}.jsonl`` and the text is on the ``answer``
    event.
    """
    from toolsmith.harness.store import TRACES_DIR

    answers: dict[tuple[str, str], str] = {}
    for path in sorted(TRACES_DIR.glob("*__*__*.jsonl")):
        pipeline, task_id, _ = path.stem.split("__", 2)
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("type") == "answer":
                answers[(task_id, pipeline)] = str(event.get("data", {}).get("text", ""))
    return answers


@app.get("/api/calibration")
def calibration() -> dict[str, Any]:
    """Agreement between the behaviour classifier and the humans who labelled it.

    This used to pass an empty answers map, so every label was skipped for want
    of text to re-classify and the endpoint reported UNCALIBRATED no matter how
    many labels the review screen had collected. The whole point of the screen
    is that the number moves when a person does the work.
    """
    from toolsmith.harness.calibrate import calibrate_behaviour, read_labels

    return calibrate_behaviour(read_labels(), _answers_by_run()).to_dict()


# ------------------------------------------------------------------ stream --


@app.get("/api/run")
async def run(
    task_id: str = Query(..., description="Task to run."),
    pipeline: str = Query("cascade_default", description="Configuration."),
    provider: str = Query("simulated", description="simulated | auto | live."),
) -> EventSourceResponse:
    """Run one task and stream every stage as it happens.

    The stream is the runtime's only output, which is why the Flow screen does
    not care whether it is watching this endpoint or replaying a committed file.
    """
    task = store.tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"no task {task_id}")
    registry = store.registry
    if pipeline not in registry.pipelines:
        raise HTTPException(404, f"no pipeline {pipeline}")

    world, build = store.worlds[task.world]
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    bus = EventBus(run_id=f"live:{pipeline}:{task_id}")

    def forward(event: Event) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event.to_dict())

    bus.subscribe(forward)

    deps = RuntimeDeps(
        registry=registry,
        factory=ProviderFactory(registry, provider),  # type: ignore[arg-type]
        ledger=CostLedger(policy=registry.budget, run_id="ui"),
        gate_config=GateConfig(),
    )

    def execute() -> None:
        try:
            Pipeline(registry.pipeline(pipeline), deps, world, build, bus).run(task)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    async def stream_live() -> AsyncIterator[dict[str, str]]:
        task_handle = loop.run_in_executor(None, execute)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                # A visible pace: the simulator finishes in milliseconds and the
                # point of this screen is to be watched.
                await asyncio.sleep(0.05)
                yield {"event": event["type"], "data": json.dumps(event)}
        finally:
            await task_handle

    return EventSourceResponse(stream_live())


# ------------------------------------------------------------------- pages --


@app.get("/api/screens")
def screens() -> dict[str, Any]:
    """What the UI shows, and what each screen is for."""
    return {
        "chat": "Ask a question and watch the pipeline answer it, with the tool "
        "calls, the gate verdicts and the running cost visible as it happens.",
        "flow": "The agent graph for one run. Nodes light up as they execute; the "
        "picture is derived from the event stream, so it cannot drift from the behaviour.",
        "lab": "The comparison surface. Cost against quality with the Pareto "
        "frontier computed, per-tier breakdowns, spend by role, and where each "
        "configuration loses.",
        "review": "The human-in-the-loop queue. Contested judgments first, because "
        "labelling what the panel already agrees on measures nothing.",
    }


if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/{screen}")
    def screen_route(screen: str) -> FileResponse:
        """Client-side routing: every path returns the shell."""
        if screen.startswith("api"):
            raise HTTPException(404)
        return FileResponse(WEB_DIR / "index.html")


@app.exception_handler(404)
async def not_found(request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse({"error": "not found", "path": str(request.url.path)}, status_code=404)


def main() -> None:  # pragma: no cover - entry point
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=7860)


if __name__ == "__main__":  # pragma: no cover
    main()
