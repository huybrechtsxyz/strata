#!/usr/bin/env python3
"""Condition expressions for diagram highlight rules (ADR-0034).

``spec.style.highlight[].condition`` is a deliberately tiny grammar rather than
raw Jinja::

    <field> == <value>
    <field> != <value>
    <field> in [<value>, <value>, ...]

A closed grammar means a typo produces a validation error naming the problem,
instead of a Jinja expression that silently evaluates to false and leaves the
author wondering why nothing is highlighted. It also keeps authored values out
of the expression namespace: every value is emitted as a quoted literal, never
interpolated as code.
"""

import re
from typing import List

# A field is a dotted attribute path on the node (e.g. 'status', 'metadata.role').
# Anchored and character-restricted so nothing else can reach the expression.
_FIELD = r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*"
_CONDITION_RE = re.compile(rf"^\s*(?P<field>{_FIELD})\s+(?P<op>==|!=|in)\s+(?P<value>.+?)\s*$")

SUPPORTED_OPERATORS = ("==", "!=", "in")


class ConditionError(ValueError):
    """Raised when a highlight condition cannot be parsed."""


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _split_list(value: str) -> List[str]:
    inner = value.strip()[1:-1]
    return [_strip_quotes(part) for part in inner.split(",") if part.strip()]


def parse_condition(condition: str, node_var: str = "n") -> str:
    """Translate a highlight condition into a Jinja expression over *node_var*.

    Values are always emitted as quoted literals, so an authored value can never
    become part of the expression itself.

    Args:
        condition: Condition source, e.g. ``status == disabled``.
        node_var: Name of the loop variable the expression is evaluated against.

    Returns:
        A Jinja expression, e.g. ``n.status == 'disabled'``.

    Raises:
        ConditionError: If *condition* does not match the grammar.
    """
    match = _CONDITION_RE.match(condition or "")
    if not match:
        raise ConditionError(
            f"Cannot parse condition '{condition}'. Expected '<field> <op> <value>' "
            f"where <op> is one of {list(SUPPORTED_OPERATORS)}, "
            f"e.g. 'status == disabled' or 'severity in [critical, high]'."
        )

    field = match.group("field")
    operator = match.group("op")
    raw_value = match.group("value").strip()

    if operator == "in":
        if not (raw_value.startswith("[") and raw_value.endswith("]")):
            raise ConditionError(
                f"Condition '{condition}' uses 'in', which needs a bracketed list, e.g. 'severity in [critical, high]'."
            )
        values = _split_list(raw_value)
        if not values:
            raise ConditionError(f"Condition '{condition}' has an empty list — it can never match.")
        rendered = ", ".join(repr(value) for value in values)
        return f"{node_var}.{field} in [{rendered}]"

    if raw_value.startswith("["):
        raise ConditionError(f"Condition '{condition}' compares a list with '{operator}'. Use 'in' to test membership.")
    return f"{node_var}.{field} {operator} {_strip_quotes(raw_value)!r}"
