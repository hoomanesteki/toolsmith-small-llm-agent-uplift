"""Prove the model-agnosticism claim instead of asserting it.

The headline property of this repository is that every model is configuration
and never code. That is easy to say and easy to break: one convenience default,
one "just for now" constant, and the claim is quietly false while the README
still makes it.

So it is a gate. This module parses every Python file under ``src/`` with
``ast``, ignores docstrings and comments (documentation is allowed and expected
to name models), and fails if a provider model identifier appears in executable
code.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from toolsmith.config import REPO_ROOT, Registry, load_registry

#: Control pseudo-models. They are not vendor identifiers, so naming them in
#: code is not a portability leak.
EXEMPT_IDS: frozenset[str] = frozenset({"oracle", "coinflip"})


@dataclass
class Hardcode:
    path: str
    line: int
    model_id: str
    context: str


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of Constant nodes that are module, class or function docstrings."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def scan_source(
    root: Path | None = None, registry: Registry | None = None
) -> tuple[list[Hardcode], int, int]:
    """Return (violations, files scanned, model ids checked)."""
    registry = registry or load_registry()
    source_root = (root or REPO_ROOT) / "src" / "toolsmith"

    # Both the vendor identifier and the repository's own key for it. The gate
    # checked only the former for a while, and missed a judge panel written as
    # `("groq-oss-120b", "gemini-20-flash", "mistral-medium")` in
    # `harness/judges.py`, because the vendor string for the first of those is
    # `openai/gpt-oss-120b`. Zero offenders, three models named in executable
    # code, and swapping the panel that grades every published qualitative
    # number was a Python edit while the README said it was a YAML edit.
    #
    # A key is as much a portability leak as a vendor id. It is the name of a
    # row in `models.yaml`, and code that spells it has picked a model.
    ids = {spec.model_id for spec in registry.models.values()} | set(registry.models)
    ids -= EXEMPT_IDS

    violations: list[Hardcode] = []
    files = sorted(source_root.rglob("*.py"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        # Cheap pre-filter: most files mention no model id at all.
        if not any(model_id in text for model_id in ids):
            continue
        tree = ast.parse(text, filename=str(path))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            for model_id in ids:
                if model_id in node.value:
                    violations.append(
                        Hardcode(
                            path=str(path.relative_to(root or REPO_ROOT)),
                            line=node.lineno,
                            model_id=model_id,
                            context=node.value[:60],
                        )
                    )
    return violations, len(files), len(ids)
