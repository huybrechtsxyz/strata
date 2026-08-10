"""Per-workspace ingest token management (ADR-0065 Step 2.4).

Tokens are high-entropy random secrets (``secrets.token_urlsafe``, via the
existing `strata.utils.secret_generator`) — only their SHA-256 hash is ever
persisted. SHA-256 (not bcrypt/argon2) is correct here specifically because
the secret is machine-generated with ~256 bits of entropy, not a human-chosen
password; those slower hashes exist to resist brute-forcing low-entropy
secrets, which doesn't apply here. Verification is a DB equality lookup on
the hash, not a raw string comparison, so there is no timing-attack surface
to add `hmac.compare_digest` for.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def _hash_token(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_token(engine: "Engine", workspace: str) -> Dict[str, str]:
    """Generate and persist a new ingest token for *workspace*.

    Returns ``{"token_id": ..., "token": ...}`` — the secret (`token`) is
    returned exactly once here; only its hash is ever stored.
    """
    import uuid

    from strata.server.db.schema import tokens
    from strata.utils.secret_generator import generate_secret

    token_id = str(uuid.uuid4())
    secret = generate_secret("urlsafe", 32)

    with engine.begin() as conn:
        conn.execute(
            tokens.insert().values(
                token_id=token_id,
                token_hash=_hash_token(secret),
                workspace=workspace,
            )
        )

    return {"token_id": token_id, "token": secret}


def list_tokens(engine: "Engine", workspace: Optional[str] = None) -> List[Dict[str, Any]]:
    """List tokens (never the hash or secret), optionally filtered by workspace."""
    from strata.server.db.schema import tokens

    query = tokens.select().order_by(tokens.c.created_at.desc())
    if workspace:
        query = query.where(tokens.c.workspace == workspace)

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [
        {
            "token_id": row["token_id"],
            "workspace": row["workspace"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
        }
        for row in rows
    ]


def revoke_token(engine: "Engine", token_id: str) -> bool:
    """Revoke a token by id. Returns True if a matching, still-active token was found."""
    from strata.server.db.schema import tokens

    with engine.begin() as conn:
        result = conn.execute(
            tokens.update()
            .where(tokens.c.token_id == token_id)
            .where(tokens.c.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        return bool(result.rowcount)


def verify_token(engine: "Engine", secret: str) -> Optional[str]:
    """Return the workspace name for an active token matching *secret*, or None."""
    from strata.server.db.schema import tokens

    token_hash = _hash_token(secret)
    query = tokens.select().where(tokens.c.token_hash == token_hash).where(tokens.c.revoked_at.is_(None))

    with engine.connect() as conn:
        row = conn.execute(query).mappings().first()

    return row["workspace"] if row else None
