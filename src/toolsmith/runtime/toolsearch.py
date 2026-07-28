"""``search_tools``: the highest-leverage cost optimisation in the system.

THE ARITHMETIC
--------------
An eleven-tool world costs roughly 1,400 tokens of schema. Put them all in the
system prompt and every turn of a six-turn loop pays for all of them, whether it
uses one or none. Input is already about three quarters of an agent's token
bill, and the executor is the only role that runs N times, so this is the single
largest line item in the whole system.

Ship a meta-tool instead. The model asks for what it needs, gets two to four
schemas, and the per-turn prefix drops by roughly a kilotoken. Track C measures
the effect against the all-in-prompt baseline rather than assuming it.

THE COST
--------
It is not free. The model can search badly and retrieve the wrong tool, which
turns a cheap prefix into a wasted turn. That trade is exactly what the ablation
row ``ablation_no_tool_search`` exists to price, and the honest version of this
argument reports both numbers.

THE RANKING
-----------
Lexical, transparent, and offline. Field-weighted term overlap with an IDF
factor, so a query term that appears in one tool's description outranks one that
appears in all of them. Embeddings would rank better and would make the result
depend on a downloaded model and a machine; at this catalogue size the ceiling is not
where the difficulty lies.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from toolsmith.worlds.base import ToolSpec, WorldSpec

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "get",
        "give",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "should",
        "show",
        "tell",
        "that",
        "the",
        "their",
        "them",
        "there",
        "this",
        "to",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)

#: Field weights. A term in the tool's name is a much stronger signal than the
#: same term in a usage example.
W_NAME = 4.0
W_DESCRIPTION = 1.0
W_EXAMPLE = 2.0
W_VERB = 1.5

DEFAULT_K = 4


def tokenise(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1]


@dataclass(slots=True)
class ToolHit:
    tool: ToolSpec
    score: float
    matched: list[str]


class ToolIndex:
    """A tiny inverted index over one world's tools.

    Built once per world and reused across every task, because rebuilding it per
    turn would trade the tokens saved for wall-clock spent.
    """

    def __init__(self, world: WorldSpec) -> None:
        self.world = world
        self._fields: dict[str, dict[str, set[str]]] = {}
        for tool in world.tools.values():
            self._fields[tool.name] = {
                "name": set(tokenise(tool.name)),
                "description": set(tokenise(tool.description)),
                "examples": set(tokenise(" ".join(tool.examples))),
                "verb": set(tokenise(tool.verb.value)),
            }
        self._idf = self._build_idf()

    def _build_idf(self) -> dict[str, float]:
        documents = len(self._fields)
        counts: dict[str, int] = {}
        for fields in self._fields.values():
            for term in set().union(*fields.values()):
                counts[term] = counts.get(term, 0) + 1
        return {term: math.log(1 + documents / count) for term, count in counts.items()}

    def search(self, query: str, k: int = DEFAULT_K) -> list[ToolHit]:
        terms = tokenise(query)
        if not terms:
            return []
        hits: list[ToolHit] = []
        for tool in self.world.tools.values():
            fields = self._fields[tool.name]
            score = 0.0
            matched: list[str] = []
            for term in terms:
                idf = self._idf.get(term, 0.6)
                weight = (
                    W_NAME * (term in fields["name"])
                    + W_DESCRIPTION * (term in fields["description"])
                    + W_EXAMPLE * (term in fields["examples"])
                    + W_VERB * (term in fields["verb"])
                )
                if weight:
                    score += weight * idf
                    matched.append(term)
            if score > 0:
                hits.append(ToolHit(tool=tool, score=round(score, 4), matched=matched))
        hits.sort(key=lambda h: (-h.score, h.tool.name))
        return hits[:k]

    def schemas_for(self, query: str, k: int = DEFAULT_K) -> list[ToolSpec]:
        return [hit.tool for hit in self.search(query, k)]

    def catalogue(self) -> list[dict[str, str]]:
        """One line per tool: the always-in-context index.

        Names and a short phrase only. The full schema is fetched on demand,
        which is progressive disclosure applied to tools rather than to skills.
        """
        return [
            {
                "name": tool.name,
                "summary": tool.description.split(".")[0][:90],
                "privileged": "yes" if tool.privileged else "",
            }
            for tool in sorted(self.world.tools.values(), key=lambda t: t.name)
        ]


SEARCH_TOOLS_SCHEMA = {
    "name": "search_tools",
    "description": (
        "Find the tools you need for this step. Describe what you want to do in plain "
        "words and you will get back the two to four most relevant tool schemas. Call "
        "this first when you are not certain which tool applies. It does not touch any "
        "data and costs nothing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What you are trying to do, for example 'refund an order' "
                "or 'find a customer by name'.",
                "maxLength": 200,
            },
            "k": {
                "type": "integer",
                "description": "How many schemas to return (1-6).",
                "minimum": 1,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}
