"""Sandboxed domains with ground truth by construction.

Three worlds ship in the box. They share one thirteen-verb grammar and disagree
about everything else, which is what turns "it works on my toy world" into a
measured generalisation result.

| World | Role | What it tests |
|---|---|---|
| ``ops`` | primary | Multi-hop lookup, policy reasoning, a privileged action |
| ``clinic`` | transfer | The same verbs against a schema never trained on |
| ``doc`` | grounding | Retrieval, citation and faithfulness |

A fourth domain is a folder. See :mod:`toolsmith.worlds.base` for the contract.
"""

from toolsmith.worlds.base import (
    BASE_DATE,
    Entity,
    PolicyDecision,
    RowChange,
    StateDiff,
    ToolResult,
    ToolSpec,
    Verb,
    WorldSpec,
    add_days,
    cents,
    db_digest,
    diff_snapshots,
    format_money,
    iso,
    parse_iso,
    snapshot,
)
from toolsmith.worlds.registry import (
    all_worlds,
    get_world,
    register,
    world_keys,
    worlds_by_role,
)
from toolsmith.worlds.sandbox import (
    BUILD_DIR,
    CallRecord,
    Injection,
    Sandbox,
    WorldBuild,
    build_world,
    validate_arguments,
)

__all__ = [
    "BASE_DATE",
    "BUILD_DIR",
    "CallRecord",
    "Entity",
    "Injection",
    "PolicyDecision",
    "RowChange",
    "Sandbox",
    "StateDiff",
    "ToolResult",
    "ToolSpec",
    "Verb",
    "WorldBuild",
    "WorldSpec",
    "add_days",
    "all_worlds",
    "build_world",
    "cents",
    "db_digest",
    "diff_snapshots",
    "format_money",
    "get_world",
    "iso",
    "parse_iso",
    "register",
    "snapshot",
    "validate_arguments",
    "world_keys",
    "worlds_by_role",
]
