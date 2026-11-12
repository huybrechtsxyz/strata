#!/usr/bin/env python3
"""Image tag policy (docs/design/image-tag-policy.md).

An opt-in workspace guard rail on the tag of every image a deployment runs. The rules read the solution (the
effective images of a deployment) and, given a build, the images its rendered charts run
(docs/work/helm-image-delivery.md, plan item 9). Unlike the security gates there is no `on_missing_data`: a build
without rendered charts is judged on the solution alone, as before.
"""

import re
from datetime import date
from typing import Annotated, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import PlatformBaseModel, expires_date_text


class ImageTagAllowEntryModel(PlatformBaseModel):
    """One image exempt from the image tag policy. `name` and `reason` are required; add `expires` (an ISO date)
    to stop honouring the entry after that day.

    Design notes:
    Mirrors `CveAllowedEntryModel`: an exemption is a reviewed governance decision, so the reason is
    schema, not a comment, and a malformed `expires` fails at load time instead of acting as "never".
    """

    name: str = Field(
        min_length=1,
        description="The image to exempt: the name of an artifact, or of a module service, as `strata deploy "
        "images` lists it, or `REFERENCE/CONTAINER` for an image a rendered chart runs (`whoami/shipper`)",
    )
    reason: str = Field(min_length=1, description="Why this image may break the policy")
    expires: str | None = Field(None, description="ISO date after which this entry is ignored (e.g. '2026-12-31')")

    @field_validator("expires", mode="before")
    @classmethod
    def accept_an_unquoted_yaml_date(cls, value: object) -> object:
        return expires_date_text(value)

    @field_validator("expires")
    @classmethod
    def validate_expires_is_iso_date(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"expires '{value}' is not a valid ISO date (e.g. '2026-12-31'): {exc}") from exc
        return value


class ImageTagPolicyModel(PlatformBaseModel):
    """Fails `build run` and `deploy run` when an image a deployment runs has no tag, has a floating tag, or does
    not have the tag shape you require. `image_tag_policy: {}` is already a useful policy: every image needs a
    tag, and the mutable aliases are rejected.

    Design notes:
    `WorkspaceSpecModel.image_tag_policy`, evaluated over `effective_images()` (docs/design/image-tag-policy.md).
    Named `image_tag_policy`, not `image_policy`, to stay clear of `image_gate_policy`, which gates on a
    vendor's open issues for the images in `sbom.json`.
    """

    schema_rules: ClassVar[tuple[str, ...]] = ("Names in `allow` must be unique.",)

    enforcement: Literal["deny", "warn"] = Field(
        default="deny", description="'deny' stops the run on a breach; 'warn' reports and continues"
    )
    origins: list[Literal["artifact", "module-service", "chart"]] | None = Field(
        None,
        min_length=1,
        description="Which images the policy covers: `artifact` for a `kind: artifact` image, `module-service` for "
        "a service image a module declares, `chart` for an image a Helm chart runs, read from the build's "
        "`sbom.json` unless the workspace sets `sbom.render_charts: false`. Omit to cover all three.",
    )
    require_tag: bool = Field(
        default=True,
        description="Fail an image that has no tag and no `kind: version` pin to take one from. A digest "
        "(`sha256:...`) counts as a tag here: it pins the image even more firmly.",
    )
    require_digest: bool = Field(
        default=False,
        description="Fail an image whose reference has no digest (`name@sha256:...` or "
        "`name:tag@sha256:...`), so what runs is fixed byte for byte, not by a tag a registry can move",
    )
    reject_tags: list[Annotated[str, Field(min_length=1)]] | None = Field(
        None,
        description="Tags that are not allowed, compared without regard to case. Omit to reject the mutable "
        "aliases `latest`, `main`, `master`, `dev`, `develop`, `staging`, `edge`, `nightly` and `canary`. Set an "
        "empty list to reject none. A digest is never rejected, and neither is a tag that merely is not a "
        "semantic version, such as a build number. With `tag@digest` only the tag is compared.",
    )
    tag_pattern: str | None = Field(
        None,
        min_length=1,
        description="Regular expression a tag must match, searched anywhere in the tag (anchor it with `^` and "
        "`$`). A digest is not checked against it; with `tag@digest` the tag is.",
    )
    require_pin: bool = Field(
        default=False,
        description="Require the tag to come from a `kind: version` pin (or `--pin`), so what a ring runs is "
        "always decided by its version document. Not applied to a `chart` image: strata cannot pin an image "
        "inside a chart; the chart's own pin fixes it.",
    )
    rings: list[Annotated[str, Field(min_length=1)]] | None = Field(
        None,
        min_length=1,
        description="Apply the policy only to deployments whose version document is in one of these rings "
        "(`spec.promotion.ring`). Omit to apply it to every deployment. A deployment with no ring is always "
        "evaluated.",
    )
    allow: list[ImageTagAllowEntryModel] | None = Field(
        None, description="Images exempt from the policy, each with a reason and an optional expiry"
    )
    on_unknown_image: Literal["skip", "warn", "block"] = Field(
        default="warn",
        description="What to do about a Helm module that declares no service `image` or `artifact`: strata does "
        "not know the images its chart deploys, so it cannot judge them. 'block' stops the run, 'warn' reports "
        "and continues, 'skip' only notes it in verbose and JSON output. Independent of `enforcement`; `allow` "
        "does not exempt a module. Declare the chart's images under the module's `services` to have them "
        "judged.\n\n"
        "Design notes: docs/work/helm-image-delivery.md, option B. Mirrors `image_gate_policy.on_unknown_image`.",
    )

    @field_validator("reject_tags")
    @classmethod
    def normalise_reject_tags(cls, value: list[str] | None) -> list[str] | None:
        """Tags are compared lower-cased, so store them that way."""
        return None if value is None else [tag.lower() for tag in value]

    @field_validator("tag_pattern")
    @classmethod
    def validate_tag_pattern_compiles(cls, value: str | None) -> str | None:
        """Fail at load time, not the first time a tag is checked."""
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"tag_pattern '{value}' is not a valid regular expression: {exc}") from exc
        return value

    @model_validator(mode="after")
    def validate_unique_allow_names(self) -> "ImageTagPolicyModel":
        """Two entries for one image would make the reason and expiry ambiguous."""
        names = [entry.name for entry in self.allow or []]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"allow has more than one entry for: {', '.join(duplicates)}")
        return self
