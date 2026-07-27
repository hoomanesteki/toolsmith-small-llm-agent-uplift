"""The world registry.

Adding a domain is a folder plus one line here. Nothing in the runtime, the
harness, the task generator, the report or the UI needs to know a world exists;
they all discover it through this registry and drive it through the shared verb
grammar.

That is the property being demonstrated. A control plane that only works on the
domain it was written for is a demo. This one takes a new domain in an
afternoon, and the conformance suite in ``tests/test_worlds.py`` runs against it
automatically the moment it is registered.
"""

from __future__ import annotations

from toolsmith.worlds.base import WorldSpec


def _load() -> dict[str, WorldSpec]:
    from toolsmith.worlds.clinic import WORLD as CLINIC
    from toolsmith.worlds.doc import WORLD as DOC
    from toolsmith.worlds.ops import WORLD as OPS

    return {world.key: world for world in (OPS, CLINIC, DOC)}


_WORLDS: dict[str, WorldSpec] | None = None


def all_worlds() -> dict[str, WorldSpec]:
    global _WORLDS
    if _WORLDS is None:
        _WORLDS = _load()
    return _WORLDS


def get_world(key: str) -> WorldSpec:
    worlds = all_worlds()
    try:
        return worlds[key]
    except KeyError:
        raise KeyError(
            f"unknown world {key!r}. Registered worlds: {', '.join(sorted(worlds))}"
        ) from None


def world_keys() -> list[str]:
    return sorted(all_worlds())


def worlds_by_role(role: str) -> list[WorldSpec]:
    return [w for w in all_worlds().values() if w.role == role]


def register(world: WorldSpec) -> None:
    """Add a world at runtime. Used by tests and by third-party domains."""
    all_worlds()[world.key] = world
