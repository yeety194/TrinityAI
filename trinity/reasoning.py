from __future__ import annotations

import ast
import math
import operator
import re
from datetime import datetime, timedelta, timezone

# Arithmetic is evaluated from a parsed tree, never with eval().
_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "floor": math.floor,
    "ceil": math.ceil,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

_WORD_OPERATORS = (
    (r"\bplus\b", "+"),
    (r"\bminus\b", "-"),
    (r"\btimes\b", "*"),
    (r"\bmultiplied by\b", "*"),
    (r"\bdivided by\b", "/"),
    (r"\bto the power of\b", "**"),
    (r"\bpercent of\b", "/100*"),
    (r"[,$]", ""),
    (r"\^", "**"),
)


class CalculationError(ValueError):
    pass


def calculate(expression: str) -> str:
    cleaned = expression.strip().rstrip("=?").strip()
    if not cleaned:
        raise CalculationError("No expression given.")
    lowered = cleaned.lower()
    for pattern, replacement in _WORD_OPERATORS:
        lowered = re.sub(pattern, replacement, lowered)
    try:
        tree = ast.parse(lowered, mode="eval")
    except SyntaxError as exc:
        raise CalculationError(f"I could not read '{expression}' as maths.") from exc
    value = _evaluate(tree.body)
    return _format_number(value)


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculationError("Only numbers are allowed.")
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _BINARY[type(node.op)](_evaluate(node.left), _evaluate(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        if name not in _FUNCTIONS:
            raise CalculationError(f"{name} is not a function I can use.")
        return float(_FUNCTIONS[name](*[_evaluate(arg) for arg in node.args]))
    if isinstance(node, (ast.List, ast.Tuple)):
        raise CalculationError("Lists are not supported.")
    raise CalculationError("That expression has a part I will not evaluate.")


def _format_number(value: float) -> str:
    if value != value or value in (float("inf"), float("-inf")):
        return str(value)
    if abs(value - round(value)) < 1e-9 and abs(value) < 1e15:
        return f"{int(round(value)):,}"
    return f"{round(value, 6):,}".rstrip("0").rstrip(".")


_RELATIVE_RE = re.compile(
    r"\bin\s+(?:(?P<num>\d+(?:\.\d+)?)|(?P<word>a|an|half an))\s*"
    r"(?P<unit>second|sec|minute|min|hour|hr|day|week)s?\b",
    re.I,
)
_CLOCK_RE = re.compile(
    r"\bat\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?\b", re.I
)
_UNIT_SECONDS = {
    "second": 1,
    "sec": 1,
    "minute": 60,
    "min": 60,
    "hour": 3600,
    "hr": 3600,
    "day": 86400,
    "week": 604800,
}


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Understand 'in 20 minutes', 'in an hour', 'at 7pm', 'tomorrow at 9'."""
    moment = now or datetime.now().astimezone()
    lowered = text.lower()

    relative = _RELATIVE_RE.search(lowered)
    if relative:
        unit = _UNIT_SECONDS[relative.group("unit").lower()]
        word = (relative.group("word") or "").lower()
        if word in {"a", "an"}:
            amount = 1.0
        elif word == "half an":
            amount = 0.5
        else:
            amount = float(relative.group("num"))
        return moment + timedelta(seconds=amount * unit)

    clock = _CLOCK_RE.search(lowered)
    if clock:
        hour = int(clock.group("hour"))
        minute = int(clock.group("minute") or 0)
        ampm = (clock.group("ampm") or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif not ampm and hour < 8:
            # "at 7" almost always means the evening for a reminder.
            hour += 12
        target = moment.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        if "tomorrow" in lowered or target <= moment:
            target += timedelta(days=1)
        return target

    if "tomorrow" in lowered:
        return (moment + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return None


def describe_delay(due: datetime, now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    seconds = max(0, int((due - moment).total_seconds()))
    if seconds < 90:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} minutes"
    hours = minutes / 60
    if hours < 36:
        return f"{hours:.1f} hours".replace(".0", "")
    return f"{hours / 24:.1f} days".replace(".0", "")
