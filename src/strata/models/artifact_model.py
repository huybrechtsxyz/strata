#!/usr/bin/env python3
"""Pydantic model for artifact configuration validation.

An `Artifact` is a pinnable, named reference to something external strata
does not fetch or deploy itself — a container image today, possibly a chart
or other identifier later (docs/design/artifact-references.md). Referenced
by identity (ADR-0015) from anywhere in the solution: a `store: artifact`
variable (an opaque external provisioner's own input, e.g. dispatcher_api's
`dspapi_container_image_tag`) or a `ModuleServiceModel.artifact` (a
strata-owned Compose/Helm service).

Deliberately NOT built on `SourceModel`, despite the surface similarity to
its chart mode (`chart_name`/`chart_version`) — `SourceModel.remote` is
fetch-oriented (`SolutionRemoteModel.fetch: strata|external` = "who
materialises bytes on disk"), and strata never fetches a container image at
all; the registry pulls it at deploy time, not strata at build time. See
the design doc's "Shape correction" changelog entry for the full reasoning.

No chart mode: real chart pins exist (`haven/versions/prd.yaml`'s
`pins.charts`), but every one targets a real `kind: module` document's own
`SourceModel.chart_version` — already fully served by the existing
mechanism, not a gap this kind needs to cover.

`properties`/`configuration`/`custom` match every other kind's convention
(`dict[str, Any] | None`) — unlike `TenantSpecModel`'s same-named fields,
these have no merge semantics; there is nothing downstream that merges an
artifact's properties into anything else. Purely descriptive/passthrough,
closer to `ResourceSpecModel.custom`'s treatment.
"""

from typing import Annotated, Any

from pydantic import Field, field_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.models.reference_fields import References


class ArtifactSpecModel(PlatformBaseModel):
    """Artifact specification: the pinnable reference itself, plus optional passthrough data."""

    image_name: str = Field(
        min_length=1,
        description="Container image path (e.g. 'int-docker-test/src/acme.dispatcher.api'). Identity — "
        "never overridden by a kind: version pin, only image_tag is.",
    )
    image_tag: str | None = Field(
        None,
        description="Image tag/digest to deploy. May be blank until a real image exists (same convention "
        "as today's plain constant variables). Overridden by kind: version's pins.artifacts[name] when set.",
    )
    registry: str | None = Field(
        None,
        description="Registry host, free text for documentation only (e.g. 'cdn-feeds.acme.com') — NOT a "
        "SolutionRemoteModel reference, since strata never fetches an image (it stays in the registry and "
        "is pulled by the runtime at deploy time, not materialised by strata at build time).",
    )
    integration: Annotated[PlatformName, References(PlatformKind.INTEGRATION)] | None = Field(
        None,
        description="Optional Integration (capability: sources) providing registry credentials — same "
        "field name/pattern as SolutionRemoteModel.integration. Real environment files don't use this "
        "today (plain variables/secrets instead); this is a supported upgrade path, not a requirement.",
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")
    properties: dict[str, Any] | None = Field(
        None, description="Free-form descriptive data (e.g. owning team, cost center) — never merged anywhere"
    )
    configuration: dict[str, Any] | None = Field(
        None, description="Raw passthrough data for whatever consumes this artifact"
    )
    custom: dict[str, Any] | None = Field(None, description="Custom user-defined data for scripts or extensions")


class ArtifactMetaModel(PlatformBaseModel):
    """Artifact metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique artifact name, referenced from a variable or a module service")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class ArtifactModel(PlatformBaseModel):
    """Root model for an artifact configuration document."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2, frozen=True, description="API version for artifact configuration"
    )
    kind: PlatformKind = Field(
        default=PlatformKind.ARTIFACT, frozen=True, description="Platform kind (always 'artifact')"
    )
    meta: ArtifactMetaModel = Field(description="Artifact metadata (name, annotations, labels, tags)")
    spec: ArtifactSpecModel = Field(description="Artifact specification (image reference, optional passthrough data)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.ARTIFACT)
