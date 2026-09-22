#!/usr/bin/env python3
"""Pydantic models for version-pin configuration validation.

A `version` document is the single place an operator changes to perform an
upgrade: it pins the images, charts, remote refs and tool versions a
deployment resolves to. Every other document declares *what* it needs; this
one declares *which version of it*.

Design driver — v1's real production file (`haven/versions/prd.yaml`) carried
a hand-written rationale on all 14 of its pins ("HELD — postgres majors need
pg_upgrade", "UNVERIFIED — could not confirm the chart version upstream"),
and warned in its own header that `strata versions lock`/`refresh` *rewrite
the file and strip comments*. The reasoning was the most valuable content in
the file and the tooling destroyed it on every run. So here that rationale is
**schema, not comments**: `status`/`reason`/`available`/`reviewed` survive any
machine rewrite, and a held pin must say why it is held.

v1 modelled this as two kinds (`version-manifest` + `version-lock`, 332 lines
across two files, backed by a 424-line service, a 454-line controller and
seven CLI commands) supporting pointer-vs-inline locks, rollback snapshots,
canary scope overlays, waves and floating `track: latest` pins. A census of
every repository found **one** `kind: version` document in production, **zero**
`version-lock`/`version-manifest` documents, and not one use of those optional
features. v2 ports the one shape that is actually used.

Scope rule — **a pin may only target something strata itself materialises.**
Everything else is already on disk before strata's process starts, so a pin
could only misreport it. In the reference CI, `actions/checkout` places the
solution repo, `setup-strata` places the CLI, and `setup-terraform`/
`setup-helm` place the tool binaries; strata fetches remotes (`fetch: strata`)
and renders image/chart versions. So only those last three are pinnable, and
`fetch` is the discriminator that decides it for remotes.

Three v1 fields are deliberately NOT ported:

- ``spec.ring`` — duplicated `meta.name` in the only real document (both
  "prd"), the same redundancy dropped from `tenant.spec.code`. Rings resolve
  against a progression registry (v1's `promotion_model.py`) that v2 has not
  ported. Ring/promotion is planned as a follow-up for rollout automation;
  when it lands it attaches *around* this model (which version document a
  ring selects) rather than inside it, so nothing here needs to change.
- ``track: latest`` / ``resolved`` / ``resolved_at`` / ``resolved_sha`` — a
  floating-pin mechanism with zero real use. A pin that floats is not a pin;
  `status: current` plus a refreshed `available` covers the intent without a
  second resolution path.
- ``pins.tools`` — zero real use, and it fails the scope rule above: CI
  installs the tool binaries, so strata cannot make a tool be a given version.
  Declaring the version a recipe expects stays on `ProvisionerModel.version`,
  where it is an **assertion** strata verifies against what is present.
  Installing software is not strata's job.
"""

from collections.abc import Iterator
from datetime import date
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    validate_kind_matches,
)

#: Pin categories, in the order they are reported. Each maps to exactly one
#: field elsewhere in the schema — see `VersionPinsModel`.
PIN_CATEGORIES = ("images", "charts", "remotes")


class VersionPinStatus(str, Enum):
    """Why a pin sits at the version it does.

    The vocabulary is taken from the real file's own comments, which already
    used exactly these three states in prose ("HELD —", "UNVERIFIED —", and
    plain bumps/"already latest").
    """

    CURRENT = "current"
    """Pinned at the intended version; nothing is being held back."""

    HELD = "held"
    """Deliberately behind the available version. Requires a `reason`."""

    UNVERIFIED = "unverified"
    """The pinned version could not be confirmed upstream — do not trust it
    for an automated bump. Requires a `reason`."""


class VersionPinModel(PlatformBaseModel):
    """One pinned version, plus the reasoning a machine rewrite must preserve.

    Accepts a bare string as shorthand, so the simple majority stays terse::

        images:
          caddy: caddy:2-alpine                    # shorthand
          db:                                       # structured
            version: docker.io/library/postgres:16-alpine
            status: held
            available: 18.6-alpine
            reason: "postgres majors need pg_upgrade/dump-restore"
            reviewed: 2026-09-08

    Both forms produce the same model, so consumers never branch on shape.
    """

    version: str = Field(min_length=1, description="The pinned value: image reference, chart version, or git ref")
    status: VersionPinStatus = Field(
        default=VersionPinStatus.CURRENT, description="Why the pin sits here: current, held, or unverified"
    )
    available: str | None = Field(
        None,
        min_length=1,
        description="Version known to be available upstream. Refresh tooling may rewrite this field freely; "
        "it must never rewrite 'reason'.",
    )
    reason: str | None = Field(
        None,
        min_length=1,
        description="Why this pin is held or unverified. Required for those states — an unexplained hold is "
        "the knowledge loss this model exists to prevent.",
    )
    reviewed: date | None = Field(None, description="Date a human last reviewed this pin (ISO 8601)")

    @model_validator(mode="before")
    @classmethod
    def coerce_scalar_shorthand(cls, value: Any) -> Any:
        """Treat a bare string as `{version: <string>}`."""
        if isinstance(value, str):
            return {"version": value}
        return value

    @model_validator(mode="after")
    def validate_reason_present_when_not_current(self) -> "VersionPinModel":
        """A held or unverified pin must say why.

        This is the whole point of the model: v1 lost exactly this information
        on every tooling run. Requiring it means a machine rewrite cannot
        produce a file that has forgotten why something is pinned back.
        """
        if self.status is not VersionPinStatus.CURRENT and not self.reason:
            raise ValueError(f"A pin with status '{self.status.value}' requires a 'reason' explaining why.")
        return self


class VersionPinsModel(PlatformBaseModel):
    """Pins grouped by what they target.

    Each category resolves against a different part of the schema, all of
    which already exist — nothing here needs a new override subtree (v1
    delivered pins by rewriting `Environment.spec.overrides`, a subtree v2
    did not port):

    - ``images``  -> `ModuleServiceModel.image`
    - ``charts``  -> `SourceModel.chart_version`
    - ``remotes`` -> `SolutionRemoteModel.reference`, and only where that
      remote is `fetch: strata`. A `fetch: external` remote is placed by CI
      before strata runs, so pinning it cannot take effect — resolution
      rejects such a pin rather than ignoring it silently.

    Keys are the target's own name, so a pin is a plain identity reference
    like every other cross-document link in v2 (ADR-0015).
    """

    images: dict[PlatformName, VersionPinModel] | None = Field(
        default=None, description="Container image pins. Key = ModuleServiceModel.name."
    )
    charts: dict[PlatformName, VersionPinModel] | None = Field(
        default=None, description="Helm chart version pins. Key = Module document name."
    )
    remotes: dict[PlatformName, VersionPinModel] | None = Field(
        default=None,
        description="Git/OCI ref pins. Key = SolutionRemoteModel.name. Only valid for remotes strata "
        "materialises itself (fetch: strata).",
    )

    def iter_pins(self) -> Iterator[tuple[str, str, VersionPinModel]]:
        """Yield `(category, target_name, pin)` for every pin declared.

        Used by resolution to apply pins and by logging to report them; a
        single traversal keeps the two from drifting apart.
        """
        for category in PIN_CATEGORIES:
            entries: dict[str, VersionPinModel] | None = getattr(self, category)
            for name, pin in (entries or {}).items():
                yield category, name, pin


class VersionSpecModel(PlatformBaseModel):
    """Specification for a version document."""

    pins: VersionPinsModel = Field(
        default=VersionPinsModel(),
        description="Version pins grouped by target category. May be empty — a new document with nothing "
        "pinned yet is valid.",
    )
    hash: str | None = Field(
        None,
        min_length=1,
        description="SHA-256 of the canonical pins payload, written by tooling. When present, resolution can "
        "detect that the file changed since it was locked. Absent on hand-written documents.",
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")


class VersionMetaModel(PlatformBaseModel):
    """Metadata for a version document."""

    name: PlatformName = Field(
        description="Unique version-document name, referenced by 'Deployment.spec.version'. Typically the "
        "ring or channel it represents (e.g. 'prd')."
    )
    annotations: dict[str, Any] | None = Field(None, description="Optional free-form annotations")
    labels: dict[str, Any] | None = Field(None, description="Optional labels (key-value pairs for classification)")
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")


class VersionModel(PlatformBaseModel):
    """Root model for a version configuration document."""

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2, frozen=True, description="API version for version configuration"
    )
    kind: PlatformKind = Field(default=PlatformKind.VERSION, description="Platform kind (always 'version')")
    meta: VersionMetaModel = Field(description="Version metadata")
    spec: VersionSpecModel = Field(description="Version specification")

    @model_validator(mode="after")
    def validate_kind(self) -> "VersionModel":
        """Reject a document declaring a different kind (ADR-0016)."""
        validate_kind_matches(self.kind, PlatformKind.VERSION)
        return self
