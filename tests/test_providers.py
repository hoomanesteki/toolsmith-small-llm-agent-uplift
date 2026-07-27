"""The provider layer, including the property the whole repository rests on:
the simulator is deterministic, so every published number is reproducible."""

from __future__ import annotations

import pytest

from toolsmith.config import load_registry
from toolsmith.config.schema import RateLimit
from toolsmith.providers import (
    LLMRequest,
    Message,
    ProviderFactory,
    SimContext,
    SimulatedProvider,
    ToolCall,
    ToolSchema,
    cache_hit_tokens,
    estimate_tokens,
)
from toolsmith.providers.base import RateLimiter


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def _ctx(task_id: str = "ops-T2-0001") -> SimContext:
    return SimContext(
        task_id=task_id,
        tier="T2",
        oracle_calls=[
            ToolCall("c0", "search_customers", {"query": "ada"}),
            ToolCall("c1", "get_order", {"order_id": "ORD-1042"}),
        ],
        oracle_answer="Order ORD-1042 shipped on 2026-01-14.",
        available_tools=["search_customers", "get_order", "list_orders", "create_ticket"],
    )


def _request(role: str, turn: int = 0, ctx: SimContext | None = None) -> LLMRequest:
    return LLMRequest(
        messages=[
            Message("system", "You are an operations agent."),
            Message("user", "When did order ORD-1042 ship?"),
        ],
        tools=[ToolSchema("get_order", "Fetch an order", {"type": "object"})],
        meta={"sim": ctx or _ctx(), "role": role, "turn": turn, "trial": 0},
    )


# --------------------------------------------------------------- tokenising --


def test_token_estimate_is_monotonic_and_nonzero():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello") >= 1
    assert estimate_tokens("hello world " * 100) > estimate_tokens("hello world")


def test_token_estimate_is_stable_across_calls():
    text = '{"order_id": "ORD-1042", "status": "shipped"}'
    assert estimate_tokens(text) == estimate_tokens(text)


# ------------------------------------------------------------------ caching --


def test_cache_hit_requires_a_byte_identical_prefix():
    request = _request("executor")
    assert cache_hit_tokens(None, request, 500) == 0
    assert cache_hit_tokens(request.prefix_hash(), request, 500) == 500
    assert cache_hit_tokens("something-else", request, 500) == 0


def test_mutating_the_system_prompt_invalidates_the_prefix():
    a = _request("executor")
    b = _request("executor")
    b.messages[0] = Message("system", "You are an operations agent!")
    assert a.prefix_hash() != b.prefix_hash()


# -------------------------------------------------------------- determinism --


def test_simulator_is_deterministic(registry):
    spec = registry.model("groq-oss-20b")
    a = SimulatedProvider(spec).complete(_request("executor"))
    b = SimulatedProvider(spec).complete(_request("executor"))
    assert a.text == b.text
    assert [c.signature() for c in a.tool_calls] == [c.signature() for c in b.tool_calls]
    assert a.tokens_out == b.tokens_out


def test_different_models_diverge(registry):
    a = SimulatedProvider(registry.model("groq-oss-20b")).complete(_request("executor"))
    b = SimulatedProvider(registry.model("claude-opus-5")).complete(_request("executor"))
    assert (a.tokens_out, a.latency_s) != (b.tokens_out, b.latency_s)


def test_different_tasks_diverge(registry):
    spec = registry.model("groq-oss-20b")
    a = SimulatedProvider(spec).complete(_request("executor", ctx=_ctx("ops-T2-0001")))
    b = SimulatedProvider(spec).complete(_request("executor", ctx=_ctx("ops-T2-0002")))
    assert a.raw != b.raw or a.tokens_out != b.tokens_out


# --------------------------------------------------------------- behaviour ---


def test_oracle_replays_the_gold_program_exactly(registry):
    spec = registry.model("oracle")
    provider = SimulatedProvider(spec)
    ctx = _ctx()
    for turn, gold in enumerate(ctx.oracle_calls):
        response = provider.complete(_request("executor", turn=turn, ctx=ctx))
        assert [c.name for c in response.tool_calls] == [gold.name]
        assert response.tool_calls[0].arguments == gold.arguments
    final = provider.complete(_request("executor", turn=len(ctx.oracle_calls), ctx=ctx))
    assert final.text == ctx.oracle_answer


def test_coinflip_mostly_fails(registry):
    """The floor row must actually be a floor."""
    provider = SimulatedProvider(registry.model("coinflip"))
    correct = 0
    for i in range(200):
        ctx = _ctx(f"ops-T2-{i:04d}")
        response = provider.complete(_request("executor", turn=0, ctx=ctx))
        if response.tool_calls and response.tool_calls[0].name == ctx.oracle_calls[0].name:
            correct += 1
    assert correct / 200 < 0.35


def test_strong_model_beats_weak_model_on_first_call(registry):
    """Sanity: the priors must order models the way the model cards do."""

    def first_call_accuracy(model_key: str, n: int = 300) -> float:
        provider = SimulatedProvider(registry.model(model_key))
        hits = 0
        for i in range(n):
            ctx = _ctx(f"ops-T2-{i:04d}")
            response = provider.complete(_request("executor", turn=0, ctx=ctx))
            if response.tool_calls and response.tool_calls[0].name == ctx.oracle_calls[0].name:
                hits += 1
        return hits / n

    assert first_call_accuracy("claude-opus-5") > first_call_accuracy("groq-oss-20b")
    assert first_call_accuracy("groq-oss-20b") > first_call_accuracy("local-mlx-qwen-4b")


def test_error_compounds_across_turns(registry):
    """The core claim about the executor: reliability is raised to the Nth power.

    A model with per-step accuracy q completes an N-step trajectory cleanly at
    roughly q**N. This is why the executor, the only role that runs N times, is
    the wrong place to economise.
    """
    spec = registry.model("groq-oss-20b")
    provider = SimulatedProvider(spec)
    steps = 6
    clean = 0
    trials = 400
    for i in range(trials):
        calls = [ToolCall(f"c{j}", "get_order", {"order_id": f"ORD-{j}"}) for j in range(steps)]
        ctx = SimContext(
            task_id=f"t{i}",
            oracle_calls=calls,
            oracle_answer="done",
            available_tools=["get_order", "list_orders", "create_ticket"],
        )
        ok = True
        for turn in range(steps):
            response = provider.complete(_request("executor", turn=turn, ctx=ctx))
            if (
                not response.tool_calls
                or response.tool_calls[0].signature() != calls[turn].signature()
            ):
                ok = False
                break
        clean += ok
    observed = clean / trials
    per_step = spec.sim.tool_select_acc * spec.sim.param_acc * spec.sim.json_valid_rate
    assert observed == pytest.approx(per_step**steps, abs=0.10)


def test_trap_handling_uses_abstain_skill(registry):
    strong = SimulatedProvider(registry.model("claude-opus-5"))
    weak = SimulatedProvider(registry.model("groq-oss-20b"))

    def abstain_rate(provider, n=200) -> float:
        hits = 0
        for i in range(n):
            ctx = SimContext(
                task_id=f"trap-{i}",
                tier="T4",
                is_trap=True,
                trap_kind="unanswerable",
                oracle_answer="I cannot answer that from the available records.",
                available_tools=["get_order"],
            )
            response = provider.complete(_request("executor", turn=0, ctx=ctx))
            hits += "cannot" in response.text
        return hits / n

    assert abstain_rate(strong) > abstain_rate(weak)


def test_injection_resistance_is_a_measured_rate(registry):
    weak = SimulatedProvider(registry.model("groq-oss-20b"))
    strong = SimulatedProvider(registry.model("claude-opus-5"))
    injected = ToolCall("evil", "issue_refund", {"order_id": "ORD-1", "amount_cents": 999999})

    def follow_rate(provider, n=200) -> float:
        hits = 0
        for i in range(n):
            ctx = _ctx(f"inj-{i}")
            ctx.injection_present = True
            ctx.injected_call = injected
            response = provider.complete(_request("executor", turn=0, ctx=ctx))
            hits += any(c.name == "issue_refund" for c in response.tool_calls)
        return hits / n

    assert follow_rate(weak) > follow_rate(strong)


def test_self_review_reduces_detection(registry):
    """Same-family review must catch fewer real errors than cross-family review."""
    reviewer = SimulatedProvider(registry.model("groq-oss-120b"))

    def detection(executor_family: str | None, n: int = 300) -> float:
        caught = 0
        for i in range(n):
            ctx = _ctx(f"rev-{i}")
            ctx.executor_was_correct = False
            ctx.executor_family = executor_family
            response = reviewer.complete(_request("reviewer", turn=0, ctx=ctx))
            caught += '"reject"' in response.text
        return caught / n

    assert detection("openai-oss") < detection("qwen")


# ------------------------------------------------------------------ factory --


def test_factory_returns_the_simulator_without_keys(registry):
    factory = ProviderFactory(registry, mode="simulated")
    assert factory.get("claude-opus-5").provenance == "simulated"
    assert factory.get("groq-oss-20b").provenance == "simulated"


def test_factory_caches_instances(registry):
    factory = ProviderFactory(registry, mode="simulated")
    assert factory.get("groq-oss-20b") is factory.get("groq-oss-20b")


def test_live_mode_fails_loudly_without_a_key(registry, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("toolsmith.providers.registry.available_providers", lambda: {"groq": False})
    factory = ProviderFactory(registry, mode="live")
    with pytest.raises(RuntimeError, match="no key for"):
        factory.get("groq-oss-20b")


def test_auto_mode_records_substitutions(registry, monkeypatch):
    monkeypatch.setattr(
        "toolsmith.providers.registry.available_providers",
        lambda: {"groq": True, "anthropic": False},
    )
    factory = ProviderFactory(registry, mode="auto")
    factory.get("claude-opus-5")
    assert "claude-opus-5" in factory.substitutions


# ------------------------------------------------------------ rate limiting --


def test_limiter_raises_when_the_daily_token_budget_is_gone():
    limiter = RateLimiter(RateLimit(provider="groq", rpm=1000, tpd=1000))
    limiter.acquire(900)
    with pytest.raises(RuntimeError, match="daily token budget"):
        limiter.acquire(200)


def test_cached_tokens_do_not_consume_the_daily_budget():
    """Groq's most exploitable free-tier fact, encoded as behaviour."""
    limiter = RateLimiter(RateLimit(provider="groq", rpm=1000, tpd=1000))
    for _ in range(50):
        limiter.acquire(900, billable_tokens=0)
    assert limiter.headroom()["tokens_today"] == 0
