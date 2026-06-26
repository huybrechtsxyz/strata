"""SIEM sink integrations — forwards structured audit events to external immutable stores."""

from strata.integrations.siem.base_siem_integration import SiemBaseIntegration
from strata.integrations.siem.elk_siem_integration import ElkSiemIntegration
from strata.integrations.siem.otel_siem_integration import OtelSiemIntegration
from strata.integrations.siem.sentinel_integration import SentinelIntegration

__all__ = [
    "SiemBaseIntegration",
    "SentinelIntegration",
    "ElkSiemIntegration",
    "OtelSiemIntegration",
]
