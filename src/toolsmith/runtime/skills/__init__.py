"""Capabilities as documents, disclosed progressively.

Each folder is one skill: a forty-word description that is always in context,
and a body loaded only when the model asks for it. See :mod:`loader` for why.
"""

from toolsmith.runtime.skills.loader import (
    MAX_DESCRIPTION_WORDS,
    READ_SKILL_SCHEMA,
    SKILLS_DIR,
    Skill,
    load_skills,
    skill_index,
    skills_for,
)

__all__ = [
    "MAX_DESCRIPTION_WORDS",
    "READ_SKILL_SCHEMA",
    "SKILLS_DIR",
    "Skill",
    "load_skills",
    "skill_index",
    "skills_for",
]
