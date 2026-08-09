"""Audit policy and sink configuration models.

These models configure _what_ gets logged (event policy) and _where_ events
are sent (sink routing). They live under ``spec.audit`` in configuration YAML.

A sink is a connection to another system (ADR-0066) — every sink is a reference
to an integration declared under ``configuration.spec.integrations``. There are no
built-in sink types: ``stdout``/``ndjson`` are removed (the local record is now the
journal, ``logger/audit.py``); ``syslog``/``webhook`` are removed as sink types and
promoted to integrations (``integrations/siem/syslog_siem_integration.py`` /
``webhook_siem_integration.py``).
"""

from typing import Dict, List, Optional, Union

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import PlatformBaseModel, PlatformName

# Renamed event types (ADR-0066) — old key -> new key. Detected explicitly so a typo'd
# legacy name gets "use X" instead of the generic unknown-key list (see "This is a clean
# break": backwards compatibility is not owed, a good error is).
LEGACY_EVENT_TYPE_RENAMES: Dict[str, str] = {
    "cli_action": "command.executed",
    "deploy_audit": "deployment.completed",
    "deployment_metrics": "deployment.measured",
    "build_event": "build.completed",
    "validation_result": "validation.completed",
    "policy_violation": "policy.violated",
    "secret_access": "secret.accessed",
    "drift_alert": "drift.detected",
    "lock_event": "lock.acquired' or 'lock.released",
}

# Legacy AuditSinkModel fields (ADR-0066) — sinks used to carry transport configuration
# directly; they are now pure references to configuration.spec.integrations[]. Detected
# explicitly so the error names the exact replacement shape.
LEGACY_SINK_FIELDS = ("type", "path", "address", "url", "headers", "format")

# Closed enum of event types (ADR-0066) — three classes:
# - Invocation: who ran what (command.executed)
# - Outcome: what a run did (deployment.*, build.completed, validation.completed, workitem.*)
# - Domain: what happened to the system (policy.violated, secret.accessed, lock.*, drift.detected)
#
# Class-aware defaults, set by measurement (18,853-entry audit.log sample) rather than taste —
# command.executed defaulting off alone removes ~95% of measured volume (VS Code polling,
# pytest runs). workitem.created/resumed added alongside the ADR's original 8 declared types
# since they are real, existing SIEM-forwarded lifecycle events (RunDeployCommand), not a new
# producer — treated as Outcome-class, defaulting on like deployment.completed.
AUDIT_EVENT_DEFAULTS: Dict[str, bool] = {
    # Invocation
    "command.executed": False,
    # Outcome
    "deployment.completed": True,
    "deployment.destroyed": True,  # wired: DestroyDeployCommand (ADR-0066 gap B)
    "deployment.measured": True,  # not yet wired — ADR-0064's metrics record isn't implemented
    "build.completed": False,  # not yet wired — no producer calls forward() for this yet
    "validation.completed": False,  # not yet wired — no producer calls forward() for this yet
    "workitem.created": True,
    "workitem.resumed": True,
    "workitem.approved": True,  # wired: WorkItemController.resolve() (ADR-0066 gap A)
    "workitem.rejected": True,  # wired: WorkItemController.resolve() (ADR-0066 gap A)
    "workitem.completed": True,  # wired: WorkItemController.resolve() (ADR-0066 gap A)
    "workitem.cancelled": True,  # wired: WorkItemController.resolve() (ADR-0066 gap A)
    # Domain
    "policy.violated": True,  # wired: validate/build/deploy/check_policy (any failed PolicyResult)
    # secret.accessed: intentionally left unwired. The concept and default were kept in
    # the closed enum, but wiring it is a deliberate non-decision, not an oversight — see
    # "secret.accessed — deliberately not wired" in the ADR's Consequences section.
    # Real secret stores (Vault, Key Vault, Bitwarden) already produce far more rigorous
    # native audit trails than strata could add; the only unique value strata's own event
    # would add is correlating an access to a specific execution_id, and the one hook
    # point available (ValueController.resolve_values()) is shared by genuine deploys AND
    # read-only inspection commands (`deploy values list/get/show`) with no way to tell
    # them apart there — wiring it as-is would reproduce exactly the read-only-command
    # polling-volume problem step 1 fixed for command.executed, one layer down.
    "secret.accessed": True,
    "lock.acquired": False,
    "lock.released": False,
    "drift.detected": True,
}


class AuditEventPolicyModel(PlatformBaseModel):
    """Per-event-type policy. A bare ``bool`` in YAML is shorthand for ``{enabled: <bool>}``."""

    enabled: bool = Field(default=True, description="Whether this event type is audited at all")
    # Reserved — not read by any producer yet; commented out so `extra="forbid"` rejects them
    # until one exists, keeping the surface honest rather than aspirational (ADR-0066):
    # severity: Optional[str] = None        # override event.kind / SIEM alert routing
    # sample: Optional[int] = None          # audit 1 in N (high-volume classes)
    # retention_days: Optional[int] = None  # hint carried to sinks that honour it


# Value accepted for each key in policy.events — a bare bool or the full object.
AuditEventPolicy = Union[bool, AuditEventPolicyModel]


def _default_event_policy() -> Dict[str, AuditEventPolicy]:
    return {k: AuditEventPolicyModel(enabled=v) for k, v in AUDIT_EVENT_DEFAULTS.items()}


class AuditPolicyModel(PlatformBaseModel):
    """Which event types are active — the global gate, consulted by ``AuditController.forward()``.

    Keys are validated against the closed set in ``AUDIT_EVENT_DEFAULTS``; a typo
    (``policy.violations`` for ``policy.violated``) is a validation error at exit code 3
    rather than a knob that silently never fires. Unlisted (but valid) keys fall back
    to their class-aware default; a genuinely unrecognised event type consulted by
    ``forward()`` at runtime (one not in this closed set at all) is never gated off —
    the closed-set validation exists to catch operator typos in configured keys, not to
    silently block producers this model doesn't yet know about.
    """

    events: Dict[str, AuditEventPolicy] = Field(
        default_factory=_default_event_policy,
        description="Map of event type → enabled flag or {enabled, ...} object",
    )

    @field_validator("events", mode="before")
    @classmethod
    def _normalize_shorthand(cls, value: object) -> object:
        """Merge class-aware defaults under explicit overrides, and normalise bare bools."""
        if not isinstance(value, dict):
            return value
        merged: Dict[str, object] = dict(AUDIT_EVENT_DEFAULTS)
        merged.update(value)
        return {
            key: (AuditEventPolicyModel(enabled=val) if isinstance(val, bool) else val) for key, val in merged.items()
        }

    @model_validator(mode="after")
    def validate_known_event_types(self) -> "AuditPolicyModel":
        unknown = sorted(set(self.events) - set(AUDIT_EVENT_DEFAULTS))
        if unknown:
            renamed = [key for key in unknown if key in LEGACY_EVENT_TYPE_RENAMES]
            if renamed:
                first = renamed[0]
                raise ValueError(
                    f"spec.audit.policy.events.{first}: '{first}' was renamed — use "
                    f"'{LEGACY_EVENT_TYPE_RENAMES[first]}'."
                )
            valid = ", ".join(sorted(AUDIT_EVENT_DEFAULTS))
            raise ValueError(
                f"spec.audit.policy.events: unknown event type(s) {unknown}. Valid event types are: {valid}."
            )
        return self

    def is_enabled(self, event_type: str) -> bool:
        """Resolve whether *event_type* is admitted by the gate.

        A recognised type always has a policy entry (defaults are merged in by the
        ``events`` validator). A type outside the closed set — a producer this model
        doesn't know about — is never gated off here; see the class docstring.
        """
        entry = self.events.get(event_type)
        if entry is None:
            return True
        return entry.enabled if isinstance(entry, AuditEventPolicyModel) else bool(entry)


class AuditSinkModel(PlatformBaseModel):
    """A configured audit event sink — a routing reference to an integration (ADR-0066).

    Sinks carry no transport configuration of their own: endpoint, credentials, and
    format all live on the referenced ``configuration.spec.integrations[]`` entry.
    """

    name: PlatformName = Field(description="Unique sink name")
    integration: PlatformName = Field(description="References configuration.spec.integrations[].name")
    enabled: bool = Field(default=True, description="Whether this sink is active")
    events: Optional[List[str]] = Field(default=None, description="Event filter (None = all enabled events)")

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_shape(cls, value: object) -> object:
        """Old sinks carried transport config directly. Name the exact replacement (ADR-0066)."""
        if not isinstance(value, dict):
            return value
        found = [key for key in LEGACY_SINK_FIELDS if key in value]
        if not found:
            return value
        legacy_type = value.get("type", "<type>")
        name = value.get("name", "<name>")
        raise ValueError(
            f"spec.audit.sinks: 'type: {legacy_type}' is no longer supported — sinks are now "
            "references to spec.integrations[]. Replace with:\n\n"
            "  integrations:\n"
            f"    - name: {name}\n"
            f"      type: {legacy_type}\n"
            "      capabilities: [audit]\n"
            "      endpoints:\n"
            "        address: <the url/path that was here>\n\n"
            "  audit:\n"
            "    sinks:\n"
            f"      - name: {name}\n"
            f"        integration: {name}"
        )


class AuditJournalModel(PlatformBaseModel):
    """Local audit-trail journal (``logger/audit.py``) — who ran what, when.

    Distinct from ``sinks``/``policy`` (which route SIEM-bound domain events, ADR-0066)
    and from ``deploy_log_path`` (which stores full ``DeployLogModel`` records). The
    journal is the NDJSON CLI-invocation log written by every command via
    ``logger.audit.audit()``. This is the primary, committed configuration location;
    it is overridden machine-locally by ``.strata/logging.yaml``'s ``audit:`` section
    (e.g. for a developer who wants a different local path), which in turn falls back
    to ``configure_audit_log()``'s built-in defaults when neither is present.
    """

    path: Optional[str] = Field(default=None, description="Journal file path (relative to work_path or absolute)")
    rotation: Optional[str] = Field(default=None, description="Rotation strategy: 'size' (default) or 'daily'")
    max_bytes: Optional[int] = Field(default=None, description="Max file size before rotation (rotation='size')")
    backup_count: Optional[int] = Field(default=None, description="Number of rotated backups to keep")
    date_suffix: Optional[str] = Field(default=None, description="strftime suffix for daily-rotated backups")


class AuditConfigModel(PlatformBaseModel):
    """Top-level audit configuration under spec.audit in environment YAML."""

    policy: AuditPolicyModel = Field(default_factory=AuditPolicyModel, description="Event type policy")
    sinks: List[AuditSinkModel] = Field(default_factory=list, description="Configured sinks")
    journal: Optional[AuditJournalModel] = Field(default=None, description="Local audit-trail journal configuration")
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

    @model_validator(mode="after")
    def validate_sink_filters_against_gate(self) -> "AuditConfigModel":
        """A sink naming an event the gate has disabled is unrepresentable, not just diagnosable.

        Rejected at exit code 3 rather than admitted silently — the alternative (a sink
        filter implicitly re-enabling an event type) reopens exactly the gate/filter
        drift ADR-0066 catalogues as problem 8.
        """
        for sink in self.sinks:
            if not sink.events:
                continue
            for event_type in sink.events:
                if event_type not in AUDIT_EVENT_DEFAULTS:
                    valid = ", ".join(sorted(AUDIT_EVENT_DEFAULTS))
                    raise ValueError(
                        f"sink '{sink.name}' filters on unknown event type '{event_type}'. "
                        f"Valid event types are: {valid}."
                    )
                if not self.policy.is_enabled(event_type):
                    raise ValueError(
                        f"sink '{sink.name}' filters on '{event_type}', but "
                        f"spec.audit.policy.events.{event_type} is false. Either enable the "
                        "event type or remove it from the sink filter."
                    )
        return self
