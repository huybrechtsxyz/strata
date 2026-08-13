#!/usr/bin/env python3
"""Design-system colour tokens for diagram rendering (ADR-0034).

Diagram definitions name a *token*, never a raw colour, so the meaning of a
colour is decided once here rather than per diagram. Severity ramps share one
5-step scale, so a user who learns "red = critical" in the drift diagram does
not have to relearn it for the CVE diagram.

CLI output carries hex, because it targets Mermaid Live and GitHub-rendered
markdown, which have no VS Code theme to read from. The webview renderer maps
these same token names onto ``--vscode-charts-*`` CSS variables instead, which
is what makes a committed diagram readable on a light, dark, or high-contrast
theme.
"""

from typing import Dict, Optional, Tuple

# Shared 5-step severity ramp: grey -> blue -> amber -> orange -> red.
_GREY = ("#e2e3e5", "#6c757d")
_BLUE = ("#dbeafe", "#2563eb")
_AMBER = ("#fff3cd", "#ffc107")
_ORANGE = ("#ffe5d0", "#fd7e14")
_RED = ("#f8d7da", "#dc3545")
_GREEN = ("#d4edda", "#28a745")

# Token name -> (fill, stroke).
#
# Grouped by semantic domain. Domains are orthogonal: 'status' describes health,
# 'kind' describes taxonomy, and a diagram may colour by either.
DESIGN_TOKENS: Dict[str, Tuple[str, str]] = {
    # Validity status
    "valid": _GREEN,
    "invalid": _AMBER,
    "missing": _RED,
    "external": _GREY,
    "orphan": ("#f5f5f5", "#adb5bd"),
    # Severity — shared by drift and CVE
    "info": _GREY,
    "unknown": _GREY,
    "low": _BLUE,
    "medium": _AMBER,
    "high": _ORANGE,
    "critical": _RED,
    # Policy enforcement — the low/medium/high slice of the same ramp
    "audit": _GREY,
    "warn": _AMBER,
    "deny": _RED,
    # Lock and promotion state
    "unlocked": _GREEN,
    "locked": _BLUE,
    "held": _AMBER,
    "expired": _GREY,
    # Health check result
    "passing": _GREEN,
    "degraded": _AMBER,
    "failing": _RED,
    # Deployment outcome
    "success": _GREEN,
    "partial": _AMBER,
    "failed": _RED,
    # Promotion record outcome (PromotionOutcome) and gate result — aliases of the
    # same ramp so a template doesn't need to translate the model's own enum
    # values before coloring with them.
    "completed": _GREEN,
    "rolled-back": _RED,
    "passed": _GREEN,
    # Taxonomy / kind — categorical, no ordering
    "resource": _BLUE,
    "module": ("#fef3c7", "#d97706"),
    "namespace": ("#d1fae5", "#059669"),
    "network": ("#e0e7ff", "#4f46e5"),
    "disabled": _GREY,
    "dangling": _RED,
    # Value resolution reachability — whether reading a declared value would need
    # a live store contact (vault, bitwarden, ...) or resolves from the YAML/env
    # alone. Informational, not a health signal, so neither end is red/green.
    "offline": _GREY,
    "live": _BLUE,
    # Drift history — a resource address relative to its last known-good state.
    "drifting": _ORANGE,
    "resolved": _GREEN,
    "acknowledged": _GREY,
    # State-locking policy — declared, not a live "is it held right now" check.
    "enabled": _BLUE,
    # Deploy output keys — whether the value would need masking if resolved,
    # never the value itself.
    "sensitive": _AMBER,
    "available": _GREEN,
    # Neutral fallback for anything unrecognised
    "neutral": _GREY,
}

DEFAULT_TOKEN = "neutral"


def resolve_token(name: str, part: Optional[str] = None) -> str:
    """Resolve a design-system token to a Mermaid ``classDef`` body or one colour.

    Args:
        name: Token name (e.g. ``critical``). Unknown names fall back to
            ``neutral`` rather than raising — a diagram colouring by a field
            whose values were not anticipated should still render.
        part: ``fill`` or ``stroke`` to get a single hex value. Omit for the
            full ``fill:#...,stroke:#...`` body.

    Returns:
        ``"fill:#xxxxxx,stroke:#yyyyyy"``, or a single hex value when *part* is given.

    Raises:
        ValueError: If *part* is neither ``fill`` nor ``stroke``.
    """
    fill, stroke = DESIGN_TOKENS.get(str(name).strip().lower(), DESIGN_TOKENS[DEFAULT_TOKEN])
    if part is None:
        return f"fill:{fill},stroke:{stroke}"
    if part == "fill":
        return fill
    if part == "stroke":
        return stroke
    raise ValueError(f"Unknown token part '{part}'. Expected 'fill', 'stroke', or nothing.")


def mermaid_escape(value: object) -> str:
    """Escape a string for safe use inside a quoted Mermaid node label.

    Mermaid closes a quoted label on the first unescaped ``"``, so an unescaped
    quote in a description silently truncates the diagram. Newlines become
    ``<br/>`` rather than being stripped, since multi-line labels are the common
    reason a label contains one.
    """
    text = str(value)
    for char, entity in (('"', "#quot;"), ("<", "#lt;"), (">", "#gt;")):
        text = text.replace(char, entity)
    return text.replace("\r\n", "<br/>").replace("\n", "<br/>").replace("\r", "<br/>")
