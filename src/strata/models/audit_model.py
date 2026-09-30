#!/usr/bin/env python3
"""Audit trail configuration models — `spec.audit` (docs/design/audit-trail.md).

Phase 1 of that doc's Layer 2 Implementation Plan: the config surface only —
`AuditConfigModel`/`AuditSinkModel`/`AuditGitSinkTargetModel`. The written
artifacts themselves (the deployment manifest, metrics record) are separate
models (Phase 2), not config.

Deliberately not ported from v1's real `models/audit_config_model.py`:

- No `path`/`structure`/`journal` fields at all — `.strata/audit` is a fixed
  `layout.audit_dir()` location (not configurable, same tier as
  `REMOTES_DIRNAME`), and the per-execution directory shape is derived from
  a deployment's own `layers.segments` when declared, never a Jinja2
  template field. `journal` has no evidenced v2 consumer and is deferred
  entirely. See the design doc's "Streamlining the configuration surface".
- No `deployments.ndjson` durable append series — real usage shows ~90% of
  deploys run in ephemeral CI, so a local-only append series never
  accumulates where it would matter; dropped rather than built and unused.
  See "A third file".
- `AuditPolicyModel`'s `Union[bool, AuditEventPolicyModel]` object wrapper is
  not ported — its extra fields were v1's own "reserved, not read by any
  producer yet". Flattened to `event_overrides: dict[str, bool] | None`
  directly on `AuditConfigModel`. See "Event admission — a closed set gate,
  not a policy framework".
- The closed event-type set is scoped to what v2 actually produces
  (`deployment.completed`/`destroyed`/`measured`), not v1's full 20-type
  list spanning workitem/lock/drift/cost/secret producers v2 doesn't have.
  Grows only when a new producer is built, same discipline as `Capability`.
- No `Capability.AUDIT` yet — `AuditSinkModel.integration` is annotated
  with `References(PlatformKind.INTEGRATION)` (so the reference walker
  checks it resolves), but the capability-gated dispatch through it is
  deferred alongside the `integration` arm's dispatch itself.
"""

from typing import Annotated

from pydantic import Field, model_validator

from strata.models.common_models import PlatformBaseModel, PlatformKind, PlatformName
from strata.models.reference_fields import References, RemoteReference
from strata.utils.names import check_unique_names

#: The closed set of event types v2 actually produces, each with its
#: default enabled state. Not configurable — `event_overrides` may only
#: override keys already in this table (see `validate_known_event_types`).
#: Grows only when a new producer is built (e.g. a future policy/workitem
#: controller), same discipline as `models.integration_model.Capability`.
EVENT_DEFAULTS: dict[str, bool] = {
    "deployment.completed": True,
    "deployment.destroyed": True,
    "deployment.measured": True,
}


class AuditGitSinkTargetModel(PlatformBaseModel):
    """Git push destination — routing only. Transport (url, auth) lives on `remote`.

    Distinct from `SourceModel.remote`'s read-oriented usage: `branch` is a
    mutable tip refreshed before every write, not a pinned `reference`. See
    the design doc's "Durable push destination — why SolutionRemoteModel
    can't be reused as-is".
    """

    remote: Annotated[PlatformName, RemoteReference()] = Field(
        description="Name of a remote declared in the solution manifest's spec.remotes (strata.yaml). "
        "Owns the URL and credentials (via its own `integration`); this sink never declares transport "
        "directly."
    )
    branch: str = Field(
        min_length=1,
        description="Mutable branch this sink pushes to — distinct from the remote's own (read-pinned) "
        "`reference`, refreshed (fetch + reset) immediately before every push.",
    )
    path: str = Field(
        min_length=1,
        description="Root prefix under the repository. The shared per-execution relative path (see "
        "docs/design/audit-trail.md's 'Path resolution') is appended under this.",
    )


class AuditSinkModel(PlatformBaseModel):
    """A configured audit event destination — exactly one of two structurally
    different arms (docs/design/audit-trail.md's "The endpoints").
    """

    name: PlatformName = Field(description="Unique sink name")
    enabled: bool = Field(default=True, description="Whether this sink is active")
    required: bool = Field(
        default=False,
        description="Same name/default as Integration.required. False (default): a dispatch failure "
        "warns but never fails the run. True: a dispatch failure escalates to a hard failure (deploy run "
        "exit code 3). See 'Dispatch failure — a required flag, not a binary silent-vs-fail choice'.",
    )
    events: list[str] | None = Field(
        default=None, description="Event filter — None means every gate-admitted event type"
    )

    integration: Annotated[PlatformName, References(PlatformKind.INTEGRATION)] | None = Field(
        default=None,
        description="Arm 1 — references an Integration document (siem/webhook/syslog). Dispatch through "
        "this arm is not implemented yet (docs/design/audit-trail.md's Layer 2 Implementation Plan, "
        "Phase 5) — a configured integration sink produces an explicit info-level finding instead of "
        "silently doing nothing.",
    )
    git: AuditGitSinkTargetModel | None = Field(default=None, description="Arm 2 — durable git push")

    @model_validator(mode="after")
    def validate_exactly_one_target(self) -> "AuditSinkModel":
        """A sink is a routing reference to exactly one destination shape, never both, never neither."""
        if (self.integration is None) == (self.git is None):
            raise ValueError(f"sink '{self.name}': exactly one of 'integration' or 'git' must be set.")
        return self

    @model_validator(mode="after")
    def validate_events_known(self) -> "AuditSinkModel":
        """A sink filtering on an event type outside the closed set is a typo, not a future-proof filter."""
        if self.events:
            unknown = sorted(set(self.events) - set(EVENT_DEFAULTS))
            if unknown:
                valid = ", ".join(sorted(EVENT_DEFAULTS))
                raise ValueError(
                    f"sink '{self.name}' filters on unknown event type(s) {unknown}. Valid event types are: {valid}."
                )
        return self


class AuditConfigModel(PlatformBaseModel):
    """`spec.audit` — fully optional; every field either derives a default or is dropped
    (docs/design/audit-trail.md's "Streamlining the configuration surface").
    """

    event_overrides: dict[str, bool] | None = Field(
        default=None,
        description="Overrides over EVENT_DEFAULTS, e.g. {'deployment.measured': false}. Keys must "
        "already be in the closed event-type set.",
    )
    sinks: list[AuditSinkModel] | None = Field(default=None, description="Configured audit event destinations")

    @model_validator(mode="after")
    def validate_known_event_types(self) -> "AuditConfigModel":
        """A typo'd override key (e.g. `deployment.completd`) is a validation error, not a silent no-op."""
        if self.event_overrides:
            unknown = sorted(set(self.event_overrides) - set(EVENT_DEFAULTS))
            if unknown:
                valid = ", ".join(sorted(EVENT_DEFAULTS))
                raise ValueError(
                    f"spec.audit.event_overrides: unknown event type(s) {unknown}. Valid event types are: {valid}."
                )
        return self

    @model_validator(mode="after")
    def validate_unique_sink_names(self) -> "AuditConfigModel":
        """Same discipline as ConfigurationSpecModel's providers/topologies/paths."""
        if self.sinks:
            check_unique_names([s.name for s in self.sinks], "sink names in audit configuration")
        return self

    @model_validator(mode="after")
    def validate_sink_filters_against_gate(self) -> "AuditConfigModel":
        """A sink naming an event the gate has disabled is unrepresentable, not just diagnosable.

        Rejected outright rather than admitted silently — the alternative (a sink filter implicitly
        re-enabling an event type the gate disabled) reopens the gate/filter drift v1's own ADR-0066
        catalogued as problem 8. Ported from v1's real
        `AuditConfigModel.validate_sink_filters_against_gate`, scoped to this smaller event-type set.
        """
        if not self.sinks:
            return self
        merged = {**EVENT_DEFAULTS, **(self.event_overrides or {})}
        for sink in self.sinks:
            if not sink.events:
                continue
            for event_type in sink.events:
                if event_type in merged and not merged[event_type]:
                    raise ValueError(
                        f"sink '{sink.name}' filters on '{event_type}', but "
                        f"spec.audit.event_overrides.{event_type} is false. Either enable the event type "
                        "or remove it from the sink filter."
                    )
        return self
