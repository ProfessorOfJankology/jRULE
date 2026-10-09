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


def resolve_template_value(path: str, ctx: dict[str, Any]) -> Any:
    """Resolve a template path while distinguishing missing from JSON null."""
    found,resolved=_get_path_strict(ctx,path)
    if not found:
        raise ValueError(f"template variable {path!r} was not found")
    return resolved


def render_template(value: Any, ctx: dict[str, Any]) -> str:
    """Render {{ variable.path }} placeholders without executing arbitrary code."""
    text = str(value if value is not None else "")

    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        resolved = resolve_template_value(path, ctx)
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


def apply_transform(value: Any, transform: dict[str, Any]) -> Any:
    """Apply a small, deterministic value transform used by visual rules."""
    op=str(transform.get("op","")).lower()
    arg=transform.get("arg")
    if op=="count":
        return len(value) if isinstance(value,(list,dict,str,tuple)) else 0
    if op=="first":
        if isinstance(value,(list,tuple,str)) and value:return value[0]
        return None
    if op=="last":
        if isinstance(value,(list,tuple,str)) and value:return value[-1]
        return None
    if op=="key":
        return value.get(str(arg)) if isinstance(value,dict) else None
    if op=="index":
        try:return value[int(arg)] if isinstance(value,(list,tuple,str)) else None
        except (ValueError,TypeError,IndexError):return None
    if op=="as_number":
        try:
            number=float(value)
            return int(number) if number.is_integer() else number
        except (ValueError,TypeError):return None
    if op=="as_string":
        if value is None:return None
        if isinstance(value,(dict,list,tuple)):return json.dumps(value,separators=(",",":"),default=str)
        return str(value)
    if op=="as_boolean":
        if isinstance(value,bool):return value
        if isinstance(value,(int,float)):return value!=0
        if isinstance(value,str):
            lowered=value.strip().lower()
            if lowered in {"true","1","yes","on"}:return True
            if lowered in {"false","0","no","off",""}:return False
        return None
    if op=="as_date":
        return str(value)[:10] if value is not None else None
    if op=="as_time":
        if value is None:return None
        text=str(value)
        return text[11:19] if "T" in text and len(text)>=19 else text[:8]
    if op=="lowercase":
        return str(value).lower() if value is not None else None
    if op=="uppercase":
        return str(value).upper() if value is not None else None
    raise ValueError(f"Unknown transform: {op}")


def resolve_expression(expr: Any, ctx: dict[str, Any]) -> Any:
    """Resolve either a legacy dotted path or a structured value expression."""
    if isinstance(expr,str):
        return get_path(ctx,expr)
    if not isinstance(expr,dict):
        return expr
    value=get_path(ctx,str(expr.get("source","")))
    for transform in expr.get("transforms") or []:
        value=apply_transform(value,transform)
    return value


def previous_expression(expr: Any) -> Any:
    """Return the matching previous-state expression for current-state input."""
    if isinstance(expr,str):
        return "previous."+expr[8:] if expr.startswith("current.") else expr
    if isinstance(expr,dict):
        result=dict(expr)
        source=str(result.get("source",""))
        if source.startswith("current."):
            result["source"]="previous."+source[8:]
        result["transforms"]=[dict(t) for t in (expr.get("transforms") or [])]
        return result
    return expr


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


def _contains_recursive(value: Any, target: Any) -> bool:
    """Search nested JSON values (not dictionary keys) for an exact scalar match.

    Strings are treated as whole values, unlike the existing contains operator.
    Iterative traversal avoids recursion-depth errors on unusually nested data.
    """
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
        elif item == target and not isinstance(target, (dict, list, tuple)):
            return True
    return False


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
    elif op in {"gt",">","gte",">=","lt","<","lte","<="} and left is not None and right is not None:
        try:
            a,b=str(left),str(right)
            if op in {"gt",">"}:return a>b
            if op in {"gte",">="}:return a>=b
            if op in {"lt","<"}:return a<b
            if op in {"lte","<="}:return a<=b
        except Exception:
            return False

    if op == "contains_recursive":
        return _contains_recursive(left, right)
    if op == "not_contains_recursive":
        return not _contains_recursive(left, right)
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
    if condition.get("right_type") in {"variable","expression"}:
        return resolve_expression(condition.get("right"),ctx)
    if condition.get("right_type") == "typed":
        return condition.get("right")
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

    left_expr=node.get("left","")
    left=resolve_expression(left_expr,ctx)
    previous=resolve_expression(previous_expression(left_expr),ctx)
    right=resolve_operand(node,ctx)
    return compare(str(node.get("operator","eq")),left,right,previous)

