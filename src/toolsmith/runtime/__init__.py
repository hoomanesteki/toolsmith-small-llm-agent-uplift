"""GATE -> PLAN -> EXECUTE -> REVIEW -> GATE.

Five stages, each a swappable component with a declared model, each emitting a
typed event. The orchestrator is :class:`Pipeline`; everything else in this
package is one stage of it.

The design commitments, stated once:

* the executor is the only role that runs N times, so its errors compound as
  q**N and its input dominates the bill
* tool schemas are retrieved, not resident, because per-turn input is the
  largest line item in the system
* the prefix never mutates, because the cache discount has no partial credit
* privileged actions are authorised server-side, by the world, after the model
  has asked
* tool results are marked as data before they re-enter context
"""

from toolsmith.runtime.behaviour import (
    answer_keys_hit,
    behaviour_matches,
    classify_behaviour,
    contains_answer_key,
    normalise_answer,
)
from toolsmith.runtime.context import ContextBuilder
from toolsmith.runtime.events import Event, EventBus, EventType, graph_from_events
from toolsmith.runtime.gates import GateConfig, GateVerdict, InputGate, OutputGate, ToolResultGate
from toolsmith.runtime.pipeline import Pipeline, RuntimeDeps
from toolsmith.runtime.prompts import BUNDLES, PromptBundle, get_bundle, register
from toolsmith.runtime.record import ModelCall, RunRecord, ToolInvocation
from toolsmith.runtime.router import ConfidenceSignals, Router, RoutingDecision
from toolsmith.runtime.skills import Skill, load_skills, skills_for
from toolsmith.runtime.toolsearch import ToolHit, ToolIndex

__all__ = [
    "BUNDLES",
    "ConfidenceSignals",
    "ContextBuilder",
    "Event",
    "EventBus",
    "EventType",
    "GateConfig",
    "GateVerdict",
    "InputGate",
    "ModelCall",
    "OutputGate",
    "Pipeline",
    "PromptBundle",
    "Router",
    "RoutingDecision",
    "RunRecord",
    "RuntimeDeps",
    "Skill",
    "ToolHit",
    "ToolIndex",
    "ToolInvocation",
    "ToolResultGate",
    "answer_keys_hit",
    "behaviour_matches",
    "classify_behaviour",
    "contains_answer_key",
    "get_bundle",
    "graph_from_events",
    "load_skills",
    "normalise_answer",
    "register",
    "skills_for",
]
