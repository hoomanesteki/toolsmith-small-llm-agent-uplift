"""Handlers every world shares.

``calculator`` and ``today`` are identical in every domain, so they live here
rather than being copied three times. The calculator is a real safe evaluator
rather than ``eval``: it parses with ``ast`` and walks a whitelist of nodes, so
a model cannot reach the interpreter through an arithmetic tool.

Giving an agent a calculator at all is a deliberate design choice. Models are
unreliable at arithmetic and reliable at deciding that arithmetic is needed, so
the tool exists to move the failure from "wrong number" to "wrong plan", which
is the failure the harness can actually diagnose.
"""

from __future__ import annotations

import ast
import operator
import sqlite3
from collections.abc import Callable
from typing import Any

from toolsmith.worlds.base import (
    BASE_DATE,
    ToolResult,
    ToolSpec,
    Verb,
    add_days,
    iso,
)

BinaryOp = Callable[[float, float], float]
UnaryOp = Callable[[float], float]

_BINARY: dict[type[ast.operator], BinaryOp] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], UnaryOp] = {ast.UAdd: operator.pos, ast.USub: operator.neg}

MAX_EXPONENT = 8


def _evaluate(node: ast.AST) -> float:
    match node:
        case ast.Expression():
            return _evaluate(node.body)
        case ast.Constant(value=value) if isinstance(value, int | float):
            return float(value)
        case ast.BinOp(op=op) if type(op) in _BINARY:
            left, right = _evaluate(node.left), _evaluate(node.right)
            if isinstance(op, ast.Pow) and abs(right) > MAX_EXPONENT:
                raise ValueError(f"exponent {right} exceeds the limit of {MAX_EXPONENT}")
            if isinstance(op, ast.Div | ast.FloorDiv | ast.Mod) and right == 0:
                raise ZeroDivisionError("division by zero")
            binary: BinaryOp = _BINARY[type(op)]
            return binary(left, right)
        case ast.UnaryOp(op=op) if type(op) in _UNARY:
            unary: UnaryOp = _UNARY[type(op)]
            return unary(_evaluate(node.operand))
        case _:
            raise ValueError(f"unsupported expression element: {type(node).__name__}")


def calculate(_: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    expression = str(args.get("expression", "")).strip()
    if not expression:
        return ToolResult.failure("invalid_arguments", "expression must not be empty")
    try:
        value = _evaluate(ast.parse(expression, mode="eval"))
    except ZeroDivisionError as exc:
        return ToolResult.failure("division_by_zero", str(exc))
    except (SyntaxError, ValueError, TypeError) as exc:
        return ToolResult.failure(
            "invalid_expression",
            f"{exc}. Only numbers and + - * / // % ** are supported.",
        )
    if value != value or value in (float("inf"), float("-inf")):  # NaN or infinity
        return ToolResult.failure("not_finite", "the expression did not produce a finite number")
    rounded = round(value, 6)
    return ToolResult(
        ok=True,
        data={
            "expression": expression,
            "value": int(rounded) if rounded == int(rounded) else rounded,
        },
    )


def today_handler(_: sqlite3.Connection, args: dict[str, Any]) -> ToolResult:
    offset = int(args.get("offset_days", 0) or 0)
    date = add_days(offset)
    return ToolResult(
        ok=True,
        data={
            "date": iso(date),
            "weekday": date.strftime("%A"),
            "offset_days": offset,
            "note": "This world's clock is fixed. It is never the wall clock.",
        },
    )


CALCULATOR_TOOL = ToolSpec(
    verb=Verb.CALCULATOR,
    name="calculator",
    description=(
        "Evaluate an arithmetic expression. Use this for every calculation "
        "rather than doing the arithmetic yourself. Amounts are in integer cents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "An arithmetic expression, for example '(4999 * 3) - 1200'.",
                "maxLength": 200,
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    },
    handler=calculate,
    examples=[
        "add up three line items",
        "work out 15 percent of an order total",
        "compute the difference between two amounts",
    ],
)

TODAY_TOOL = ToolSpec(
    verb=Verb.TODAY,
    name="today",
    description=(
        f"Return the current date in this system (fixed at {iso(BASE_DATE)}). "
        "Use it before any relative date reasoning such as 'in the last 30 days'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "offset_days": {
                "type": "integer",
                "description": "Optional offset. -30 gives the date thirty days ago.",
                "minimum": -3650,
            }
        },
        "required": [],
        "additionalProperties": False,
    },
    handler=today_handler,
    examples=["what is today's date", "the date thirty days ago", "resolve 'last quarter'"],
)


def rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def one_row(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    cursor = conn.execute(sql, params)
    rows = rows_to_dicts(cursor)
    return rows[0] if rows else None
