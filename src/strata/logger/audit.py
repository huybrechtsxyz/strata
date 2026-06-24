#!/usr/bin/env python3
"""
Dedicated audit logger for strata.

Writes audit-relevant events (user actions with observable side-effects) to a
separate NDJSON log file.  Completely isolated from the application structlog
pipeline — uses its own stdlib Logger with ``propagate=False``.

Audit events answer: "What did the user do, when, and what was the outcome?"

Auditable actions include:
    - Solution lifecycle (init, clean, export)
    - Repository management (add, remove, sync)
    - Profile management (add, remove, activate)
    - Build / deploy executions
    - Validation runs
    - Configuration changes (config set/unset)
    - External integration invocations (git, terraform, docker)
"""

import json
import logging
import logging.handlers
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from strata.utils.config import SOLUTION_AUDIT_LOG_FILE, SOLUTION_DIR

_audit_logger: Optional[logging.Logger] = None


def configure_audit_log(
    log_path: str = f"{SOLUTION_DIR}/{SOLUTION_AUDIT_LOG_FILE}",
    rotation: str = "size",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
    date_suffix: str = "%Y%m%d",
) -> None:
    """
    Configure the dedicated audit log sink.

    Creates a rotating file handler that writes NDJSON entries.  The logger
    uses ``propagate=False`` to ensure audit entries never leak into the
    application log stream.

    Args:
        log_path: Path to the audit log file (relative to work_path or absolute).
        rotation: Rotation strategy — ``"size"`` (RotatingFileHandler) or
            ``"daily"`` (TimedRotatingFileHandler, rotates at midnight UTC).
        max_bytes: Maximum file size before rotation (default 5 MB).
            Only used when ``rotation="size"``.
        backup_count: Number of rotated backups to keep (default 3).
        date_suffix: strftime pattern for daily-rotated backup filenames
            (default ``"%Y%m%d"``).  Only used when ``rotation="daily"``.
    """
    global _audit_logger
    _audit_logger = logging.getLogger("strata.audit")
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False

    # Remove existing handlers to avoid duplicates on reconfigure
    for handler in _audit_logger.handlers[:]:
        handler.close()
        _audit_logger.removeHandler(handler)

    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    file_handler: logging.Handler
    if rotation == "daily":
        timed = logging.handlers.TimedRotatingFileHandler(
            log_path, when="midnight", backupCount=backup_count, encoding="utf-8", utc=True
        )
        # Override suffix to compact yyyymmdd format (stdlib default is %Y-%m-%d)
        timed.suffix = date_suffix  # type: ignore[attr-defined]
        # Update extMatch so getFilesToDelete() recognises the new suffix pattern
        safe_pattern = re.sub(r"%[YmdHMS]", r"\\d+", re.escape(date_suffix))
        timed.extMatch = re.compile(r"\." + safe_pattern + r"$", re.ASCII)  # type: ignore[attr-defined]
        file_handler = timed
    else:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )

    file_handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(file_handler)


def audit(
    action: str,
    *,
    outcome: str = "success",
    target: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """
    Emit a single audit log entry.

    Silent no-op when the audit logger has not been configured (e.g. in tests
    or when work_path is not yet resolved).

    Args:
        action: The action performed (e.g. "sln.init", "deploy.run", "repo.add").
        outcome: Result of the action — "success", "failure", or "skipped".
        target: The primary target of the action (e.g. file path, repo name).
        detail: Additional structured context for the entry.

    Example::

        audit("sln.init", target="my-project", detail={"work_path": "/home/user/project"})
        audit("validate", outcome="failure", target="stack/vm-infra.yaml", detail={"error_count": 3})
        audit("integration.invoked", target="terraform plan", detail={"exit_code": 0, "duration_ms": 4200})
    """
    if _audit_logger is None:
        return

    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "action": action,
        "outcome": outcome,
    }
    if target:
        entry["target"] = target
    if detail:
        entry["detail"] = detail

    _audit_logger.info(json.dumps(entry, default=str))


def is_audit_configured() -> bool:
    """Return True if the audit logger has been configured."""
    return _audit_logger is not None


def shutdown_audit() -> None:
    """Flush and close the audit logger handlers."""
    if _audit_logger is not None:
        for handler in _audit_logger.handlers[:]:
            handler.flush()
            handler.close()
