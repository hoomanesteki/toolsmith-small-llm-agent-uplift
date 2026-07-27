"""The front end has no build step, so nothing checks it. This does.

ADR 0002 chose hand-written ES modules over a bundler, and the cost it named was
losing the checks a bundler gives you for free. That cost came due: `flow.js`
used `blurbs` without importing it, which is a ReferenceError on the first line
of the screen and a parser cannot see it. The router's try/catch turned it into
"That screen failed to load", so the app looked like it had a data problem.

These are the two checks a bundler would have performed, written out.
"""

from __future__ import annotations

import re

import pytest

from toolsmith.config import REPO_ROOT

WEB = REPO_ROOT / "web" / "js"
MODULES = sorted(WEB.glob("*.js")) + sorted((WEB / "screens").glob("*.js"))

#: Anything a module may use without importing it.
GLOBALS = {
    "window",
    "document",
    "console",
    "localStorage",
    "history",
    "location",
    "fetch",
    "EventSource",
    "URLSearchParams",
    "CustomEvent",
    "Event",
    "Error",
    "Node",
    "Math",
    "JSON",
    "Object",
    "Array",
    "Number",
    "String",
    "Boolean",
    "Set",
    "Map",
    "Date",
    "Promise",
    "performance",
    "requestAnimationFrame",
    "setTimeout",
    "clearTimeout",
    "setInterval",
    "clearInterval",
    "encodeURIComponent",
    "decodeURIComponent",
    "isNaN",
    "parseFloat",
    "parseInt",
    "structuredClone",
}


def _code_only(text: str) -> str:
    """Strip imports, comments and string literals.

    Without the last of those, `"/flow?run="` inside a template literal reads as
    a use of the exported `flow` function, and the check reports a file for
    importing something it plainly does not need.
    """
    body = re.sub(r"^import[^;]*;", "", text, flags=re.M)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"`(?:[^`\\]|\\.)*`", '""', body)
    body = re.sub(r"'(?:[^'\\\n]|\\.)*'", '""', body)
    body = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', body)
    return body


def _exports(path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    names = set(re.findall(r"^export\s+(?:async\s+)?function\s+(\w+)", text, re.M))
    names |= set(re.findall(r"^export\s+(?:const|let|class)\s+(\w+)", text, re.M))
    return names


def _imported(text: str) -> set[str]:
    names: set[str] = set()
    for block in re.findall(r"import\s*\{([^}]*)\}\s*from", text):
        for part in block.split(","):
            names.add(part.split(" as ")[-1].strip())
    return {n for n in names if n}


def _declared(text: str) -> set[str]:
    names = set(re.findall(r"\b(?:const|let|var)\s+(\w+)", text))
    names |= set(re.findall(r"\b(?:async\s+)?function\s+(\w+)", text))
    names |= set(re.findall(r"\bclass\s+(\w+)", text))
    # Destructuring, parameters and catch bindings, conservatively.
    for block in re.findall(r"(?:const|let|var)\s*\{([^}]*)\}", text):
        for part in block.split(","):
            names.add(part.split(":")[-1].split("=")[0].strip())
    for block in re.findall(r"\(([^)]*)\)\s*=>", text):
        for part in block.split(","):
            names.add(part.split("=")[0].strip().lstrip("."))
    return {n for n in names if n and n.isidentifier()}


EXPORTED = {name for path in MODULES for name in _exports(path)}


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_every_module_name_a_file_uses_is_one_it_imports(module):
    """A name exported by a sibling and used here must appear in an import.

    This is exactly the `blurbs` bug: the identifier existed, the module that
    exported it was already loaded by the router, and the file that used it
    never asked for it.
    """
    text = module.read_text(encoding="utf-8")
    body = _code_only(text)

    available = _imported(text) | _declared(text) | _exports(module) | GLOBALS
    used = set(re.findall(r"\b([a-zA-Z_$][\w$]*)\b", body))

    missing = sorted((used & EXPORTED) - available)
    assert not missing, f"{module.name} uses {missing} without importing them"


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_module_imports_something_it_never_uses(module):
    text = module.read_text(encoding="utf-8")
    body = _code_only(text)
    unused = sorted(n for n in _imported(text) if not re.search(rf"\b{re.escape(n)}\b", body))
    assert not unused, f"{module.name} imports {unused} and never uses them"
