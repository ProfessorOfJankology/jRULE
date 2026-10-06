from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any





_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


def _get_path_strict(root: dict[str, Any] | None, path: str) -> tuple[bool, Any]:
    if root is None:
        return False, None
    cur: Any = root
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def render_template(value: Any, ctx: dict[str, Any]) -> str:
    """Render {{ variable.path }} placeholders without executing arbitrary code."""
    text = str(value if value is not None else "")

    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        found, resolved = _get_path_strict(ctx, path)
        if not found:
            raise ValueError(f"template variable {path!r} was not found")
        if resolved is None:
            raise ValueError(f"template variable {path!r} is null")
        if isinstance(resolved, bool):
            return "true" if resolved else "false"
        if isinstance(resolved, (dict, list, tuple)):
            return json.dumps(resolved, separators=(",", ":"), default=str)
        return str(resolved)

    return _TEMPLATE_RE.sub(replace, text)


def get_path(root: dict[str, Any] | None, path: str) -> Any:
    if root is None:
        return None
    cur: Any = root
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def parse_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    s = value.strip()
    if s == "":
        return ""
    lowered = s.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        if s.startswith("[") or s.startswith("{"):
            return json.loads(s)
        if "." in s:
            return float(s)
        return int(s)
    except Exception:
        return value


def _numeric_pair(a: Any, b: Any) -> tuple[float, float] | None:
    try:
        if isinstance(a, bool) or isinstance(b, bool):
            return None
        return float(a), float(b)
    except (TypeError, ValueError):
        return None


def compare(op: str, left: Any, right: Any = None, previous: Any = None) -> bool:
    op = op.lower()
    if op == "exists":
        return left is not None
    if op == "not_exists":
        return left is None
    if op == "is_true":
        return left is True
    if op == "is_false":
        return left is False
    if op == "changed":
        return previous != left
    if op == "changed_to":
        return previous != left and left == right
    if op == "changed_from":
        return previous == right and left != previous

    if op in {"eq", "=="}:
        return left == right
    if op in {"ne", "!="}:
        return left != right

    nums = _numeric_pair(left, right)
    if nums:
        a, b = nums
        if op in {"gt", ">"}:
            return a > b
        if op in {"gte", ">="}:
            return a >= b
        if op in {"lt", "<"}:
            return a < b
        if op in {"lte", "<="}:
            return a <= b

    if op == "contains":
        try:
            return right in left
        except Exception:
            return False
    if op == "not_contains":
        try:
            return right not in left
        except Exception:
            return True
    if op == "in":
        try:
            return left in right
        except Exception:
            return False
    if op == "not_in":
        try:
            return left not in right
        except Exception:
            return True
    if op in {"between", "not_between"}:
        if not isinstance(right, (list, tuple)) or len(right) != 2:
            return False
        low, high = right
        nums_low = _numeric_pair(left, low)
        nums_high = _numeric_pair(left, high)
        if nums_low and nums_high:
            result = nums_low[0] >= nums_low[1] and nums_high[0] <= nums_high[1]
        else:
            try:
                result = str(low) <= str(left) <= str(high)
            except Exception:
                result = False
        return not result if op == "not_between" else result

    # Time-aware wrappers work with zero-padded HH:MM/HH:MM:SS strings.
    if op in {"time_before", "time_after"}:
        if left is None or right is None:
            return False
        current = str(left)
        target = str(right)
        return current < target if op == "time_before" else current > target

    if op in {"time_between", "time_not_between"}:
        if not isinstance(right, (list, tuple)) or len(right) != 2 or left is None:
            return False
        current = str(left)
        start, end = map(str, right)
        if start <= end:
            result = start <= current < end
        else:
            # Overnight windows, e.g. 18:00 -> 06:00.
            result = current >= start or current < end
        return not result if op == "time_not_between" else result

    # ISO YYYY-MM-DD strings sort chronologically, making date rules predictable.
    if op in {"date_before", "date_after"}:
        if left is None or right is None:
            return False
        current = str(left)[:10]
        target = str(right)[:10]
        return current < target if op == "date_before" else current > target

    if op in {"date_between", "date_not_between"}:
        if not isinstance(right, (list, tuple)) or len(right) != 2 or left is None:
            return False
        current = str(left)[:10]
        start, end = str(right[0])[:10], str(right[1])[:10]
        result = start <= current <= end
        return not result if op == "date_not_between" else result

    return False


def resolve_operand(condition: dict[str, Any], ctx: dict[str, Any]) -> Any:
    if condition.get("right_type") == "variable":
        return get_path(ctx, str(condition.get("right", "")))
    return parse_scalar(condition.get("right"))


def evaluate_condition(node: dict[str, Any], ctx: dict[str, Any]) -> bool:
    if not node:
        return True

    if node.get("kind") == "group":
        items = node.get("items") or []
        if not items:
            return True
        logic = node.get("logic", "all").lower()
        results = [evaluate_condition(item, ctx) for item in items]
        return any(results) if logic == "any" else all(results)

    path = str(node.get("left", ""))
    left = get_path(ctx, path)
    previous_path = path
    if path.startswith("current."):
        previous_path = "previous." + path[len("current."):]
    previous = get_path(ctx, previous_path) if previous_path.startswith("previous.") else None
    if path.startswith("current."):
        previous = get_path(ctx, "previous." + path[8:])

    right = resolve_operand(node, ctx)
    return compare(str(node.get("operator", "eq")), left, right, previous)

