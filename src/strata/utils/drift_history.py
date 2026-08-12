"""Drift detection history store.

Persists per-deployment drift history at ``.strata/drift/{deployment_name}.drift.json``.
Each run appends a new snapshot entry.  History is never overwritten — entries accumulate
so operators can ask "how long has this resource been drifting?".

History is gitignored (``DriftHistoryStore.ensure_gitignore`` adds the path).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.logger import get_logger
from strata.utils.config import SOLUTION_DIR, SOLUTION_DRIFT_DIR, get_drift_dir

logger = get_logger(__name__)


class DriftHistoryStore:
    """Load, update, and persist drift history for a deployment.

    History file structure::

        {
          "deployment": "my-deployment",
          "entries": {
            "azurerm_network_security_rule.allow_ssh": {
              "first_detected": "2026-07-01T12:00:00Z",
              "last_detected": "2026-07-06T09:00:00Z",
              "consecutive_checks": 3,
              "acknowledged": false
            },
            ...
          },
          "runs": [
            {
              "checked_at": "2026-07-06T09:00:00Z",
              "addresses": ["azurerm_network_security_rule.allow_ssh"]
            },
            ...
          ]
        }
    """

    def __init__(self, work_path: Path, deployment_name: str) -> None:
        self._work_path = work_path
        self._deployment_name = deployment_name
        self._history_dir = get_drift_dir(work_path)
        self._history_file = self._history_dir / f"{deployment_name}.drift.json"
        self._data: Dict[str, Any] = {}
        self._loaded = False

    @property
    def history_dir(self) -> Path:
        """Directory the history file lives in — the local base for durable push (ADR-0065)."""
        return self._history_dir

    @property
    def history_file(self) -> Path:
        """Absolute path to this deployment's drift-history file."""
        return self._history_file

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load existing history from disk (no-op if file does not exist)."""
        if not self._history_file.exists():
            self._data = {
                "deployment": self._deployment_name,
                "entries": {},
                "runs": [],
            }
            self._loaded = True
            return

        try:
            raw = self._history_file.read_text(encoding="utf-8")
            self._data = json.loads(raw)
            self._loaded = True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "drift_history_load_failed",
                path=str(self._history_file),
                error=str(exc),
            )
            self._data = {
                "deployment": self._deployment_name,
                "entries": {},
                "runs": [],
            }
            self._loaded = True

    def record_run(self, checked_at: str, drifted_addresses: List[str]) -> None:
        """Record one drift run, updating per-address history.

        ``checked_at`` is an ISO 8601 timestamp (UTC).
        ``drifted_addresses`` is the list of resource addresses with drift this run.
        Addresses no longer drifting have their consecutive_checks reset.
        """
        if not self._loaded:
            self.load()

        entries: Dict[str, Any] = self._data.setdefault("entries", {})
        runs: List[Any] = self._data.setdefault("runs", [])

        # Update per-address tracking
        seen = set(drifted_addresses)
        for address in drifted_addresses:
            if address not in entries:
                entries[address] = {
                    "first_detected": checked_at,
                    "last_detected": checked_at,
                    "consecutive_checks": 1,
                    "acknowledged": False,
                }
            else:
                entries[address]["last_detected"] = checked_at
                entries[address]["consecutive_checks"] = entries[address].get("consecutive_checks", 0) + 1

        # Reset consecutive_checks for addresses no longer drifting
        for address, entry in entries.items():
            if address not in seen and not entry.get("acknowledged", False):
                entries[address]["consecutive_checks"] = 0

        # Append run record
        runs.append(
            {
                "checked_at": checked_at,
                "addresses": drifted_addresses,
            }
        )

    def get_entry(self, address: str) -> Optional[Dict[str, Any]]:
        """Return the history entry for a resource address, or None."""
        if not self._loaded:
            self.load()
        return self._data.get("entries", {}).get(address)

    def is_acknowledged(self, address: str) -> bool:
        """Return True if the address has been acknowledged."""
        entry = self.get_entry(address)
        return bool(entry and entry.get("acknowledged", False))

    def acknowledge(self, address: str, reason: str = "", acknowledged_by: str = "") -> bool:
        """Mark a resource address as acknowledged, suppressing it from future drift reports.

        If no prior history entry exists for the address a placeholder entry is created so
        the acknowledgement is persisted even if the address has not been seen in a run yet.

        Returns True always (the operation is idempotent).
        """
        if not self._loaded:
            self.load()
        entries: Dict[str, Any] = self._data.setdefault("entries", {})
        now = self.now_iso()
        if address not in entries:
            entries[address] = {
                "first_detected": now,
                "last_detected": now,
                "consecutive_checks": 0,
                "acknowledged": True,
                "acknowledged_reason": reason,
                "acknowledged_by": acknowledged_by,
                "acknowledged_at": now,
            }
        else:
            entries[address]["acknowledged"] = True
            entries[address]["acknowledged_reason"] = reason
            entries[address]["acknowledged_by"] = acknowledged_by
            entries[address]["acknowledged_at"] = now
        return True

    def remove_acknowledgement(self, address: str) -> bool:
        """Remove acknowledgement for a resource address so it appears in future reports.

        Returns True if an acknowledged entry was found and updated, False otherwise.
        """
        if not self._loaded:
            self.load()
        entry = self._data.get("entries", {}).get(address)
        if entry is None or not entry.get("acknowledged", False):
            return False
        entry["acknowledged"] = False
        entry.pop("acknowledged_reason", None)
        entry.pop("acknowledged_by", None)
        entry.pop("acknowledged_at", None)
        return True

    def reset_baseline(self) -> None:
        """Reset the baseline: clear run history and per-address tracking.

        Sets ``baseline_at`` to now. Used after a known-good deploy so that age tracking
        starts fresh from this point.  Any previously acknowledged entries are also cleared.
        """
        if not self._loaded:
            self.load()
        self._data["baseline_at"] = self.now_iso()
        self._data["entries"] = {}
        self._data["runs"] = []

    def list_runs(self, last: int = 0) -> List[Dict[str, Any]]:
        """Return the most recent drift-check runs.

        Args:
            last: Maximum number of runs to return (0 = return all).

        Returns:
            List of run records, most recent last.
        """
        if not self._loaded:
            self.load()
        runs: List[Dict[str, Any]] = self._data.get("runs", [])
        if last > 0:
            return list(runs[-last:])
        return list(runs)

    def list_acknowledged(self) -> List[Dict[str, Any]]:
        """Return all acknowledged address entries with their metadata."""
        if not self._loaded:
            self.load()
        result = []
        for address, entry in self._data.get("entries", {}).items():
            if entry.get("acknowledged", False):
                result.append({"address": address, **entry})
        return result

    def get_baseline_at(self) -> Optional[str]:
        """Return the baseline timestamp if one has been set, otherwise None."""
        if not self._loaded:
            self.load()
        return self._data.get("baseline_at")

    def save(self) -> None:
        """Persist history to disk. Creates parent directories as needed."""
        if not self._loaded:
            return
        try:
            self._history_dir.mkdir(parents=True, exist_ok=True)
            self._history_file.write_text(
                json.dumps(self._data, indent=2, default=str),
                encoding="utf-8",
            )
            self.ensure_gitignore()
        except OSError as exc:
            logger.warning(
                "drift_history_save_failed",
                path=str(self._history_file),
                error=str(exc),
            )

    def ensure_gitignore(self) -> None:
        """Add ``.strata/drift/`` to the workspace ``.gitignore`` if not already present."""
        gitignore = self._work_path / ".gitignore"
        ignore_entry = f"{SOLUTION_DIR}/{SOLUTION_DRIFT_DIR}/"
        try:
            if gitignore.exists():
                content = gitignore.read_text(encoding="utf-8")
                if ignore_entry not in content:
                    with gitignore.open("a", encoding="utf-8") as fh:
                        fh.write(f"\n# strata drift history\n{ignore_entry}\n")
            else:
                gitignore.write_text(
                    f"# strata drift history\n{ignore_entry}\n",
                    encoding="utf-8",
                )
        except OSError:
            pass  # non-fatal

    @staticmethod
    def now_iso() -> str:
        """Return the current UTC time as an ISO 8601 string."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
