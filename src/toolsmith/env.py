"""Minimal ``.env`` loading, with no dependency and no surprises.

Deliberately not python-dotenv: this is thirty lines, it never overwrites an
already-exported variable, and it means `uv sync` pulls one fewer package into
a project whose entire argument is about counting costs honestly.
"""

from __future__ import annotations

import os
from pathlib import Path

from toolsmith.config import REPO_ROOT

#: Which environment variable holds each provider's key.
KEY_VARS: dict[str, tuple[str, ...]] = {
    "groq": ("GROQ_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "mistral": ("MISTRAL_API_KEY",),
    "together": ("TOGETHER_API_KEY",),
    "mlx": (),
    "simulated": (),
}

_loaded = False


def load_dotenv(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Read ``KEY=value`` lines from ``.env`` into the process environment."""
    global _loaded
    path = path or REPO_ROOT / ".env"
    found: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            found[key] = value
            if override or key not in os.environ:
                os.environ[key] = value
    _loaded = True
    return found


def api_key(provider: str) -> str | None:
    """Return the key for a provider, loading ``.env`` on first use."""
    if not _loaded:
        load_dotenv()
    for var in KEY_VARS.get(provider, ()):
        value = os.environ.get(var)
        if value:
            return value
    return None


def available_providers() -> dict[str, bool]:
    """Which providers this machine could actually call right now."""
    if not _loaded:
        load_dotenv()
    out = {}
    for provider, variables in KEY_VARS.items():
        out[provider] = True if not variables else any(os.environ.get(v) for v in variables)
    return out
