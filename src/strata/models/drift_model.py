"""Data models for infrastructure drift detection reports."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DriftSeverity(str, Enum):
    """Severity classification for a drift entry.

    Comparison operators follow natural severity ordering:
    ``CRITICAL > HIGH > MEDIUM > LOW > INFO``.
    """

    CRITICAL = "critical"  # Security-sensitive changes: NSG, IAM, firewall rules
    HIGH = "high"  # Core infrastructure: VM size, disk, network topology
    MEDIUM = "medium"  # Configuration changes: app settings, scaling rules
    LOW = "low"  # Cosmetic changes: tags, descriptions
    INFO = "info"  # Informational: output-only resources, data sources

    @classmethod
    def ordered(cls) -> List["DriftSeverity"]:
        """Return severities from highest to lowest."""
        return [cls.CRITICAL, cls.HIGH, cls.MEDIUM, cls.LOW, cls.INFO]

    @property
    def _weight(self) -> int:
        """Numeric weight: higher value = more severe."""
        return {
            "critical": 4,
            "high": 3,
            "medium": 2,
            "low": 1,
            "info": 0,
        }[self.value]

    def __le__(self, other: "DriftSeverity") -> bool:  # type: ignore[override]
        return self._weight <= other._weight

    def __lt__(self, other: "DriftSeverity") -> bool:  # type: ignore[override]
        return self._weight < other._weight

    def __ge__(self, other: "DriftSeverity") -> bool:  # type: ignore[override]
        return self._weight >= other._weight

    def __gt__(self, other: "DriftSeverity") -> bool:  # type: ignore[override]
        return self._weight > other._weight


@dataclass
class DriftEntry:
    """A single drifted resource detected in a deployment stage."""

    address: str  # e.g. "azurerm_network_security_rule.allow_ssh"
    resource_type: str  # e.g. "azurerm_network_security_rule"
    action: str  # "update" | "delete" | "create"
    severity: DriftSeverity
    stage: str
    changed_attributes: List[str]  # attribute paths that changed
    before: Dict[str, Any] = field(default_factory=dict)  # values in state
    after: Dict[str, Any] = field(default_factory=dict)  # values in config
    first_detected: str = ""  # ISO timestamp of first detection
    consecutive_checks: int = 0


@dataclass
class DriftSummary:
    """Per-severity count totals for a DriftReport."""

    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0

    def increment(self, severity: DriftSeverity) -> None:
        setattr(self, severity.value, getattr(self, severity.value) + 1)

    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low + self.info

    def to_dict(self) -> Dict[str, int]:
        return {
            "critical": self.critical,
            "high": self.high,
            "medium": self.medium,
            "low": self.low,
            "info": self.info,
            "total": self.total(),
        }


@dataclass
class DriftReport:
    """Complete drift detection result for a deployment."""

    deployment: str
    checked_at: str  # ISO timestamp
    stages_checked: List[str]
    entries: List[DriftEntry] = field(default_factory=list)
    summary: DriftSummary = field(default_factory=DriftSummary)

    @property
    def has_drift(self) -> bool:
        return len(self.entries) > 0

    @property
    def max_severity(self) -> Optional[DriftSeverity]:
        if not self.entries:
            return None
        order = DriftSeverity.ordered()
        for sev in order:
            if any(e.severity == sev for e in self.entries):
                return sev
        return None

    def above_threshold(self, threshold: DriftSeverity) -> bool:
        """Return True if any entry is at or above the given severity threshold."""
        if not self.entries:
            return False
        return any(e.severity >= threshold for e in self.entries)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deployment": self.deployment,
            "checked_at": self.checked_at,
            "stages_checked": self.stages_checked,
            "has_drift": self.has_drift,
            "max_severity": self.max_severity.value if self.max_severity else None,
            "summary": self.summary.to_dict(),
            "entries": [
                {
                    "address": e.address,
                    "resource_type": e.resource_type,
                    "action": e.action,
                    "severity": e.severity.value,
                    "stage": e.stage,
                    "changed_attributes": e.changed_attributes,
                    "before": e.before,
                    "after": e.after,
                    "first_detected": e.first_detected,
                    "consecutive_checks": e.consecutive_checks,
                }
                for e in self.entries
            ],
        }
