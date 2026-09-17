"""Shared missing-data-handling mechanism for policies and gates (ADR-0082).

Both the policy engine (``BasePolicy``/``PolicyModel``) and the gate engine
(``GateConditionEvaluator``/``DeploymentGateModel``) need to answer the same
question — "what should happen when a guardrail is actively configured but the
data it needs to evaluate was never produced?" — but they have different result
shapes (``PolicyResult`` vs. a gate-trigger decision) and different resolution
paths (policy enforcement vs. human approval).

Rather than reimplementing the same three-way decision twice, :func:`resolve_missing_data`
is the single, engine-agnostic function both call. It has no dependency on either
engine's types (no import of ``PolicyResult``, ``GateContext``, ``PolicyModel``, or
``DeploymentGateModel``) — each caller translates its neutral :class:`MissingDataOutcome`
into its own result type at the call site.

See ``docs/decisions/0082-uniform-missing-data-handling-for-policies-and-gates.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MissingDataPolicy(str, Enum):
    """How a guardrail (policy or gate) should behave when its required input data
    is missing — as opposed to the guardrail simply not applying in this context.

    See the "What counts as 'missing data' vs. 'not applicable'" section of
    ADR-0082 — this enum only governs the former.
    """

    SKIP = "skip"
    """Today's behavior everywhere: treat as not-applicable, pass silently."""

    WARN = "warn"
    """Treat as not-applicable, but surface a visible warning (``PolicyResult.warnings``
    / gate manifest) — never just a debug-level log line."""

    BLOCK = "block"
    """Treat missing data as a failure signal: a policy reports ``passed=False``
    with a violation (enforced per its configured ``enforcement`` level); a gate
    condition counts as MATCHED — the gate fires and requires its configured
    resolution (e.g. human approval) exactly as if the real threshold had been
    exceeded."""


@dataclass(frozen=True)
class MissingDataOutcome:
    """Neutral result of resolving one missing-data event against a configured
    :class:`MissingDataPolicy`.

    Engine-agnostic and immutable — a pure value handed across two otherwise
    unrelated subsystems (policies, gates). Each caller translates it into its own
    result type; nothing below this layer is reimplemented per engine.
    """

    blocked: bool
    """``True`` => the caller should treat this as a failure/trigger signal."""

    warning: Optional[str]
    """Non-``None`` => must be surfaced to the operator, never left at debug-only
    logging. ``None`` when ``blocked`` is ``True`` (the block itself is the surfaced
    signal — a redundant warning alongside a denial/gate-trigger that already
    explains itself would be noise) or when the mode is ``SKIP``."""

    reason: str
    """The original reason passed in, always populated regardless of outcome —
    for audit/detail fields either way."""


def resolve_missing_data(mode: MissingDataPolicy, reason: str) -> MissingDataOutcome:
    """Resolve a single missing-data event against a configured :class:`MissingDataPolicy`.

    This is the one shared decision function both ``BasePolicy`` and
    ``GateConditionEvaluator`` call (ADR-0082) — it is pure ``(mode, reason) ->
    outcome`` with no engine-specific dependencies, which is what makes it safely
    importable from both ``strata.validators.policies.base_policy`` and
    ``strata.controllers.gate_controller`` without either depending on the other.

    Args:
        mode: The configured ``on_missing_data`` behavior.
        reason: Human-readable explanation of what data is missing (e.g.
            ``"cost.json not found"``), echoed back on the outcome for audit/detail
            fields regardless of what was decided.

    Returns:
        A :class:`MissingDataOutcome` describing whether this should count as a
        failure/trigger (``blocked``) and, if not blocked, whether a warning should
        be surfaced.
    """
    if mode == MissingDataPolicy.BLOCK:
        return MissingDataOutcome(blocked=True, warning=None, reason=reason)
    if mode == MissingDataPolicy.WARN:
        return MissingDataOutcome(blocked=False, warning=reason, reason=reason)
    return MissingDataOutcome(blocked=False, warning=None, reason=reason)
