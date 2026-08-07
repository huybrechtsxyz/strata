"""Audit policy and sink configuration models.

These models configure _what_ gets logged (event policy) and _where_ events
are sent (sink routing).  They live under ``spec.audit`` in environment YAML
and are merged across the deployment's ``environments[]`` array.

Sinks come in two flavours:
- Built-in types (stdout, ndjson, syslog, webhook) — lightweight, no integration needed
- Integration references — full SIEM integrations declared in configuration.spec.integrations
"""

from typing import Dict, List, Optional

from pydantic import Field, model_validator

from strata.models.common_models import PlatformBaseModel, PlatformName

# Built-in sink types handled directly by AuditController.forward_to_siem().
# SIEM destinations (splunk, sentinel, elk, otel) are *not* listed here — they are
# integrations, referenced via AuditSinkModel.integration.
BUILTIN_SINK_TYPES: tuple = ("stdout", "ndjson", "syslog", "webhook")


class AuditPolicyModel(PlatformBaseModel):
    """Which event types are active. Configured in environment YAML under spec.audit.policy."""

    events: Dict[str, bool] = Field(
        default_factory=lambda: {
            "deploy_audit": True,
            "cli_action": True,
            "policy_violation": True,
            "secret_access": True,
            "lock_event": False,
            "validation_result": False,
            "drift_alert": False,
            "build_event": False,
        },
        description="Map of event type → enabled flag",
    )


class AuditSinkModel(PlatformBaseModel):
    """A configured audit event sink — forwards events to a built-in type or integration."""

    name: PlatformName = Field(description="Unique sink name")
    type: Optional[str] = Field(default=None, description="Built-in sink type: stdout, ndjson, syslog, webhook")
    integration: Optional[PlatformName] = Field(
        default=None, description="References configuration.spec.integrations[].name"
    )
    enabled: bool = Field(default=True, description="Whether this sink is active")
    events: Optional[List[str]] = Field(default=None, description="Event filter (None = all enabled events)")

    # Type-specific fields (built-in sinks only):
    path: Optional[str] = Field(default=None, description="Output path (ndjson only)")
    address: Optional[str] = Field(default=None, description="Target address (syslog only)")
    url: Optional[str] = Field(default=None, description="Target URL (webhook only)")
    headers: Optional[Dict[str, str]] = Field(default=None, description="HTTP headers (webhook only)")
    format: Optional[str] = Field(
        default=None,
        description="Payload format for syslog sink: 'json' (default) or 'cef' (Common Event Format)",
    )

    @model_validator(mode="after")
    def validate_sink_target(self) -> "AuditSinkModel":
        """Exactly one of 'type' or 'integration' must be set."""
        if not self.type and not self.integration:
            raise ValueError("Sink must specify either 'type' or 'integration'")
        if self.type and self.integration:
            raise ValueError("Sink cannot specify both 'type' and 'integration'")
        return self

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> "AuditSinkModel":
        """Validate that type-specific fields match the declared type."""
        if self.integration:
            if any([self.path, self.address, self.url, self.headers, self.format]):
                raise ValueError("Integration-backed sinks must not have type-specific fields")
            return self

        match self.type:
            case "stdout":
                if any([self.path, self.address, self.url, self.headers, self.format]):
                    raise ValueError("stdout sink takes no extra fields")
            case "ndjson":
                if not self.path:
                    raise ValueError("ndjson sink requires 'path'")
                if any([self.address, self.url, self.headers, self.format]):
                    raise ValueError("ndjson sink only accepts 'path'")
            case "syslog":
                if not self.address:
                    raise ValueError("syslog sink requires 'address'")
                if any([self.path, self.url, self.headers]):
                    raise ValueError("syslog sink only accepts 'address' and 'format'")
                if self.format and self.format not in ("json", "cef"):
                    raise ValueError("syslog sink 'format' must be 'json' or 'cef'")
            case "webhook":
                if not self.url:
                    raise ValueError("webhook sink requires 'url'")
                if any([self.path, self.address, self.format]):
                    raise ValueError("webhook sink only accepts 'url' and 'headers'")
            case _:
                raise ValueError(
                    f"Unknown sink type '{self.type}'. "
                    f"Built-in types are: {', '.join(BUILTIN_SINK_TYPES)}. "
                    "SIEM destinations (splunk, sentinel, elk, otel) are integrations — "
                    "declare them in configuration.spec.integrations and reference them "
                    "with 'integration: <name>' instead of 'type'."
                )
        return self


class AuditConfigModel(PlatformBaseModel):
    """Top-level audit configuration under spec.audit in environment YAML."""

    policy: AuditPolicyModel = Field(default_factory=AuditPolicyModel, description="Event type policy")
    sinks: List[AuditSinkModel] = Field(default_factory=list, description="Configured sinks")
    structure: Optional[str] = Field(
        default=None,
        description=(
            "Deploy-log directory structure. "
            "Built-in: flat, by-stage, by-execution (default), by-date, "
            "by-environment, by-workspace, by-tenant, full."
        ),
    )
    deploy_log_path: Optional[str] = Field(
        default=None,
        description="Custom deploy-log base path relative to workspace root (defaults to .strata/deploy-log)",
    )
    repository: Optional[str] = Field(
        default=None,
        description=(
            "Name of a registered solution repo (from 'strata repo add') to commit and push "
            "deploy-log files to after each deployment. Omit to skip remote push. "
            "Example: 'config' or 'state'."
        ),
    )
