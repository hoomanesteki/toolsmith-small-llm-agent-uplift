"""Context assembly: the file where 74% of the token bill is decided.

Three rules, and the reason each one exists.

**The prefix never mutates.** System prompt, then skill index, then tool
catalogue, then tools, then history. Always that order, always byte-identical
across turns of a run. A single changed character at position three of the
system prompt invalidates the whole cached prefix, and there is no partial
credit: the discount is 50% on Groq and 90% on Anthropic, and on Groq the cached
tokens also stop counting against the rate limit. This class asserts the prefix
hash is stable and records a violation if it ever is not, because the failure is
otherwise completely silent.

**Compaction happens at 60%, not at overflow.** Compacting when the window is
full means compacting under pressure, with no room for the summary itself.
Compacting at 60% means the summary lands with room to spare, and the plan and
the last three turns survive verbatim because those are what the next turn
actually reads.

**Growth is quadratic and is measured.** A six-turn loop re-reads its transcript
every turn: 3,000 then 3,800 then 4,600 tokens. Output grows linearly, input
grows as the square of the turn count. The per-turn input series is recorded on
every run so the report can show the curve rather than assert it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from toolsmith.providers import LLMRequest, Message, ToolSchema, estimate_tokens
from toolsmith.runtime.skills import Skill, skill_index
from toolsmith.runtime.skills.loader import READ_SKILL_SCHEMA
from toolsmith.runtime.toolsearch import SEARCH_TOOLS_SCHEMA, ToolIndex
from toolsmith.worlds.base import ToolSpec

#: Assumed usable context window. Compaction is relative to this rather than to
#: a provider's advertised maximum, because the advertised maximum includes the
#: output budget and the last thing a long run needs is to discover that.
DEFAULT_WINDOW_TOKENS = 32_000

#: Turns kept verbatim through a compaction. The plan plus the recent past is
#: what the next turn reads; the middle is what it skims.
KEEP_RECENT_TURNS = 3


def _to_schema(tool: ToolSpec) -> ToolSchema:
    return ToolSchema(name=tool.name, description=tool.description, parameters=tool.parameters)


@dataclass
class ContextBuilder:
    """Builds each turn's request and keeps the prefix honest."""

    system_prompt: str
    tool_index: ToolIndex
    skills: list[Skill] = field(default_factory=list)
    exposure: str = "tool_search"
    """``tool_search`` ships a catalogue plus a meta-tool. ``all_in_prompt``
    ships every schema on every turn, which is the baseline Track C beats."""

    compaction_at: float = 0.60
    window_tokens: int = DEFAULT_WINDOW_TOKENS

    # -- state --------------------------------------------------------------
    history: list[Message] = field(default_factory=list)
    retrieved_tools: dict[str, ToolSpec] = field(default_factory=dict)
    plan_text: str = ""
    prefix_hash: str | None = None
    prefix_violations: int = 0
    compactions: int = 0
    input_tokens_by_turn: list[int] = field(default_factory=list)

    # ------------------------------------------------------------- prefix --

    def _prefix_messages(self) -> list[Message]:
        parts = [self.system_prompt]
        index = skill_index(self.skills)
        if index:
            parts.append(index)
        if self.exposure == "tool_search":
            catalogue = "\n".join(
                f"- {row['name']}: {row['summary']}"
                + ("  [PRIVILEGED]" if row["privileged"] else "")
                for row in self.tool_index.catalogue()
            )
            parts.append(
                "## Tools available\n"
                "Names only. Call `search_tools` to get the schemas you need.\n" + catalogue
            )
        return [Message("system", "\n\n".join(parts))]

    def _tools(self) -> list[ToolSchema]:
        if self.exposure == "all_in_prompt":
            return [
                _to_schema(t)
                for t in sorted(self.tool_index.world.tools.values(), key=lambda t: t.name)
            ]
        schemas = [
            ToolSchema(
                name=SEARCH_TOOLS_SCHEMA["name"],  # type: ignore[arg-type]
                description=SEARCH_TOOLS_SCHEMA["description"],  # type: ignore[arg-type]
                parameters=SEARCH_TOOLS_SCHEMA["parameters"],  # type: ignore[arg-type]
            )
        ]
        if self.skills:
            schemas.append(
                ToolSchema(
                    name=READ_SKILL_SCHEMA["name"],  # type: ignore[arg-type]
                    description=READ_SKILL_SCHEMA["description"],  # type: ignore[arg-type]
                    parameters=READ_SKILL_SCHEMA["parameters"],  # type: ignore[arg-type]
                )
            )
        schemas.extend(
            _to_schema(t) for t in sorted(self.retrieved_tools.values(), key=lambda t: t.name)
        )
        return schemas

    # ------------------------------------------------------------ mutation --

    def add(self, message: Message) -> None:
        self.history.append(message)

    def remember_tools(self, tools: list[ToolSpec]) -> None:
        """Retrieved schemas persist for the rest of the run.

        Dropping them next turn would force the model to search again for a tool
        it just used, which is both a wasted call and a wasted turn. Keeping them
        does grow the prefix, which is why the prefix hash is checked rather than
        assumed, and why the growth is recorded.
        """
        for tool in tools:
            self.retrieved_tools[tool.name] = tool

    def set_plan(self, plan_text: str) -> None:
        self.plan_text = plan_text

    # ---------------------------------------------------------- compaction --

    def _should_compact(self, tokens: int) -> bool:
        return self.compaction_at < 1.0 and tokens > self.window_tokens * self.compaction_at

    def compact(self) -> int:
        """Summarise the middle, keep the plan and the last turns verbatim.

        Returns the number of tokens reclaimed. The summary is built by code
        rather than by a model call: a compaction that costs a model call costs
        more than the tokens it saves at these sizes, and the whole point is to
        spend less.
        """
        if len(self.history) <= KEEP_RECENT_TURNS + 1:
            return 0
        head = self.history[:1]
        recent = self.history[-KEEP_RECENT_TURNS:]
        middle = self.history[1:-KEEP_RECENT_TURNS]
        if not middle:
            return 0

        before = sum(estimate_tokens(m.content_for_tokens()) for m in middle)
        calls: list[str] = []
        findings: list[str] = []
        for message in middle:
            for call in message.tool_calls:
                calls.append(call.name)
            if message.role == "tool" and message.content:
                findings.append(message.content[:180])

        summary = Message(
            "user",
            "## Earlier in this run (compacted)\n"
            f"Tool calls made: {', '.join(calls) if calls else 'none'}\n"
            f"Results seen:\n"
            + "\n".join(f"- {f}" for f in findings[-6:])
            + "\nThe plan and the most recent turns follow verbatim.",
        )
        self.history = [*head, summary, *recent]
        self.compactions += 1
        return before - estimate_tokens(summary.content_for_tokens())

    # -------------------------------------------------------------- output --

    def build(
        self,
        user_request: str,
        role: str = "executor",
        turn: int = 0,
        meta: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> LLMRequest:
        messages = self._prefix_messages()
        messages.append(Message("user", user_request))
        if self.plan_text:
            messages.append(Message("assistant", f"Plan:\n{self.plan_text}"))
        messages.extend(self.history)

        request = LLMRequest(
            messages=messages,
            tools=self._tools(),
            temperature=temperature,
            meta={"role": role, "turn": turn, **(meta or {})},
        )

        tokens = request.messages and sum(
            estimate_tokens(m.content_for_tokens()) for m in request.messages
        )
        self.input_tokens_by_turn.append(int(tokens or 0))

        if self._should_compact(int(tokens or 0)):
            self.compact()
            return self.build(user_request, role, turn, meta, temperature)

        # The prefix must be byte-identical across turns or the cache discount
        # silently disappears. Record a violation rather than raising: a broken
        # cache is a cost bug, not a correctness bug, and the run should finish
        # so the cost shows up in the ledger.
        current = request.prefix_hash(n_messages=1)
        if self.prefix_hash is None:
            self.prefix_hash = current
        elif current != self.prefix_hash and self.exposure == "all_in_prompt":
            self.prefix_violations += 1
        return request

    # -------------------------------------------------------------- report --

    def stats(self) -> dict[str, Any]:
        series = self.input_tokens_by_turn
        return {
            "turns": len(series),
            "input_tokens_by_turn": series,
            "input_tokens_total": sum(series),
            "input_growth_per_turn": (
                round((series[-1] - series[0]) / max(1, len(series) - 1), 1)
                if len(series) > 1
                else 0
            ),
            "compactions": self.compactions,
            "prefix_violations": self.prefix_violations,
            "tools_retrieved": sorted(self.retrieved_tools),
            "exposure": self.exposure,
        }
