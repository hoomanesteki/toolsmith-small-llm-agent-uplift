"""Progressive disclosure for capabilities.

A skill is a folder with a ``SKILL.md``. Its front matter carries a description
of at most forty words, and that description is the only thing that sits in
context by default. The body is loaded when the model asks for it.

The reason is the same arithmetic that motivates tool-search. Six capability
documents at 400 tokens each is 2,400 tokens on every turn of every task,
whether they are relevant or not, and the executor pays it N times. Forty-word
descriptions cost about 250 tokens for the whole set, and the model reaches for
a body perhaps once per task.

There is a second reason, which is about behaviour rather than cost. A
megaprompt containing every rule for every situation gets followed
inconsistently, because most of it is irrelevant to the turn at hand. A short
index plus a document fetched at the moment of need is read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).parent
MAX_DESCRIPTION_WORDS = 40

_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    body: str
    applies_to: list[str]
    """World keys, or ``["*"]`` for every domain."""

    path: Path

    @property
    def word_count(self) -> int:
        return len(self.description.split())

    def index_line(self) -> str:
        return f"- **{self.name}**: {self.description}"


def _parse(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(text)
    meta: dict[str, str] = {}
    body = text
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        body = text[match.end() :]
    return Skill(
        name=meta.get("name", path.parent.name),
        description=meta.get("description", "").strip(),
        body=body.strip(),
        applies_to=[w.strip() for w in meta.get("applies_to", "*").split(",") if w.strip()],
        path=path,
    )


def load_skills(directory: Path | None = None) -> list[Skill]:
    directory = directory or SKILLS_DIR
    skills = [_parse(path) for path in sorted(directory.glob("*/SKILL.md"))]
    for skill in skills:
        if skill.word_count > MAX_DESCRIPTION_WORDS:
            raise ValueError(
                f"skill {skill.name!r} has a {skill.word_count}-word description; the limit is "
                f"{MAX_DESCRIPTION_WORDS}. The description is always in context, so its length "
                "is paid on every turn of every task."
            )
        if not skill.body:
            raise ValueError(f"skill {skill.name!r} has no body")
    return skills


def skills_for(world_key: str, directory: Path | None = None) -> list[Skill]:
    return [s for s in load_skills(directory) if "*" in s.applies_to or world_key in s.applies_to]


def skill_index(skills: list[Skill]) -> str:
    """The always-in-context block. Short by construction."""
    if not skills:
        return ""
    lines = "\n".join(s.index_line() for s in skills)
    return (
        "## Available guidance\n"
        "Each line below names a document you can read in full with "
        "`read_skill(name)`. Read one when the situation matches; do not guess.\n"
        f"{lines}"
    )


READ_SKILL_SCHEMA = {
    "name": "read_skill",
    "description": (
        "Read one of the guidance documents listed under 'Available guidance' in full. "
        "Use it when a situation matches a description and you need the detail. Costs "
        "nothing and touches no data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The skill name.", "maxLength": 60}
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}
