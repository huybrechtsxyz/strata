"""Role-binding persistence and access resolution (ADR-0067 Step 9).

Mirrors `db/tokens.py`'s shape (create/list/revoke), plus `resolve_access()` — the
one place a principal's candidate `(subject_type, subject)` pairs are turned into a
`ResolvedAccess` by reading every active binding and unioning the ones that match,
in Python rather than a cross-dialect-fragile SQL `OR` — see `rbac.py`'s
`union_access()` for why a plain union, with no precedence rule, is the resolution
strategy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from strata.server.auth.rbac import VALID_CAPABILITIES, VALID_SUBJECT_TYPES, ResolvedAccess, parse_tier, union_access

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def create_binding(
    engine: "Engine",
    subject_type: str,
    subject: str,
    tier: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
    workspace: Optional[str] = None,
    environment: Optional[str] = None,
    created_by: Optional[str] = None,
) -> str:
    """Create and persist a new role binding. Returns the new `binding_id`.

    Raises `ValueError` for an unknown `subject_type`/capability, an unparseable
    `tier`, or a binding that would grant neither a tier nor any capability — a
    binding granting nothing is meaningless and is rejected here, not silently
    accepted.
    """
    import uuid

    from strata.server.db.schema import role_bindings

    if subject_type not in VALID_SUBJECT_TYPES:
        valid = ", ".join(sorted(VALID_SUBJECT_TYPES))
        raise ValueError(f"subject_type must be one of: {valid}")

    parsed_tier = parse_tier(tier) if tier else None

    resolved_capabilities = list(capabilities or [])
    for capability in resolved_capabilities:
        if capability not in VALID_CAPABILITIES:
            valid = ", ".join(sorted(VALID_CAPABILITIES))
            raise ValueError(f"unknown capability '{capability}' (expected one of: {valid})")

    if parsed_tier is None and not resolved_capabilities:
        raise ValueError("a role binding must grant a tier and/or at least one capability")

    binding_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            role_bindings.insert().values(
                binding_id=binding_id,
                subject_type=subject_type,
                subject=subject,
                workspace=workspace,
                environment=environment,
                tier=parsed_tier.name.lower() if parsed_tier else None,
                capabilities=resolved_capabilities,
                created_by=created_by,
            )
        )
    return binding_id


def list_bindings(engine: "Engine", workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """List role bindings, optionally filtered by workspace (exact match only, not wildcard)."""
    from strata.server.db.schema import role_bindings

    query = role_bindings.select().order_by(role_bindings.c.created_at.desc())
    if workspace:
        query = query.where(role_bindings.c.workspace == workspace)

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [
        {
            "binding_id": row["binding_id"],
            "subject_type": row["subject_type"],
            "subject": row["subject"],
            "workspace": row["workspace"],
            "environment": row["environment"],
            "tier": row["tier"],
            "capabilities": row["capabilities"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "created_by": row["created_by"],
            "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
        }
        for row in rows
    ]


def revoke_binding(engine: "Engine", binding_id: str) -> bool:
    """Revoke a binding by id. Returns True if a matching, still-active binding was found."""
    from strata.server.db.schema import role_bindings

    with engine.begin() as conn:
        result = conn.execute(
            role_bindings.update()
            .where(role_bindings.c.binding_id == binding_id)
            .where(role_bindings.c.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        return bool(result.rowcount)


def _scope_matches(
    binding_workspace: Optional[str],
    binding_environment: Optional[str],
    workspace: Optional[str],
    environment: Optional[str],
) -> bool:
    """True if a binding's scope applies to the requested (workspace, environment).

    A global check (`workspace is None`, e.g. the RBAC management routes themselves)
    is deliberately satisfied only by an equally-global binding (both `NULL`) — a
    binding scoped to one workspace must never satisfy a platform-wide check just
    because `NULL` is also used as "wildcard" in the scoped case below.
    """
    if workspace is None:
        return binding_workspace is None and binding_environment is None
    if binding_workspace is not None and binding_workspace != workspace:
        return False
    if binding_environment is not None and binding_environment != environment:
        return False
    return True


def resolve_access(
    engine: "Engine",
    candidates: List[Tuple[str, str]],
    workspace: Optional[str] = None,
    environment: Optional[str] = None,
) -> ResolvedAccess:
    """Return the union of every active binding matching *candidates* at this scope.

    *candidates* is a principal's `Principal.candidate_bindings()` — a list of
    `(subject_type, subject)` pairs to match against. Filters by `subject` in SQL
    (cheap, portable) then matches `subject_type` and scope in Python, avoiding a
    cross-dialect-fragile `OR` of tuple equality and nullable-column comparisons.
    """
    from strata.server.db.schema import role_bindings

    if not candidates:
        return ResolvedAccess()

    subjects = {subject for _, subject in candidates}
    query = (
        role_bindings.select().where(role_bindings.c.revoked_at.is_(None)).where(role_bindings.c.subject.in_(subjects))
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    candidate_set = set(candidates)
    matched_tiers = []
    matched_capabilities: List[str] = []
    for row in rows:
        if (row["subject_type"], row["subject"]) not in candidate_set:
            continue
        if not _scope_matches(row["workspace"], row["environment"], workspace, environment):
            continue
        if row["tier"]:
            matched_tiers.append(parse_tier(row["tier"]))
        matched_capabilities.extend(row["capabilities"] or [])

    return union_access(matched_tiers, matched_capabilities)
