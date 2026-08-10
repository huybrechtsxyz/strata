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
    # deploy-log | deployment-manifest | deployment-metrics | drift-history | cost-history
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
