#!/usr/bin/env python3
"""Pydantic model for asset configuration validation (docs/design/asset-management.md).

An `Asset` is a named, reusable file-set location — plain input files that
are not IaC code (an OpenAPI contract, images, a certificate bundle) and
whose on-disk variant differs per deployment. Standalone kind, not an
embedded field, for the same reason `artifact`/`tenant`/`provider` are
standalone: the same asset family can be referenced from more than one
provisioner/workspace (docs/archive/artifact-references.md's
"Identity/reuse" reasoning applies identically here).

Deliberately NOT built on `SourceModel` despite the surface similarity
(`remote`/`source_path`) — `SourceModel` also carries chart-registry mode
(`chart_name`/`chart_version`/`chart_repository`) and `target_path`, none of
which apply here (an asset is always a plain file/directory copy, never a
Helm pull). Re-declaring just the two fields it does need avoids inheriting
`SourceModel.validate_source_mode()`'s git-vs-chart mode check, which would
be meaningless for an asset.
"""

from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.models.reference_fields import RemoteReference
from strata.utils.path_safety import validate_relative_path


class AssetSpecModel(PlatformBaseModel):
    """Asset specification: where its files live, plus optional documentation data."""

    description: str | None = Field(None, description="Optional description for documentation purposes")
    remote: Annotated[PlatformName, RemoteReference()] | None = Field(
        None,
        description="Remote this asset's files live under (SourceModel's own field/meaning, reused "
        "unchanged). None means this solution's own repository.",
    )
    source_path: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        description="Path to the file or directory, relative to the resolved remote's root. May contain "
        "'{name}' segment tokens, filled from the deployment's resolved spec.properties at build time "
        "(same token syntax path_conventions.compile_pattern() uses for matching, reused via "
        "path_conventions.fill_pattern() for filling). May also contain glob wildcards ('*', '**', '?', "
        "'[abc]') to select a subset of files, e.g. 'assets/{deploy_tier}/openapi/*.json'; a path with "
        "no wildcard names a single file or directory exactly as before. Shell brace expansion "
        "('{a,b}') is NOT supported. Must be relative — an absolute path or one containing '..' is "
        "rejected; use 'remote' to point at files outside this solution's own repository."
    )

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, v: str) -> str:
        """Reject absolute paths and '..' traversal, and normalize separators
        (see `validate_relative_path`) — the same guard `SourceModel
        .source_path` already applies. Glob wildcards pass through untouched.
        """
        return validate_relative_path(v)


class AssetMetaModel(PlatformBaseModel):
    """Asset metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique asset name, referenced from a workspace's spec.assets pool")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class AssetModel(PlatformBaseModel):
    """Root model for an asset configuration document."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2, frozen=True, description="API version for asset configuration"
    )
    kind: PlatformKind = Field(default=PlatformKind.ASSET, frozen=True, description="Platform kind (always 'asset')")
    meta: AssetMetaModel = Field(description="Asset metadata (name, annotations, labels, tags)")
    spec: AssetSpecModel = Field(description="Asset specification (source location, optional remote)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.ASSET)
