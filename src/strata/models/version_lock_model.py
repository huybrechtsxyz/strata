#!/usr/bin/env python3
"""Pydantic model for version-lock configuration."""

from enum import Enum
from typing import Annotated, List, Literal, Optional

from pydantic import Field, StringConstraints, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
)


class VersionPinTargetType(str, Enum):
    """Supported target types for a version pin."""

    REMOTE = "remote"
    HELM_CHART = "helm_chart"
    IMAGE = "image"
    TOOL = "tool"


class VersionPinTrack(str, Enum):
    """Tracking mode for a version pin."""

    EXACT = "exact"
    LATEST = "latest"


class VersionPinTargetModel(PlatformBaseModel):
    """Identifies the target being pinned."""

    type: VersionPinTargetType = Field(description="Target type: remote | helm_chart | image | tool")
    name: PlatformName = Field(description="Name of the remote, chart module, service, or tool")


class VersionPinModel(PlatformBaseModel):
    """A single version pin entry within a version-lock or version-manifest."""

    target: VersionPinTargetModel = Field(description="The target being pinned")
    version: Optional[str] = Field(
        None,
        description="Exact version string (tag, chart version, image tag). Required when track is 'exact' or omitted.",
    )
    track: VersionPinTrack = Field(
        default=VersionPinTrack.EXACT,
        description="Tracking mode. 'exact' pins to version; 'latest' floats (dev rings only).",
    )
    resolved: Optional[str] = Field(
        None,
        description="Last known resolved version when track=latest (informational, set by CI callback).",
    )
    resolved_at: Optional[str] = Field(
        None,
        description="ISO 8601 UTC timestamp of when resolved was last recorded.",
    )
    resolved_sha: Optional[str] = Field(
        None,
        description="Immutable SHA (git commit SHA or OCI digest) at pin time. "
        "Used by validate --deep for tamper detection (F-2).",
    )


class VersionLockMetaModel(PlatformBaseModel):
    """Metadata for a version-lock file."""

    name: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]*$", min_length=1, max_length=64, strip_whitespace=True),
    ] = Field(description="Ring name or ring.scope identifier (e.g. 'prd', 'prd.acme')")
    annotations: Optional[dict] = Field(None, description="Optional free-form annotations")
    labels: Optional[dict] = Field(None, description="Optional labels")


class VersionLockPreviousModel(PlatformBaseModel):
    """Previous ring lock state — enables rollback without git history traversal.

    Written by ``strata promote`` when it advances an existing ring lock.
    Contains a snapshot of the *previous* ring lock's key fields so that
    ``strata promote rollback`` can restore to the prior version in one step.
    """

    source: str = Field(description="Relative path to the previous version file (same base directory as the lock).")
    version: Optional[str] = Field(
        None,
        description="Version identifier from the previous version file's meta.name.",
    )
    hash: Optional[str] = Field(
        None,
        description="SHA-256 of the previous version file's spec.pins (from its spec.hash field).",
    )


class VersionLockSpecModel(PlatformBaseModel):
    """Specification for a version-lock file.

    New-style (pointer): set ``source`` to a path relative to the lock file's directory.
    The lock is a thin pointer — no duplicated pins.  Written by ``strata promote``.

    Old-style (inline pins): set ``pins`` directly.  Written by ``strata versions apply``.
    Still valid for non-promotion deployments.

    Exactly one of ``source`` or ``pins`` must be present.
    """

    ring: PlatformName = Field(description="Ring this lock governs (must match a ring in the progression config)")
    source: Optional[str] = Field(
        None,
        description=(
            "Path to the version file this lock points to, relative to the lock file's directory. "
            "Written by 'strata promote'. Mutually exclusive with 'pins'."
        ),
    )
    hash: Optional[str] = Field(
        None,
        description=(
            "SHA-256 of the pointed-to version file's spec.pins (copied from the version file's "
            "own spec.hash at promote time). Used by VersionService to detect tampering after promotion."
        ),
    )
    version: Optional[str] = Field(
        None,
        description=(
            "Version identifier copied from the version file's meta.name at promote time. "
            "Informational — avoids reading the version file just to display the version."
        ),
    )
    wave: Optional[int] = Field(
        None,
        description="Wave number for wave-lock files (e.g. 1 for prod.wave.1.lock.yaml). Absent on ring locks.",
    )
    previous: Optional[VersionLockPreviousModel] = Field(
        None,
        description=(
            "Snapshot of the previous ring lock — written on every ring lock advance so that "
            "'strata promote rollback' can restore without git history traversal."
        ),
    )
    scope: Optional[str] = Field(
        None,
        description="Layer name when this is a scoped canary overlay (e.g. 'tenant').",
    )
    scope_selector: Optional[str] = Field(
        None,
        description="Which deployment(s) the scoped overlay applies to (e.g. tenant name 'acme').",
    )
    pins: Optional[List[VersionPinModel]] = Field(
        None,
        description="Inline pins (old-style). Mutually exclusive with 'source'.",
    )

    @model_validator(mode="after")
    def validate_source_or_pins(self) -> "VersionLockSpecModel":
        """Exactly one of source or pins must be present."""
        has_source = self.source is not None
        has_pins = self.pins is not None
        if has_source and has_pins:
            raise ValueError("spec.source and spec.pins are mutually exclusive in a version-lock file.")
        if not has_source and not has_pins:
            raise ValueError("A version-lock file must have either spec.source (pointer) or spec.pins (inline).")
        return self


class VersionLockModel(PlatformBaseModel):
    """Root model for a version-lock file (kind: version-lock).

    Machine-generated by ``strata promote start`` and ``strata versions apply``.
    Never hand-edited — humans review the diff in a PR before merging.

    One file per ring: ``versions/<ring>.yaml``.
    One scoped overlay per canary wave: ``versions/<ring>.<scope_selector>.yaml``.

    Example::

        apiVersion: strata.huybrechts.xyz/v1
        kind: version-lock
        meta:
          name: prd
        spec:
          ring: prd
          pins:
            - target: { type: remote, name: iac_core }
              version: v2.5.0
            - target: { type: helm_chart, name: traefik }
              version: "28.2.0"
            - target: { type: image, name: app }
              version: v1.4.0
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for platform configuration",
    )
    kind: Literal[PlatformKind.VERSION_LOCK] = Field(
        default=PlatformKind.VERSION_LOCK,
        frozen=True,
        description="Platform kind (always 'version-lock')",
    )
    meta: VersionLockMetaModel = Field(description="Version-lock metadata (name = ring or ring.scope)")
    spec: VersionLockSpecModel = Field(description="Version-lock specification")

    @property
    def is_pointer(self) -> bool:
        """True when this is a new-style pointer lock (spec.source set)."""
        return self.spec.source is not None
