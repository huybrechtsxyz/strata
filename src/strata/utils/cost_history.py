"""Cost history store.

Persists per-deployment cost snapshots at ``.strata/cost/{deployment_name}.cost-history.json``.
Each call to ``record_snapshot()`` appends a new entry. History is capped at a
configurable maximum number of entries (default: 50) to prevent unbounded growth.

History file structure::

    {
      "deployment": "production",
      "snapshots": [
        {
          "recorded_at": "2026-07-22T14:30:00Z",
          "version": "1.0.0",
          "total_monthly": 4702.40,
          "currency": "USD",
          "provisioners": {
            "terraform": {"total_monthly": 4702.40}
          },
          "delta_from_previous": null
        },
        {
          "recorded_at": "2026-07-22T16:00:00Z",
          "version": "1.0.1",
          "total_monthly": 4950.00,
          "currency": "USD",
          "provisioners": {
            "terraform": {"total_monthly": 4950.00}
          },
          "delta_from_previous": 247.60
        }
      ]
    }
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.logger import get_logger
from strata.utils.config import get_cost_dir

logger = get_logger(__name__)

# Maximum snapshots retained per deployment
_DEFAULT_MAX_SNAPSHOTS = 50


class CostHistoryStore:
    """Load, append, and persist cost snapshots for a deployment.

    Usage::

        store = CostHistoryStore(work_path, "production")
        store.load()
        store.record_snapshot(
            cost_data=result,
            version="1.0.0",
            currency="EUR",
        )
        store.save()

        # Retrieve history
        snapshots = store.list_snapshots(last=10)
    """

    def __init__(
        self,
        work_path: Path,
        deployment_name: str,
        max_snapshots: int = _DEFAULT_MAX_SNAPSHOTS,
    ) -> None:
        self._work_path = work_path
        self._deployment_name = deployment_name
        self._max_snapshots = max_snapshots
        self._history_dir = get_cost_dir(work_path)
        self._history_file = self._history_dir / f"{deployment_name}.cost-history.json"
        self._data: Dict[str, Any] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load existing history from disk (no-op if file does not exist)."""
        if not self._history_file.exists():
            self._data = {"deployment": self._deployment_name, "snapshots": []}
            self._loaded = True
            return

        try:
            raw = self._history_file.read_text(encoding="utf-8")
            self._data = json.loads(raw)
            self._loaded = True
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("cost_history_load_failed", path=str(self._history_file), error=str(exc))
            self._data = {"deployment": self._deployment_name, "snapshots": []}
            self._loaded = True

    def record_snapshot(
        self,
        cost_data: Dict[str, Any],
        version: Optional[str] = None,
        currency: str = "USD",
    ) -> None:
        """Extract costs from a ``cost.json``-shaped dict and append a snapshot.

        Args:
            cost_data: Output from ``CostController.show()`` or ``cost.json`` contents.
                       Expected shape: ``{"provisioners": {"name": {...infracost...}}}``
            version: Deployment version string (informational).
            currency: Currency code for the snapshot (informational).
        """
        if not self._loaded:
            self.load()

        provisioner_totals: Dict[str, Any] = {}
        grand_total: float = 0.0

        provisioners = cost_data.get("provisioners", {})
        if isinstance(provisioners, dict):
            for prov_name, prov_data in provisioners.items():
                monthly = self._extract_total(prov_data)
                if monthly is not None:
                    provisioner_totals[str(prov_name)] = {"total_monthly": monthly}
                    grand_total += monthly

        snapshots: List[Dict[str, Any]] = self._data.setdefault("snapshots", [])

        # Compute delta from the previous snapshot
        delta: Optional[float] = None
        if snapshots:
            prev = snapshots[-1]
            prev_total = prev.get("total_monthly")
            if prev_total is not None and grand_total > 0:
                delta = round(grand_total - float(prev_total), 2)

        entry: Dict[str, Any] = {
            "recorded_at": self.now_iso(),
            "total_monthly": round(grand_total, 2),
            "currency": currency,
            "provisioners": provisioner_totals,
            "delta_from_previous": delta,
        }
        if version:
            entry["version"] = version

        snapshots.append(entry)

        # Trim to max_snapshots (keep most recent)
        if len(snapshots) > self._max_snapshots:
            self._data["snapshots"] = snapshots[-self._max_snapshots :]

    def save(self) -> None:
        """Persist history to disk. Non-fatal on failure."""
        if not self._loaded:
            return
        try:
            self._history_dir.mkdir(parents=True, exist_ok=True)
            self._history_file.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("cost_history_save_failed", path=str(self._history_file), error=str(exc))

    def list_snapshots(self, last: int = 0) -> List[Dict[str, Any]]:
        """Return snapshots, most-recent last.

        Args:
            last: Number of most-recent snapshots to return. 0 returns all.
        """
        if not self._loaded:
            self.load()
        snapshots = self._data.get("snapshots", [])
        if last > 0:
            return snapshots[-last:]
        return list(snapshots)

    def latest(self) -> Optional[Dict[str, Any]]:
        """Return the most-recent snapshot, or None."""
        if not self._loaded:
            self.load()
        snapshots = self._data.get("snapshots", [])
        return snapshots[-1] if snapshots else None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _extract_total(prov_data: Any) -> Optional[float]:
        """Extract totalMonthlyCost from a provisioner result dict."""
        if not isinstance(prov_data, dict):
            return None

        # breakdown.totalMonthlyCost (Infracost standard)
        breakdown = prov_data.get("breakdown")
        if isinstance(breakdown, dict):
            value = breakdown.get("totalMonthlyCost")
            if value is not None:
                try:
                    return float(value)
                except (ValueError, TypeError):
                    pass

        # projects[].breakdown.totalMonthlyCost (multi-project)
        projects = prov_data.get("projects")
        if isinstance(projects, list):
            total = 0.0
            found = False
            for project in projects:
                proj_breakdown = project.get("breakdown", {}) if isinstance(project, dict) else {}
                value = proj_breakdown.get("totalMonthlyCost")
                if value is not None:
                    try:
                        total += float(value)
                        found = True
                    except (ValueError, TypeError):
                        pass
            if found:
                return total

        # Top-level totalMonthlyCost
        value = prov_data.get("totalMonthlyCost")
        if value is not None:
            try:
                return float(value)
            except (ValueError, TypeError):
                pass

        return None
