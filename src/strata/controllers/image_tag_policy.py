#!/usr/bin/env python3
"""Evaluate `spec.image_tag_policy` over a deployment's effective images (docs/design/image-tag-policy.md).

Pure: it reads only the rows `effective_images()` returns, the policy and the deployment's ring, so it
needs no solution, network or credential. Callers (`build run`, `deploy run`, `strata policy check`,
`deploy images`) fetch the rows and the ring and decide when to run it.
"""

import re
from collections.abc import Sequence
from datetime import date

from strata.controllers.effective_images import effective_images
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import resolve_version
from strata.integrations.resolved_context import EffectiveImage
from strata.models.deployment_model import DeploymentModel
from strata.models.image_tag_policy_model import ImageTagAllowEntryModel, ImageTagPolicyModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.sbom_utils import MUTABLE_ALIAS_TAGS


def check_image_tag_policy(
    context: SolutionContext,
    deployment: DeploymentModel,
    workspace: WorkspaceModel,
    *,
    pinned_by_flag: bool = False,
    images: Sequence[EffectiveImage] | None = None,
) -> Diagnostics:
    """Evaluate the workspace's `image_tag_policy` for `deployment`; nothing to report when it is unset.

    `deployment` is the resolved model, already carrying a `--pin` in `spec.version`, and `pinned_by_flag`
    says whether it did (as `effective_images()` takes them). Pass `images` when the caller already has the
    deployment's `effective_images()` rows, so they are not resolved twice.
    """
    policy = workspace.spec.image_tag_policy
    if policy is None:
        return Diagnostics()
    if images is None:
        images = effective_images(context, deployment, pinned_by_flag=pinned_by_flag)
    return evaluate_image_tag_policy(images, policy, deployment_ring(context, deployment))


def deployment_ring(context: SolutionContext, deployment: DeploymentModel) -> str | None:
    """The ring of the deployment's version document (`spec.promotion.ring`), or `None` when it has none."""
    version = resolve_version(context, deployment)
    promotion = version.spec.promotion if version is not None else None
    return promotion.ring if promotion is not None else None


def evaluate_image_tag_policy(
    images: Sequence[EffectiveImage],
    policy: ImageTagPolicyModel,
    ring: str | None,
    *,
    today: date | None = None,
) -> Diagnostics:
    """One finding per breached rule per image: an error for `deny`, a warning for `warn`.

    Args:
        images: The deployment's `effective_images()` rows.
        policy: The workspace's `image_tag_policy`.
        ring: The deployment's `spec.promotion.ring` from its version document, or `None` when it has none.
            A deployment with no ring is always evaluated; one whose ring is not in `policy.rings` is not,
            and gets a single info finding saying so.
        today: The day an `allow` entry's `expires` is compared with; defaults to the current date.
    """
    diagnostics = Diagnostics()
    if policy.rings is not None and ring is not None and ring not in policy.rings:
        diagnostics.info(
            f"image_tag_policy: not applied, the deployment's ring '{ring}' is not one of {', '.join(policy.rings)}.",
            code="image_tag_policy_not_applied",
        )
        return diagnostics

    report = diagnostics.error if policy.enforcement == "deny" else diagnostics.warning
    allowed = {entry.name: entry for entry in policy.allow or []}
    rejected = set(MUTABLE_ALIAS_TAGS) if policy.reject_tags is None else set(policy.reject_tags)
    pattern = re.compile(policy.tag_pattern) if policy.tag_pattern is not None else None
    day = today or date.today()

    for image in images:
        if policy.origins is not None and image.origin not in policy.origins:
            continue
        entry = allowed.get(image.name)
        note = ""
        if entry is not None:
            if _is_active(entry, day):
                continue
            note = f" (its allow entry expired on {entry.expires})"

        for code, problem in _problems(image, policy, rejected, pattern):
            report(f"image_tag_policy: {_describe(image)} {problem}{note}", code=code)
    return diagnostics


def _is_active(entry: ImageTagAllowEntryModel, today: date) -> bool:
    return entry.expires is None or date.fromisoformat(entry.expires) >= today


def _is_digest(tag: str) -> bool:
    """A tag never contains `:`, so a value that does is a digest (`sha256:...`)."""
    return ":" in tag


def _problems(
    image: EffectiveImage, policy: ImageTagPolicyModel, rejected: set[str], pattern: re.Pattern[str] | None
) -> list[tuple[str, str]]:
    tag = image.image_tag
    if not tag:
        if policy.require_tag:
            return [("image_without_tag", f"has no tag. {_fix(image)}")]
        return [("image_not_pinned", f"is not pinned. {_fix(image)}")] if policy.require_pin else []

    problems: list[tuple[str, str]] = []
    if not _is_digest(tag):
        if tag.lower() in rejected:
            problems.append(("image_floating_tag", f"has the floating tag '{tag}'. {_fix(image)}"))
        if pattern is not None and pattern.search(tag) is None:
            problems.append(("image_tag_pattern", f"has the tag '{tag}', which does not match '{pattern.pattern}'."))
    if policy.require_pin and image.tag_source != "pin":
        problems.append(("image_not_pinned", f"is not pinned (its tag '{tag}' is {_source(image)}). {_fix(image)}"))
    return problems


def _describe(image: EffectiveImage) -> str:
    used_by = f"; used by {', '.join(image.via)}" if image.via else ""
    return f"{image.origin} '{image.name}' ({image.image_name}{used_by})"


def _source(image: EffectiveImage) -> str:
    return "declared on the document" if image.tag_source == "declared" else "not set"


def _fix(image: EffectiveImage) -> str:
    """Where to put the tag: the pin that takes precedence, or the declaration itself."""
    if image.origin == "artifact":
        return f"Pin it under spec.pins.artifacts.{image.name} of a kind: version document, or set image_tag on the artifact."
    return f"Pin it under spec.pins.images.{image.name} of a kind: version document, or set the tag in the service's image."
