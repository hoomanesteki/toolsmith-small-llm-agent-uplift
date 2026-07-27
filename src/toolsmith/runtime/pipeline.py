"""GATE -> PLAN -> EXECUTE -> REVIEW -> GATE.

The orchestrator. Every stage is a swappable component with a declared model,
every stage emits a typed event, and every stage's cost lands in the ledger
under its own role, which is what makes the "73% of spend is the executor"
finding a measurement rather than an assertion.

Reading order, if you are here to understand the system:

* :meth:`Pipeline.run` is the whole flow in fifty lines
* :meth:`Pipeline._execute` is the loop that does the work and the arithmetic
* :meth:`Pipeline._call` is where every token is counted

Two implementation details that matter more than they look.

**The simulator's turn index counts world tool calls, not loop iterations.** A
turn spent on ``search_tools`` is real work but is not a step in the oracle
program, so it must not advance the oracle pointer. Conflating them would make
tool-search look like an accuracy regression when it is a context optimisation.

**Every privileged call goes through the world's policy function.** The pipeline
never checks eligibility itself. It cannot: that is domain logic, and putting it
here is how it ends up in a prompt.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from toolsmith.config import ModelSpec, PipelineSpec, Registry
from toolsmith.ledger import CostLedger, LedgerEntry
from toolsmith.providers import (
    LLMRequest,
    LLMResponse,
    Message,
    ProviderFactory,
    SimContext,
    ToolCall,
    estimate_tokens,
)
from toolsmith.runtime.behaviour import classify_behaviour
from toolsmith.runtime.context import ContextBuilder
from toolsmith.runtime.events import EventBus, EventType
from toolsmith.runtime.gates import GateConfig, InputGate, OutputGate, ToolResultGate
from toolsmith.runtime.prompts import get_bundle
from toolsmith.runtime.record import ModelCall, RunRecord, ToolInvocation
from toolsmith.runtime.router import ConfidenceSignals, Router
from toolsmith.runtime.skills import skills_for
from toolsmith.runtime.toolsearch import ToolIndex
from toolsmith.tasks.models import Task
from toolsmith.tasks.oracle import to_injections
from toolsmith.worlds import Sandbox, WorldSpec
from toolsmith.worlds.base import ToolSpec
from toolsmith.worlds.sandbox import WorldBuild

#: Meta tools handled by the runtime rather than by the world.
META_TOOLS = {"search_tools", "read_skill"}

#: Modelled per-call tool latency, in milliseconds. Stands in for the network
#: and database time a real service would spend, which the in-process sandbox
#: does not.
TOOL_LATENCY_MS = 25.0

#: Exception types that mean the code is wrong, not the run.
#:
#: A run that fails is data: a provider timed out, a budget was exhausted, a
#: model produced something unusable. Those are recorded and the matrix goes on.
#: A TypeError is not data, it is a bug, and swallowing it into `record.error`
#: turns a loud failure into 8,000 quietly zeroed rows. This distinction was
#: added after exactly that happened.
BUG_TYPES: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    NameError,
    ImportError,
    IndentationError,
    SyntaxError,
)


@dataclass(slots=True)
class RuntimeDeps:
    """Everything the pipeline needs, injected rather than imported.

    Keeps the orchestrator testable and makes the dependency surface visible:
    a registry, a provider factory, a ledger, and the built worlds.
    """

    registry: Registry
    factory: ProviderFactory
    ledger: CostLedger
    gate_config: GateConfig


class Pipeline:
    """One configuration, executable against any task in any world."""

    def __init__(
        self,
        spec: PipelineSpec,
        deps: RuntimeDeps,
        world: WorldSpec,
        build: WorldBuild,
        bus: EventBus | None = None,
    ) -> None:
        self.spec = spec
        self.roles = spec.roles
        self.deps = deps
        self.world = world
        self.build = build
        self.bus = bus or EventBus(run_id=f"run_{uuid.uuid4().hex[:8]}")
        self.tool_index = ToolIndex(world)
        self.skills = skills_for(world.key)
        self.prompts = get_bundle(self.roles.prompt_variant)

        self.input_gate = InputGate(deps.gate_config)
        self.tool_gate = ToolResultGate(deps.gate_config)
        self.output_gate = OutputGate(deps.gate_config)
        self._prefix_state: dict[str, str] = {}
        self.router = Router(
            triggers=list(self.roles.escalate_on),
            threshold=self.roles.confidence_threshold,
            escalate_to=self.roles.escalate_to,
        )

    # ============================================================== running ==

    def run(self, task: Task, trial: int = 0) -> RunRecord:
        started = time.perf_counter()
        record = RunRecord(
            run_id=self.bus.run_id,
            task_id=task.task_id,
            world=self.world.key,
            pipeline=self.spec.name,
            trial=trial,
            provider_mode=self.deps.factory.mode,
            models_used={
                "planner": self.roles.planner,
                "executor": self.roles.executor,
                "reviewer": self.roles.reviewer,
                "escalation": self.roles.escalate_to or "",
            },
        )
        self.bus.task_id = task.task_id
        self.bus.emit(
            EventType.RUN_STARTED,
            "run",
            pipeline=self.spec.name,
            task=task.task_id,
            tier=task.tier,
            world=self.world.key,
            prompt=task.prompt,
            models=record.models_used,
        )

        try:
            self._run_inner(task, record, trial)
        except BUG_TYPES:
            # Programming errors are not results. Let them out.
            raise
        except Exception as exc:  # a failing run is data, not a crash
            record.error = f"{type(exc).__name__}: {exc}"
            record.behaviour = "error"
            self.bus.emit(EventType.RUN_FAILED, "run", error=record.error)

        record.wall_clock_s = time.perf_counter() - started
        # Published latency is modelled provider time, not measured wall-clock.
        # See RunRecord.latency_s.
        record.latency_s = sum(c.latency_s for c in record.model_calls) + sum(
            c.latency_ms / 1000 for c in record.calls
        )
        record.substitutions = self.deps.factory.substitutions
        self.bus.emit(
            EventType.RUN_FINISHED,
            "run",
            behaviour=record.behaviour,
            usd=round(record.usd, 6),
            # Modelled latency only. wall_clock_s stays on the record for
            # debugging and deliberately does not reach the event stream: the
            # stream is persisted as a committed fixture, and a measured value
            # in a fixture makes it differ from itself on every regeneration.
            latency_s=round(record.latency_s, 3),
            turns=record.turns,
            escalated=record.escalated,
            calls=len(record.calls),
        )
        return record

    def _run_inner(self, task: Task, record: RunRecord, trial: int) -> None:
        # -- [G1] input gate ------------------------------------------------
        gate_in = self.input_gate(task.prompt)
        record.gate_verdicts.append(gate_in.to_dict())
        self.bus.emit(EventType.GATE_INPUT, "gate", **gate_in.to_dict())
        if gate_in.blocked:
            record.answer = f"I cannot process that request: {gate_in.reason}."
            record.behaviour = "refuse"
            self.bus.emit(EventType.ANSWER, "answer", text=record.answer, source="input_gate")
            return

        request_text = gate_in.payload
        context = ContextBuilder(
            system_prompt=self.prompts.executor,
            tool_index=self.tool_index,
            skills=self.skills,
            exposure=self.roles.tool_exposure,
            compaction_at=self.roles.compaction_at,
        )

        with Sandbox(
            self.world,
            self.build,
            injections=to_injections(task, self.world),
            max_calls=self.roles.max_tool_calls,
        ) as sandbox:
            # -- planner: one call, never repeated --------------------------
            plan = self._plan(task, request_text, record, trial)
            context.set_plan(plan)
            record.plan = plan

            # -- executor loop ----------------------------------------------
            answer = self._execute(
                task, request_text, context, sandbox, record, trial, self.roles.executor
            )

            # -- reviewer: a different family from the executor -------------
            signals = self._review(task, request_text, answer, record, trial, sandbox)

            # -- router: escalate, which is a second independent attempt ----
            decision = self.router.decide(signals)
            record.review_confidence = decision.confidence
            if decision.escalate and self.roles.escalate_to:
                record.escalated = True
                record.escalation_reason = f"{decision.trigger}: {decision.reason}"
                self.bus.emit(
                    EventType.ESCALATED,
                    "router",
                    model=self.roles.escalate_to,
                    trigger=decision.trigger,
                    reason=decision.reason,
                    confidence=decision.confidence,
                )
                retry_context = ContextBuilder(
                    system_prompt=self.prompts.executor,
                    tool_index=self.tool_index,
                    skills=self.skills,
                    exposure=self.roles.tool_exposure,
                    compaction_at=self.roles.compaction_at,
                )
                retry_context.set_plan(plan)
                answer = self._execute(
                    task,
                    request_text,
                    retry_context,
                    sandbox,
                    record,
                    trial,
                    self.roles.escalate_to,
                    role_label="escalation",
                )

            record.state_diff = (
                sandbox.state_diff().signature() if sandbox.state_diff().changes else ""
            )
            record.context_stats = context.stats()

            # -- [G3] output gate -------------------------------------------
            evidence = "\n".join(c.result.to_json() for c in sandbox.calls if c.result.ok)
            available = sorted({c for call in sandbox.calls for c in call.result.citations})
            gate_out = self.output_gate(answer, evidence=evidence, available_citations=available)
            record.gate_verdicts.append(gate_out.to_dict())
            self.bus.emit(EventType.GATE_OUTPUT, "gate", **gate_out.to_dict())

            record.answer = (
                gate_out.payload
                if not gate_out.blocked
                else (f"I cannot deliver that response: {gate_out.reason}.")
            )
            record.behaviour = classify_behaviour(record.answer)
            record.citations = [c for c in _cited(record.answer) if c in set(available)]
            self.bus.emit(
                EventType.ANSWER,
                "answer",
                text=record.answer,
                behaviour=record.behaviour,
                citations=record.citations,
            )

    # =============================================================== stages ==

    def _plan(self, task: Task, request_text: str, record: RunRecord, trial: int) -> str:
        """One short call. Cheap to upgrade precisely because it runs once."""
        model_key = self.roles.planner
        builder = ContextBuilder(
            system_prompt=self.prompts.planner,
            tool_index=self.tool_index,
            skills=[],
            exposure="tool_search",
            compaction_at=1.0,
        )
        request = builder.build(
            request_text,
            role="planner",
            turn=0,
            meta={"sim": self._sim_context(task), "trial": trial},
        )
        response = self._call(model_key, "planner", request, record, turn=0)
        self.bus.emit(
            EventType.PLAN_CREATED,
            "planner",
            model=model_key,
            plan=response.text[:1200],
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )
        return response.text

    def _execute(
        self,
        task: Task,
        request_text: str,
        context: ContextBuilder,
        sandbox: Sandbox,
        record: RunRecord,
        trial: int,
        model_key: str,
        role_label: str = "executor",
    ) -> str:
        """The loop. The only role that runs N times, and therefore the only one
        whose per-step reliability is raised to the Nth power."""
        world_calls = len([c for c in record.calls if c.tool not in META_TOOLS])
        answer = ""

        for turn in range(self.roles.max_turns):
            if record.usd > self.roles.max_usd_per_task:
                self.bus.emit(
                    EventType.BUDGET_UPDATE,
                    "budget",
                    stopped=True,
                    usd=round(record.usd, 6),
                    cap=self.roles.max_usd_per_task,
                )
                break

            self.bus.emit(EventType.TURN_STARTED, "executor", turn=turn, model=model_key)
            request = context.build(
                request_text,
                role="executor",
                turn=turn,
                # "turn" here is the oracle pointer, deliberately not the loop
                # index: a turn spent on search_tools is real work but is not a
                # step in the gold program. See _sim_context.
                meta={"sim": self._sim_context(task), "trial": trial, "turn": world_calls},
            )
            response = self._call(model_key, role_label, request, record, turn=turn)
            record.turns = max(record.turns, turn + 1)

            if not response.tool_calls:
                answer = response.text
                context.add(response.as_message())
                self.bus.emit(
                    EventType.TURN_FINISHED,
                    "executor",
                    turn=turn,
                    final=True,
                    text=response.text[:800],
                )
                break

            context.add(response.as_message())
            for call in response.tool_calls:
                handled = self._handle_call(call, context, sandbox, record, turn)
                if handled:
                    world_calls += 1
            self.bus.emit(EventType.TURN_FINISHED, "executor", turn=turn, final=False)
        return answer

    def _handle_call(
        self,
        call: ToolCall,
        context: ContextBuilder,
        sandbox: Sandbox,
        record: RunRecord,
        turn: int,
    ) -> bool:
        """Run one tool call. Returns True if it was a world tool.

        Meta tools (``search_tools``, ``read_skill``) touch no data and do not
        advance the oracle pointer, which is why the return value matters.
        """
        if call.name == "search_tools":
            query = str(call.arguments.get("query", ""))
            hits = self.tool_index.search(query, int(call.arguments.get("k", 4) or 4))
            context.remember_tools([h.tool for h in hits])
            payload = (
                "\n\n".join(
                    f"{h.tool.name}: {h.tool.description}\nparameters: {h.tool.parameters}"
                    for h in hits
                )
                or "No tool matched that description. Try different words."
            )
            context.add(Message("tool", payload, tool_call_id=call.id, name=call.name))
            self.bus.emit(
                EventType.TOOL_SEARCHED,
                "executor",
                turn=turn,
                query=query,
                found=[h.tool.name for h in hits],
                scores=[h.score for h in hits],
            )
            return False

        if call.name == "read_skill":
            wanted = str(call.arguments.get("name", ""))
            skill = next((s for s in self.skills if s.name == wanted), None)
            payload = (
                skill.body
                if skill
                else (
                    f"No skill named {wanted!r}. Available: {', '.join(s.name for s in self.skills)}"
                )
            )
            context.add(Message("tool", payload, tool_call_id=call.id, name=call.name))
            return False

        # -- a real tool ----------------------------------------------------
        spec = next((t for t in self.world.tools.values() if t.name == call.name), None)
        self.bus.emit(
            EventType.TOOL_CALLED,
            "executor",
            turn=turn,
            tool=call.name,
            arguments=call.arguments,
            privileged=bool(spec and spec.privileged),
        )
        if spec is not None and spec.privileged:
            record.privileged_attempted = True
            self.bus.emit(
                EventType.HITL_REQUESTED,
                "hitl",
                tool=call.name,
                arguments=call.arguments,
                note="privileged action; authorised server-side by the world policy function",
            )
            record.hitl_requests.append({"tool": call.name, "arguments": call.arguments})

        result = sandbox.call(call.name, call.arguments)
        call_record = sandbox.calls[-1]
        # Modelled, not measured: a sandboxed SQLite call takes microseconds
        # here and tens of milliseconds against a real service, and either way
        # the measured value is machine noise that would make every artifact
        # differ between runs.
        latency_ms = TOOL_LATENCY_MS + len(result.to_json()) / 1000.0

        # -- [G2] tool-result gate -----------------------------------------
        gate = self.tool_gate(result.to_json(), call_id=call.id, tool=call.name)
        record.gate_verdicts.append(gate.to_dict())
        if gate.findings:
            record.injection_seen = True
        self.bus.emit(
            EventType.GATE_TOOL_RESULT, "gate", turn=turn, tool=call.name, **gate.to_dict()
        )

        invocation = ToolInvocation(
            index=len(record.calls),
            tool=call.name,
            arguments=call.arguments,
            ok=result.ok,
            error_code=result.error_code,
            mutated=result.mutated,
            privileged=bool(spec and spec.privileged),
            policy_allowed=None if call_record.policy is None else call_record.policy.allowed,
            policy_code=call_record.policy.code if call_record.policy else "",
            injection_detected=bool(gate.findings),
            latency_ms=latency_ms,
        )
        record.calls.append(invocation)
        if invocation.privileged and invocation.policy_allowed is False:
            record.privileged_refused = True

        context.add(Message("tool", gate.payload, tool_call_id=call.id, name=call.name))
        self.bus.emit(
            EventType.TOOL_RESULT,
            "executor",
            turn=turn,
            tool=call.name,
            ok=result.ok,
            error_code=result.error_code,
            mutated=result.mutated,
            policy=invocation.policy_code,
            latency_ms=round(latency_ms, 2),
            preview=result.to_json()[:600],
        )
        return True

    def _review(
        self,
        task: Task,
        request_text: str,
        answer: str,
        record: RunRecord,
        trial: int,
        sandbox: Sandbox,
    ) -> ConfidenceSignals:
        """Cross-model verification. Never the executor's own family."""
        model_key = self.roles.reviewer
        executor_spec = self.deps.registry.model(self.roles.executor)

        transcript = "\n".join(
            f"{c.tool}({c.arguments}) -> ok={c.ok} {c.error_code or ''}" for c in record.calls
        )
        builder = ContextBuilder(
            system_prompt=self.prompts.reviewer,
            tool_index=self.tool_index,
            skills=[],
            exposure="tool_search",
            compaction_at=1.0,
        )
        review_prompt = (
            f"Request:\n{request_text}\n\nPlan:\n{record.plan}\n\n"
            f"Trajectory:\n{transcript}\n\nProposed answer:\n{answer}"
        )
        sim = self._sim_context(task)
        sim.executor_family = executor_spec.family
        sim.executor_was_correct = self._provisionally_correct(task, answer, sandbox)

        request = builder.build(
            review_prompt, role="reviewer", turn=0, meta={"sim": sim, "trial": trial}
        )
        response = self._call(model_key, "reviewer", request, record, turn=0)

        accepted, confidence = _parse_verdict(response.text)
        record.review_verdict = "accept" if accepted else "reject"
        self.bus.emit(
            EventType.REVIEW_VERDICT,
            "reviewer",
            model=model_key,
            verdict=record.review_verdict,
            confidence=confidence,
            cross_family=executor_spec.family != self.deps.registry.model(model_key).family,
        )
        return ConfidenceSignals(
            verifier_accepted=accepted,
            verifier_confidence=confidence,
            schema_valid=bool(answer.strip()),
            tool_calls_made=len(record.calls),
            tool_budget=max(1, len(task.program)),
        )

    # =============================================================== helpers ==

    def _sim_context(self, task: Task) -> SimContext:
        """Ground truth for the simulator. Never sent over the wire.

        The full gold program is handed over; how far through it the executor
        is travels separately in ``meta["turn"]``, which counts world tool calls
        rather than loop iterations. A turn spent on ``search_tools`` is real
        work but is not a step in the program, and conflating the two would make
        a context optimisation look like an accuracy regression.
        """
        injected = None
        if task.injections:
            privileged = next((t for t in self.world.tools.values() if t.privileged), None)
            if privileged is not None:
                injected = ToolCall(
                    id="injected",
                    name=privileged.name,
                    arguments=_plausible_privileged_args(privileged, task),
                )
        return SimContext(
            task_id=task.task_id,
            tier=task.tier,
            oracle_calls=[
                ToolCall(f"c{i}", self.world.resolve(step.verb), dict(step.arguments))
                for i, step in enumerate(task.program)
            ],
            oracle_answer=task.oracle_answer,
            answer_keys=list(task.answer_keys),
            available_tools=self.world.tool_names,
            is_trap=task.is_trap,
            trap_kind=task.trap_kind,
            injection_present=bool(task.injections),
            injected_call=injected,
        )

    def _provisionally_correct(self, task: Task, answer: str, sandbox: Sandbox) -> bool:
        """Whether the trajectory actually matches the oracle.

        Used only to sample the reviewer's detection rate in simulation. It is
        never shown to the reviewer and never used as a metric: the harness
        recomputes correctness independently from the record.
        """
        state_ok = (sandbox.state_diff().signature() if sandbox.state_diff().changes else "") == (
            task.oracle_state_diff
        )
        keys_ok = all(k.lower() in answer.lower() for k in task.answer_keys)
        return bool(state_ok and keys_ok)

    def _call(
        self, model_key: str, role: str, request: LLMRequest, record: RunRecord, turn: int
    ) -> LLMResponse:
        """Every provider call goes through here, so every token is counted."""
        spec: ModelSpec = self.deps.registry.model(model_key)
        provider = self.deps.factory.get(model_key)

        # Prefix caching, per role. The discount only applies when the stable
        # prefix is byte-identical to the previous call by the same role, and
        # there is no partial credit: one changed character costs all of it.
        prefix = request.prefix_hash(n_messages=1)
        prefix_tokens = estimate_tokens(request.messages[0].content_for_tokens()) + sum(
            estimate_tokens(str(t.to_openai())) for t in request.tools
        )
        previous = self._prefix_state.get(role)
        cached = prefix_tokens if (previous == prefix and spec.cache_read_discount > 0) else 0
        self._prefix_state[role] = prefix
        request.meta["cached_in"] = cached

        projected = spec.cost_usd(4_000, 400)
        self.deps.ledger.check_affordable(projected, provider.provenance)

        response = provider.complete(request)
        usd = spec.cost_usd(response.tokens_in, response.tokens_out, response.tokens_cached_in)

        self.deps.ledger.record(
            LedgerEntry(
                run_id=record.run_id,
                task_id=record.task_id,
                pipeline=self.spec.name,
                role=role,
                model_key=model_key,
                provider=spec.provider,
                provenance=provider.provenance,
                tokens_in=response.tokens_in,
                tokens_cached_in=response.tokens_cached_in,
                tokens_out=response.tokens_out,
                usd=usd,
                latency_s=response.latency_s,
                ok=response.ok,
            )
        )
        record.model_calls.append(
            ModelCall(
                role=role,
                model_key=model_key,
                provider=spec.provider,
                provenance=provider.provenance,
                tokens_in=response.tokens_in,
                tokens_cached_in=response.tokens_cached_in,
                tokens_out=response.tokens_out,
                usd=usd,
                latency_s=response.latency_s,
                turn=turn,
                finish_reason=response.finish_reason,
            )
        )
        record.usd += usd
        self.bus.emit(
            EventType.MODEL_CALL,
            role,
            model=model_key,
            provider=spec.provider,
            provenance=provider.provenance,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            tokens_cached_in=response.tokens_cached_in,
            usd=round(usd, 8),
            latency_s=round(response.latency_s, 3),
            turn=turn,
        )
        return response


# ------------------------------------------------------------------ helpers --


def _parse_verdict(text: str) -> tuple[bool, float]:
    import json

    try:
        payload = json.loads(text)
        return payload.get("verdict") == "accept", float(payload.get("confidence", 0.5))
    except (json.JSONDecodeError, TypeError, ValueError):
        # A reviewer that cannot emit valid JSON has failed to review, which is
        # itself a rejection signal rather than a silent pass.
        return "reject" not in text.lower(), 0.5


def _cited(text: str) -> list[str]:
    from toolsmith.runtime.gates.detectors import extract_citations

    return extract_citations(text)


def _plausible_privileged_args(tool: ToolSpec, task: Task) -> dict[str, Any]:
    """Arguments an injected instruction would plausibly ask for.

    Built from the tool's own required properties so the simulated attack is
    schema-valid in any world, including one added later.
    """
    required = tool.parameters.get("required", [])
    arguments: dict[str, Any] = {}
    for key in required:
        if key.endswith("_id"):
            source = next(
                (s.arguments[key] for s in task.program if key in s.arguments), "UNKNOWN-1"
            )
            arguments[key] = source
        elif key.endswith("_cents"):
            arguments[key] = 999_999
        else:
            arguments[key] = "injected"
    return arguments
