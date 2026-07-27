"""The event stream: one typed envelope for every stage of every run.

The runtime emits events rather than logging. Three consumers read the same
stream and none of them needs a special path:

* the **UI** subscribes over server-sent events and animates the agent graph
* the **harness** collects the stream into a trajectory record
* the **audit log** writes it to disk, redacted, as the compliance artifact

Because the stream is the only output, a replayed run and a live run are
indistinguishable downstream. That is what makes the committed trace fixtures
work with zero API keys: they are event streams, not screenshots.

The envelope shape follows the AG-UI convention (``type``, ``timestamp``,
``data``) so a third-party client can consume it without a bespoke adapter.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import itertools
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """Every stage boundary is observable. Nothing happens off the record."""

    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"
    RUN_FAILED = "run.failed"

    GATE_INPUT = "gate.input"
    GATE_TOOL_RESULT = "gate.tool_result"
    GATE_OUTPUT = "gate.output"

    PLAN_CREATED = "plan.created"

    TURN_STARTED = "turn.started"
    TOOL_SEARCHED = "tool.searched"
    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"
    TURN_FINISHED = "turn.finished"

    CONTEXT_COMPACTED = "context.compacted"
    REVIEW_VERDICT = "review.verdict"
    ESCALATED = "escalation.started"

    MODEL_CALL = "model.call"
    BUDGET_UPDATE = "budget.update"
    HITL_REQUESTED = "hitl.requested"
    HITL_RESOLVED = "hitl.resolved"

    ANSWER = "answer"


_counter = itertools.count()


@dataclass(slots=True)
class Event:
    type: EventType
    run_id: str
    task_id: str = ""
    stage: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = field(default_factory=lambda: next(_counter))
    timestamp: str = field(
        default_factory=lambda: dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["type"] = str(self.type)
        return payload

    def to_sse(self) -> str:
        """One server-sent event frame."""
        return f"event: {self.type}\ndata: {json.dumps(self.to_dict(), default=str)}\n\n"


Subscriber = Callable[[Event], None]


class EventBus:
    """Fan-out with a retained history.

    History is retained because a UI that connects mid-run should see the whole
    graph, not the tail of it. Bounded, because a runaway loop must not become a
    memory leak.
    """

    def __init__(self, run_id: str, task_id: str = "", max_history: int = 4_000) -> None:
        self.run_id = run_id
        self.task_id = task_id
        self.max_history = max_history
        self._events: list[Event] = []
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def emit(self, event_type: EventType, stage: str = "", **data: Any) -> Event:
        event = Event(
            type=event_type,
            run_id=self.run_id,
            task_id=self.task_id,
            stage=stage,
            data=data,
        )
        with self._lock:
            self._events.append(event)
            if len(self._events) > self.max_history:
                del self._events[: len(self._events) - self.max_history]
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            # A broken listener must not take the run down with it.
            with contextlib.suppress(Exception):
                subscriber(event)
        return event

    # -- reading ------------------------------------------------------------

    def history(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def of_type(self, *types: EventType) -> list[Event]:
        wanted = set(types)
        return [e for e in self.history() for _ in (0,) if e.type in wanted]

    def replay(self) -> Iterator[Event]:
        yield from self.history()

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e.to_dict(), default=str) for e in self.history())

    @classmethod
    def from_jsonl(cls, text: str, run_id: str = "replay") -> EventBus:
        """Rebuild a bus from a committed fixture. The zero-key demo path."""
        bus = cls(run_id=run_id)
        for line in text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            bus._events.append(
                Event(
                    type=EventType(row["type"]),
                    run_id=row.get("run_id", run_id),
                    task_id=row.get("task_id", ""),
                    stage=row.get("stage", ""),
                    data=row.get("data", {}),
                    seq=row.get("seq", 0),
                    timestamp=row.get("timestamp", ""),
                )
            )
        return bus


def graph_from_events(events: list[Event]) -> dict[str, Any]:
    """Collapse an event stream into the node and edge lists the UI draws.

    The agent graph is derived from what happened rather than declared up front,
    so a run that escalated shows an escalation node and a run that did not,
    does not. The picture cannot drift from the behaviour.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    seen: set[str] = set()
    previous: str | None = None

    def add(node_id: str, kind: str, label: str, **attrs: Any) -> None:
        nonlocal previous
        if node_id not in seen:
            seen.add(node_id)
            nodes.append({"id": node_id, "kind": kind, "label": label, **attrs})
            if previous is not None:
                edges.append({"source": previous, "target": node_id})
        previous = node_id

    for event in events:
        match event.type:
            case EventType.GATE_INPUT:
                add("gate_in", "gate", "Input gate", verdict=event.data.get("action"))
            case EventType.PLAN_CREATED:
                add("planner", "role", "Planner", model=event.data.get("model"))
            case EventType.TURN_STARTED:
                add(
                    f"turn_{event.data.get('turn', 0)}",
                    "turn",
                    f"Turn {event.data.get('turn', 0)}",
                    model=event.data.get("model"),
                )
            case EventType.TOOL_CALLED:
                turn = event.data.get("turn", 0)
                add(
                    f"tool_{turn}_{event.data.get('tool')}",
                    "tool",
                    str(event.data.get("tool")),
                    privileged=event.data.get("privileged", False),
                )
            case EventType.REVIEW_VERDICT:
                add("reviewer", "role", "Reviewer", verdict=event.data.get("verdict"))
            case EventType.ESCALATED:
                add("escalation", "role", "Escalation", model=event.data.get("model"))
            case EventType.GATE_OUTPUT:
                add("gate_out", "gate", "Output gate", verdict=event.data.get("action"))
            case EventType.ANSWER:
                add("answer", "answer", "Response")
            case _:
                continue
    return {"nodes": nodes, "edges": edges}
