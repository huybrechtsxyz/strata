#!/usr/bin/env python3
"""The solution manifest — `strata.yaml` at the solution root.

This is the **bootstrap** document: the marker that says "a strata solution
starts here", plus the minimum needed to find everything else. It is found by
walking up from the current directory (like `go.mod`, `Cargo.toml`,
`package.json`), not by discovery — discovery rules live downstream of it.

v1 had this concept but only as machine-local runtime state:
`.strata/solution.json` is a real `kind: solution` document with `meta.name`
and `spec.repositories[]`, but v1's `.gitignore` lists
`**/.strata/solution.json`, so it never survives a clone. Everything durable
therefore had to be duplicated into a committed `config/remotes.yaml`, and
in production the two drifted — the same remote is `type: bundled` in
`remotes.yaml` and `type: gitops` in `solution.json`, with the location
recorded in both (`deploy_path` vs `path`). `remotes.yaml`'s own comment
concedes it: "These values must match the `path` recorded for the same
repository in .strata/solution.json, which is what `strata repo add` wrote."
Committing the manifest collapses those two registries into one.

No lock file counterpart is planned. `reference` is pinned here and the
checkout location is a CLI convention, so a lock would mostly re-record
declared facts — which is exactly what produced v1's drift.

**Scope discipline.** This file answers "what/where is this solution",
never "what rules govern it". Platform policy — provider/topology
registries, path conventions, stores — stays in `Configuration`. Same split
as `go.mod` (module identity and deps) versus build configuration. Resist
adding policy fields here.

**Known constraint (ADR-0015).** Remotes supply *artifacts* (Terraform
modules, charts, copied files), not *documents*. Strata documents are
discovered from the solution repo only, so a Module/ProviderConfig document
cannot currently live in a shared repository the way v1's
`ModuleReferenceModel.file: "@repo/..."` allowed. The painful case is
org-wide governance registries owned by a platform team. The planned
mitigation is remote-qualified identity (`providers: ["@platform/azure"]`,
Bazel's `@repo//pkg:target` precedent) — deferred until something needs it,
but the loader's index must be keyed `(remote, kind, name)` with
`remote=None` for local so it stays an additive change.
"""

from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)
from strata.utils.names import check_unique_names
from strata.utils.path_safety import validate_relative_path


class RemoteType(str, Enum):
    """Remote artifact-source type — *what kind of thing* a remote is.

    Ports v1's `RemoteType` with clearer names: `GITOPS` -> `GIT` (GitOps is
    a delivery methodology, not a source type — the thing being declared is
    simply a git repository), `CONTAINER` -> `OCI` (an OCI registry serves
    container images, Helm charts and arbitrary artifacts alike, so the
    narrower "container" was misleading), and `BUNDLED` -> `LOCAL`.

    Deliberately says nothing about *who fetches* the remote — see
    `RemoteFetch`, which v1 conflated into this same field.
    """

    GIT = "git"
    OCI = "oci"
    HELM = "helm"
    LOCAL = "local"


class RemoteFetch(str, Enum):
    """Who is responsible for materialising a remote on disk.

    Split out of `type` because v1 conflated the two, and real deployments
    had to lie about the type to get the behaviour they needed. A production
    `config/remotes.yaml` declares a git repository as `type: bundled`
    purely so strata skips its own fetch: in CI the repo is already cloned by
    Azure Pipelines' `resources.repositories`/`checkout`, and strata has no
    git credentials of its own there. Its comment records the cost — pinning
    to a `reference` and dirty-working-tree gating both silently stopped
    applying, because "bundled" means "trust whatever is on disk".

    With this split, that case is stated honestly: `type: git` (keep the URL
    and the pinned ref) plus `fetch: external` (somebody else clones it).
    """

    STRATA = "strata"
    EXTERNAL = "external"


class SolutionRemoteModel(PlatformBaseModel):
    """A named remote artifact source, referenced elsewhere as ``@<name>/<path>``.

    Declares *identity and location only* — "where `infra` is, at which ref,
    authenticated how". Selecting *what* to take from it belongs at the use
    site (`SourceModel.remote`/`source_path`, `ModuleFileModel.source`),
    which names this remote via `@<name>/...` — see `strata.utils.repo_refs`.

    This split is the near-universal pattern across comparable tooling: Flux
    (`GitRepository`/`OCIRepository` + `sourceRef`), Bazel
    (`@repo_name//pkg:target`), Nix flake `inputs`, and Helm's own
    ``repositories.yaml`` + ``@alias`` dependency syntax. It buys three
    things inlined URLs cannot: one place to rotate credentials, one place to
    redirect upstream -> internal mirror (airgap/fork), and a single ref per
    repo per solution so two modules cannot silently pull different versions
    of the same repository.

    Remotes live here rather than in `Configuration` because of bootstrap
    ordering: v1's own `solution.json` registers a `config` repository, i.e.
    Configuration itself can live in a remote — so remotes must resolve
    before Configuration can be loaded.

    Two v1 fields are deliberately NOT ported:

    - ``source_path`` — declaring a path on the *remote* makes it "a specific
      thing inside a repo" rather than "a repo", so it cannot be reused for a
      second artifact in that same repository. Selection stays at the use site.
    - ``deploy_path`` — the local checkout location is CLI convention, not
      declarative schema. v1 recorded it in both `remotes.yaml` and
      `.strata/solution.json` and required humans to keep them equal; when
      they diverged, builds failed with "has not been fetched yet" against an
      already-fetched repo.

    Also deferred — ``conventions`` (release/quality-gate tag regexes), a real
    v1 feature consumed by its policy engine and ``strata repo status``,
    neither of which exists in v2.

    Example::

        remotes:
          - name: infra
            type: git
            url: https://github.com/org/infra.git
            reference: v2.1.0
            integration: corp-git
    """

    name: PlatformName = Field(
        description="Remote name, used as the '@<name>' prefix at every use site. Required — v1 made this "
        "field Optional, and its own get_remote_map() then silently skipped unnamed entries."
    )
    type: RemoteType = Field(description="What kind of source this is: git, oci, helm, or local")
    url: str = Field(
        min_length=1,
        description="Remote location: a URL for git/oci/helm, or a solution-relative path for 'local'.",
    )
    reference: str | None = Field(
        None,
        min_length=1,
        description="Ref every artifact from this remote is taken at (git branch/tag/commit SHA, or OCI "
        "tag/digest). Required for git/oci. Deliberately the ONLY place a ref is declared — there is no "
        "per-use-site override, so one remote resolves to exactly one tree per solution.",
    )
    fetch: RemoteFetch = Field(
        default=RemoteFetch.STRATA,
        description="Who materialises this remote on disk: 'strata' (default) fetches it, 'external' means "
        "CI or the developer already checked it out and strata must only read it. Use 'external' instead of "
        "misdeclaring the type when a pipeline clones the repo itself.",
    )
    integration: PlatformName | None = Field(
        None,
        description="Name of an Integration providing credentials for this remote (must declare the "
        "'sources' capability). Omit for public/unauthenticated remotes and for type 'local'. Credentials "
        "are never inline here — Integration is strata's single credential mechanism.",
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")

    @model_validator(mode="after")
    def validate_reference_for_type(self) -> "SolutionRemoteModel":
        """`reference` is required for git/oci and meaningless for helm/local.

        A git/oci remote at a ref IS one immutable tree, so the ref is part of
        its identity. A helm remote is an index serving many (chart, version)
        pairs — the version is a per-chart *selection*
        (`SourceModel.chart_version`), not remote identity. A local remote is
        whatever is on disk.
        """
        if self.type in (RemoteType.GIT, RemoteType.OCI):
            if self.reference is None:
                raise ValueError(
                    f"Remote '{self.name}': 'reference' is required for type '{self.type.value}' "
                    "(pin a branch, tag, commit SHA or digest)."
                )
        elif self.reference is not None:
            raise ValueError(
                f"Remote '{self.name}': 'reference' is not valid for type '{self.type.value}'. "
                "Chart versions are selected per-use via SourceModel.chart_version."
            )
        return self

    @model_validator(mode="after")
    def validate_local_remote(self) -> "SolutionRemoteModel":
        """A 'local' remote is an on-disk solution-relative path.

        Rejects absolute paths and '..' traversal (v1 applied no validation
        at all here, so a bundled remote could point anywhere on the host),
        rejects `integration` since on-disk files need no credentials, and
        rejects a non-default `fetch` since there is nothing to fetch.
        """
        if self.type is RemoteType.LOCAL:
            validate_relative_path(self.url)
            if self.integration is not None:
                raise ValueError(f"Remote '{self.name}': 'integration' is not valid for type 'local'.")
            if self.fetch is not RemoteFetch.STRATA:
                raise ValueError(
                    f"Remote '{self.name}': 'fetch' is not valid for type 'local' — it is already on disk."
                )
        return self


class SolutionDiscoveryModel(PlatformBaseModel):
    """Tuning for the document discovery scan (ADR-0015).

    Only `exclude` — deliberately no `include`. An include list overlaps with
    "where the solution root is", and include/exclude precedence is a
    well-known source of confusion (VS Code's own `files.exclude` vs
    `search.exclude` vs `.gitignore` interaction being the obvious example).
    One mechanism.

    Patterns are **additive** to the loader's built-in ignores (`.git`,
    `.venv`, `build`, `.strata`, tool caches, ...), never a replacement —
    otherwise writing `exclude: ["foo"]` would silently re-enable scanning
    `.git`. The built-ins are the floor; this raises it.

    Kept in the manifest rather than a sibling `.strataignore` file: the
    manifest is already the single bootstrap document, and a second
    must-find-first file would undermine that. (Flux uses `.sourceignore`
    because its source is a remote repo it does not own; a solution owns its
    own root.)

    The common real case is scaffolding — a `templates/` or `examples/`
    directory holding strata-shaped YAML with placeholders, which would
    otherwise fail to parse or index as bogus documents.
    """

    exclude: list[str] | None = Field(
        None,
        description="Glob patterns matched against each path relative to the solution root, POSIX-style "
        "(e.g. 'templates/**', 'examples', '*.generated.yaml'). A matching directory is not descended into. "
        "Added to the loader's built-in ignores, never replacing them. Note: these are fnmatch-style globs, "
        "not full gitignore syntax \u2014 there is no negation ('!') and no leading-slash anchoring.",
    )


class SolutionSpecModel(PlatformBaseModel):
    """Solution specification: where Configuration lives, and what this solution is composed of."""

    configuration: str = Field(
        "config",
        description="Solution-relative path to the Configuration document(s) — either a single file or a "
        "directory whose 'kind: configuration' documents are all merged. Defaults to 'config', which is "
        "what real solutions use. Overridden by the CLI's --config-file/--config-path.",
    )
    discovery: SolutionDiscoveryModel | None = Field(
        None, description="Optional discovery tuning (extra exclude patterns)"
    )
    remotes: list[SolutionRemoteModel] | None = Field(
        None,
        description="Named remote artifact sources (git/oci/helm/local), referenced elsewhere as '@<name>/<path>'",
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")

    @field_validator("configuration")
    @classmethod
    def validate_configuration_path(cls, v: str) -> str:
        """The configuration pointer must stay inside the solution."""
        return validate_relative_path(v)

    @model_validator(mode="after")
    def validate_unique_remote_names(self) -> "SolutionSpecModel":
        """Remote names must be unique — they are the '@<name>' resolution key."""
        if self.remotes:
            check_unique_names([r.name for r in self.remotes], "remote names in solution")
        return self


class SolutionMetaModel(PlatformBaseModel):
    """Solution metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique solution name")
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional list of tags")


class SolutionModel(PlatformBaseModel):
    """Root model for the solution manifest (`strata.yaml`)."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for the solution manifest",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.SOLUTION,
        frozen=True,
        description="Platform kind (always 'solution')",
    )
    meta: SolutionMetaModel = Field(description="Solution metadata (name, annotations, labels, tags)")
    spec: SolutionSpecModel = Field(description="Solution specification (configuration pointer, remotes)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.SOLUTION)
