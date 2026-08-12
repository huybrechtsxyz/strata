"""Human login session management (ADR-0067 Step 8).

Created by `/auth/callback` only when the identity provider actually returns a
`refresh_token` (some don't, absent an `offline_access`-equivalent scope grant)
— a session row is the persistent half of the hybrid model described in
ADR-0067's "Session model" section; the stateless access token remains the
per-request credential, verified with no database hit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def create_session(engine: "Engine", subject: str, encrypted_refresh_token: str, email: Optional[str] = None) -> str:
    """Persist a new session row and return its `session_id`."""
    import uuid

    from strata.server.db.schema import sessions

    session_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            sessions.insert().values(
                session_id=session_id,
                subject=subject,
                email=email,
                encrypted_refresh_token=encrypted_refresh_token,
            )
        )
    return session_id


def get_session(engine: "Engine", session_id: str) -> Optional[Dict[str, Any]]:
    """Return the raw session row (including the encrypted refresh token) if active, else None.

    Returns None both when the session doesn't exist and when it has been revoked —
    callers should not distinguish the two in an error message (matches `verify_token`'s
    own "invalid or revoked" framing for the same reason: no oracle for enumeration).
    """
    from strata.server.db.schema import sessions

    query = sessions.select().where(sessions.c.session_id == session_id).where(sessions.c.revoked_at.is_(None))
    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()
    return dict(row) if row else None


def list_sessions(engine: "Engine") -> List[Dict[str, Any]]:
    """List all sessions (never the encrypted refresh token) for admin introspection."""
    from strata.server.db.schema import sessions

    query = sessions.select().order_by(sessions.c.created_at.desc())
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [
        {
            "session_id": row["session_id"],
            "subject": row["subject"],
            "email": row["email"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "last_refreshed_at": row["last_refreshed_at"].isoformat() if row["last_refreshed_at"] else None,
            "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
        }
        for row in rows
    ]


def revoke_session(engine: "Engine", session_id: str) -> bool:
    """Revoke a session by id. Returns True if a matching, still-active session was found."""
    from strata.server.db.schema import sessions

    with engine.begin() as conn:
        result = conn.execute(
            sessions.update()
            .where(sessions.c.session_id == session_id)
            .where(sessions.c.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        return bool(result.rowcount)


def touch_session(engine: "Engine", session_id: str, encrypted_refresh_token: Optional[str] = None) -> None:
    """Update `last_refreshed_at`, and replace the stored refresh token if the IdP rotated it."""
    from strata.server.db.schema import sessions

    values: Dict[str, Any] = {"last_refreshed_at": datetime.now(timezone.utc)}
    if encrypted_refresh_token is not None:
        values["encrypted_refresh_token"] = encrypted_refresh_token

    with engine.begin() as conn:
        conn.execute(sessions.update().where(sessions.c.session_id == session_id).values(**values))
