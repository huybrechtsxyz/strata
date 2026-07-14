#!/usr/bin/env python3
"""Pydantic model for version-manifest configuration."""

from typing import Annotated, Dict, Literal, Optional

from pydantic import Field, StringConstraints

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
)


class VersionManifestPinsModel(PlatformBaseModel):
    """Flat version declarations grouped by target type.

    All values are exact version strings (tags, chart versions, git refs).
    Keys must match the target name as declared in the stack modules.

    Example::

        images:
          app:     v2.1.0
          worker:  v2.1.0
        charts:
          traefik: "28.2.0"
          cert-manager: "1.16.0"
        remotes:
          iac_core: v2.6.0
    """

    images: Optional[Dict[PlatformName, str]] = Field(
        None,
        description="Image target versions. Key = service/module name, value = image tag.",
    )
    charts: Optional[Dict[PlatformName, str]] = Field(
        None,
        description="Helm chart target versions. Key = module name, value = chart version string.",
    )
    remotes: Optional[Dict[PlatformName, str]] = Field(
        None,
        description="Git remote target versions. Key = remote name, value = git ref (tag, branch, SHA).",
    )
    tools: Optional[Dict[PlatformName, str]] = Field(
        None,
        description="Provisioner tool versions. Key = provisioner name (must match workspace.spec.provisioners[].name), value = tool version string.",
    )


class VersionManifestMetaModel(PlatformBaseModel):
    """Metadata for a version-manifest file."""

    name: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]*$", min_length=1, max_length=64, strip_whitespace=True),
    ] = Field(description="Ring name this manifest covers (e.g. 'dev', 'prd')")
    annotations: Optional[dict] = Field(None, description="Optional free-form annotations")
    labels: Optional[dict] = Field(None, description="Optional labels")


class VersionManifestSpecModel(PlatformBaseModel):
    """Specification for a version-manifest file."""

    ring: PlatformName = Field(description="Ring this manifest covers")
    pins: VersionManifestPinsModel = Field(
        default_factory=VersionManifestPinsModel,
        description="Version declarations grouped by type (images, charts, remotes).",
    )
    hash: Optional[str] = Field(
        None,
        description=(
            "SHA-256 of the canonical pins payload, written by 'strata versions lock'. "
            "When present, deploy validates the file has not been modified since locking. "
            "Absent on new/unlocked files — they can still be deployed with a warning."
        ),
    )


class VersionManifestModel(PlatformBaseModel):
    """Root model for a version-manifest file (kind: version-manifest).

    Human- and tool-editable centralized versions file. Declares intended versions
    for a ring in a flat, easily parseable format.

    Generated as a starting point by ``strata versions init``. Updated by operators
    or external tooling (CI, renovate-style bots). Applied to the lock file by
    ``strata versions apply``.

    Sits at layer 3 in the resolution chain — above environment overrides, below the
    machine-generated lock file (``kind: version-lock``).

    Example::

        apiVersion: strata.huybrechts.xyz/v1
        kind: version
        meta:
          name: dev
        spec:
          ring: dev
          pins:
            images:
              app:     v2.1.0
              worker:  v2.1.0
            charts:
              traefik: "28.2.0"
            remotes:
              iac_core: v2.6.0
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v1,
        frozen=True,
        description="API version for platform configuration",
    )
    kind: Literal[PlatformKind.VERSION_MANIFEST] = Field(
        default=PlatformKind.VERSION_MANIFEST,
        frozen=True,
        description="Platform kind (always 'version')",
    )
    meta: VersionManifestMetaModel = Field(description="Version-manifest metadata")
    spec: VersionManifestSpecModel = Field(description="Version-manifest specification")
