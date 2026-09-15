"""RBAC data model — tiers, capabilities, principals, and resolved access
(ADR-0067 Step 9).

Deliberately framework-free and DB-free (no ``fastapi``/``sqlalchemy`` import here,
mirroring `server/config.py`'s own reasoning) — the ordering rules and the two-axis
access model are pure logic, unit-testable without either optional dependency. The
`role_bindings` table lives in `db/schema.py`; reading/writing it lives in
`db/rbac.py`; wiring this into FastAPI dependencies lives in `routes/security.py`.

Two independent axes, not one flat list (see ADR-0067's "Step 9 design" section):

- **Tier** — an ordered hierarchy, ``viewer < approver < contributor < admin``. Each
  tier includes every permission of every tier below it.
- **Capabilities** — orthogonal to tier. Today the only defined capability is
  ``deployer`` ("may trigger a deployment run against this workspace/environment").
  Not implied by any tier except ``admin``, which implies every capability that
  exists — an admin who cannot deploy anything is a confusing exception to "full
  control," and RBAC administration itself already requires that trust level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple


class Tier(IntEnum):
    """Ordered access tier — higher values include every permission of lower ones."""

    VIEWER = 1
    APPROVER = 2
    CONTRIBUTOR = 3
    ADMIN = 4


_TIER_BY_NAME: Dict[str, Tier] = {tier.name.lower(): tier for tier in Tier}

CAPABILITY_DEPLOYER = "deployer"
VALID_CAPABILITIES = frozenset({CAPABILITY_DEPLOYER})

VALID_SUBJECT_TYPES = frozenset({"user", "group"})


def parse_tier(name: str) -> Tier:
    """Parse a tier name (case-insensitive) into a `Tier`. Raises `ValueError` if unknown."""
    try:
        return _TIER_BY_NAME[name.lower()]
    except KeyError:
        valid = ", ".join(sorted(_TIER_BY_NAME))
        raise ValueError(f"unknown tier '{name}' (expected one of: {valid})") from None


@dataclass(frozen=True)
class Principal:
    """One authenticated caller, normalized regardless of how it authenticated.

    `subject` is the primary identity string — the session's `sub` claim for a human,
    or the matched `TrustedIssuer.subject_claim` value for an M2M caller (see
    `m2m_verifier.py`'s `_subject` claim). `groups` is only ever populated for human
    sessions whose IdP includes a group/team claim; M2M callers carry no separate
    group list because their `subject` value already *is* the group-shaped claim
    (e.g. a GitHub Actions token's `repository`) — see `candidate_bindings()` below.
    """

    subject: str
    email: str = ""
    groups: Tuple[str, ...] = ()
    auth_method: str = "session"  # "session" | "m2m"

    def candidate_bindings(self) -> List[Tuple[str, str]]:
        """Return every `(subject_type, subject)` pair this principal could match.

        M2M callers match only as `"group"` — their subject value identifies a class
        of callers (e.g. "any workflow run from this repository"), not an individual,
        the same reasoning `role_bindings`' own docstring gives for sharing one
        `subject_type` between IdP group claims and M2M subject claims. Human callers
        match as `"user"` on both `sub` and `email` (a binding may reasonably target
        either), plus `"group"` for every IdP group/team claim value they carry.
        """
        if self.auth_method == "m2m":
            return [("group", self.subject)] if self.subject else []

        candidates: List[Tuple[str, str]] = []
        if self.subject:
            candidates.append(("user", self.subject))
        if self.email and self.email != self.subject:
            candidates.append(("user", self.email))
        candidates.extend(("group", group) for group in self.groups if group)
        return candidates


@dataclass(frozen=True)
class ResolvedAccess:
    """The union of every role binding matching one principal at one scope."""

    tier: Optional[Tier] = None
    capabilities: FrozenSet[str] = field(default_factory=frozenset)

    def has_tier(self, min_tier: Tier) -> bool:
        return self.tier is not None and self.tier >= min_tier

    def has_capability(self, name: str) -> bool:
        if self.tier == Tier.ADMIN:
            return True  # admin implies every capability — see the module docstring
        return name in self.capabilities


def union_access(tiers: Sequence[Tier], capabilities: Sequence[str]) -> ResolvedAccess:
    """Combine every matching binding's tier/capabilities into one `ResolvedAccess`.

    Deliberately a plain union with no "most specific binding wins" precedence rule —
    a principal's effective access is everything explicitly granted to it or its
    groups, the option that needs no tie-breaking logic to reason about or test.
    """
    resolved_tier = max(tiers) if tiers else None
    resolved_capabilities = set(capabilities)
    if resolved_tier == Tier.ADMIN:
        resolved_capabilities.add(CAPABILITY_DEPLOYER)
    return ResolvedAccess(tier=resolved_tier, capabilities=frozenset(resolved_capabilities))
