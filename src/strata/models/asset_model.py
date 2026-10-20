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

from pydantic import Field, StringConstraints, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.models.reference_fields import References, RemoteReference
from strata.models.solution_model import RemoteFetch, RemoteType
from strata.utils.path_safety import (
    validate_no_path_traversal,
    validate_no_url_credentials,
    validate_not_option_like,
    validate_relative_path,
)


class AssetLocationModel(PlatformBaseModel):
    """The git repository an asset's files live in, declared on the asset itself
    (docs/design/asset-management.md) — instead of a remote named in `strata.yaml`.

    Lets one small asset document per customer point at that customer's own
    repository without growing the manifest. A `SolutionRemoteModel` minus
    `name` (the asset's own name is the identity) and minus what it cannot
    honour: only `type: git`.
    """

    type: RemoteType = Field(RemoteType.GIT, description="Only 'git' is supported for an asset location")
    url: str = Field(min_length=1, description="Git repository URL")
    reference: str = Field(
        min_length=1,
        description="Branch, tag or commit SHA the files are taken at. A `pins.assets` entry in a Version "
        "document overrides it (only with fetch: strata).",
    )
    fetch: RemoteFetch = Field(
        RemoteFetch.STRATA,
        description="'strata' clones it; 'external' means CI already checked it out at "
        ".strata/assets/<asset name>/<reference> and strata only reads it.",
    )
    integration: Annotated[PlatformName, References(PlatformKind.INTEGRATION)] | None = Field(
        None,
        description="Name of an Integration providing credentials for cloning this repository (must declare "
        "the 'sources' capability). Omit for a public repository or ambient git credentials. Credentials "
        "are never inline: the url must not embed a password. Ignored with fetch: external, which clones "
        "nothing.",
    )

    @field_validator("type")
    @classmethod
    def validate_git_only(cls, v: RemoteType) -> RemoteType:
        """`resolve_remote()` can only fetch git."""
        if v is not RemoteType.GIT:
            raise ValueError(f"An asset location supports only type 'git', not '{v.value}'.")
        return v

    @field_validator("reference")
    @classmethod
    def validate_reference_no_traversal(cls, v: str) -> str:
        """`reference` is a directory segment of the checkout path, same rule as a remote's."""
        validate_no_path_traversal(v)
        validate_not_option_like(v, "reference")
        return v

    @field_validator("url")
    @classmethod
    def validate_url_not_option_like(cls, v: str) -> str:
        """`url` is a positional argument to `git clone`; see `validate_not_option_like()`."""
        validate_not_option_like(v, "url")
        validate_no_url_credentials(v, "url")
        return v


class AssetSpecModel(PlatformBaseModel):
    """Asset specification: where its files live, plus optional documentation data."""

    description: str | None = Field(None, description="Optional description for documentation purposes")
    remote: Annotated[PlatformName, RemoteReference()] | None = Field(
        None,
        description="Remote this asset's files live under (SourceModel's own field/meaning, reused "
        "unchanged). None means this solution's own repository.",
    )
    location: AssetLocationModel | None = Field(
        None,
        description="The git repository this asset's files live in, declared here rather than as a manifest "
        "remote. Mutually exclusive with 'remote'; with neither, the files are in this solution's own "
        "repository.",
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

    @model_validator(mode="after")
    def validate_remote_xor_location(self) -> "AssetSpecModel":
        """The files live in one place: a manifest remote or the asset's own location, not both."""
        if self.remote is not None and self.location is not None:
            raise ValueError("An asset declares either 'remote' or 'location', not both.")
        return self


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
