"""Portable event-store schema for the strata state-service (ADR-0065 Step 2.2).

Defined once, in SQLAlchemy Core (not the ORM layer), and rendered per-dialect by
SQLAlchemy itself — avoids maintaining three hand-written, dialect-specific SQL
files (sqlite/postgresql/mssql) for one logical schema.

Requires the optional dependency: pip install xyz-strata[server]
"""

from __future__ import annotations

try:
    from sqlalchemy import JSON, Column, DateTime, Index, MetaData, PrimaryKeyConstraint, String, Table, func
except ImportError as _exc:
    raise ImportError(
        "The 'sqlalchemy' package is required for the strata state-service server.\n"
        "Install it with: pip install xyz-strata[server]"
    ) from _exc

metadata = MetaData()

events = Table(
    "events",
    metadata,
    Column("execution_id", String, nullable=False),
    # the CloudEvents `type` string, e.g. "xyz.huybrechts.strata.deployment.completed"
    # (ADR-0066's closed event-type enum) — not the original 5 "artifact kind" labels
    Column("record_type", String, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # promoted dimensions: bounded cardinality, indexed, safe as labels (ADR-0064's label_safe set)
    Column("deployment", String),
    Column("workspace", String),
    Column("environment", String),
    Column("tenant", String),
    Column("ring", String),
    Column("action", String),
    Column("outcome", String),
    Column("strata_version", String),
    Column("payload", JSON, nullable=False),  # the complete record, verbatim
    PrimaryKeyConstraint("execution_id", "record_type"),
)

Index("idx_events_recorded_at", events.c.recorded_at.desc())
Index("idx_events_slice", events.c.deployment, events.c.environment, events.c.recorded_at.desc())

# Per-workspace ingest token credentials (ADR-0065 Step 2.4). Unlike `events`,
# this table is mutable (revocation) — it's access-control state, not an
# audit fact, so it doesn't fall under the append-only principle above.
tokens = Table(
    "tokens",
    metadata,
    Column("token_id", String, primary_key=True),  # non-secret identifier, safe to display/log
    Column("token_hash", String, nullable=False),  # sha256(secret) — the secret itself is never stored
    Column("workspace", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("revoked_at", DateTime(timezone=True), nullable=True),  # NULL = active
)

Index("idx_tokens_hash", tokens.c.token_hash, unique=True)

# Human login sessions (ADR-0067 Step 8). Unlike `tokens`, `encrypted_refresh_token`
# must be recoverable (the server presents it back to the IdP on every refresh), so
# it is encrypted at rest, not hashed — a hash cannot be reversed to re-present the
# credential, and equality comparison (what a hash is for) is not what refresh needs.
sessions = Table(
    "sessions",
    metadata,
    Column("session_id", String, primary_key=True),  # opaque UUID; the client holds this, never the refresh token
    Column("subject", String, nullable=False),  # the id_token's `sub` claim
    Column("email", String, nullable=True),
    Column("encrypted_refresh_token", String, nullable=False),  # JWE compact serialization
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_refreshed_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),  # NULL = active
)

Index("idx_sessions_subject", sessions.c.subject)
