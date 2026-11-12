#!/usr/bin/env python3
"""Evaluate `spec.image_tag_policy` over a deployment's effective images (docs/design/image-tag-policy.md).

`evaluate_image_tag_policy()` is pure: it reads only the rows `effective_images()` returns, the Helm modules
`unknown_image_modules()` names, the policy and the deployment's ring, so it needs no solution, network or
credential. Callers (`build run`, `deploy run`, `strata policy check`, `deploy images`) fetch the rows and the ring
and decide when to run it. `check_image_tag_policy(..., build_path=)` adds the images the build's rendered charts
run (docs/work/helm-image-delivery.md, plan item 9d); `build run`, `deploy run` and `policy check` pass it (9e, 9f).
"""

import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from strata.controllers.effective_images import (
    effective_images,
    rendered_chart_images,
    sbom_written_at,
    unknown_image_modules,
    with_rendered_images,
)
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import resolve_version
from strata.integrations.resolved_context import EffectiveImage, UnknownImageModule
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
    unknown: Sequence[UnknownImageModule] | None = None,
    build_path: Path | None = None,
) -> Diagnostics:
    """Evaluate the workspace's `image_tag_policy` for `deployment`; nothing to report when it is unset.

    `deployment` is the resolved model, already carrying a `--pin` in `spec.version`, and `pinned_by_flag`
    says whether it did (as `effective_images()` takes them). Pass `images` when the caller already has the
    deployment's `effective_images()` rows, so they are not resolved twice, and `unknown` when it already has
    its `unknown_image_modules()`.

    With `build_path`, the images the build's rendered charts run are judged too, read from its `sbom.json`
    (`rendered_chart_images()`, `with_rendered_images()`). A build without an SBOM, or without rendered charts,
    is judged on the solution alone. An SBOM that cannot be read is a warning, and the solution is judged. When
    rendered images are judged, an info finding says how many and which `sbom.json` they came from.
    """
    policy = workspace.spec.image_tag_policy
    if policy is None:
        return Diagnostics()
    if images is None:
        images = effective_images(context, deployment, pinned_by_flag=pinned_by_flag)
    if unknown is None:
        unknown = unknown_image_modules(context, deployment)
    diagnostics = Diagnostics()
    if build_path is not None:
        try:
            rendered = rendered_chart_images(context, build_path)
        except ValueError as exc:
            diagnostics.warning(
                f"image_tag_policy: the images rendered charts run are not judged: {exc}",
                code="image_tag_policy_sbom_unreadable",
            )
            rendered = None
        if rendered:
            images, unknown = with_rendered_images(images, unknown, rendered)
            # Which build the chart images came from: at deploy and in `policy check` it can be an old one.
            written = sbom_written_at(build_path)
            diagnostics.info(
                f"image_tag_policy: {len(rendered)} image(s) rendered charts run judged too, from "
                f"{build_path / 'sbom.json'}" + (f" written {written}" if written else "") + ".",
                code="image_tag_policy_rendered_images",
            )
    diagnostics.extend(evaluate_image_tag_policy(images, policy, deployment_ring(context, deployment), unknown=unknown))
    return diagnostics


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
    unknown: Sequence[UnknownImageModule] = (),
    today: date | None = None,
) -> Diagnostics:
    """One finding per breached rule per image: an error for `deny`, a warning for `warn`. Plus one finding per
    Helm module in `unknown`, as `on_unknown_image` says, when `origins` covers `module-service`.

    Args:
        images: The deployment's `effective_images()` rows.
        policy: The workspace's `image_tag_policy`.
        ring: The deployment's `spec.promotion.ring` from its version document, or `None` when it has none.
            A deployment with no ring is always evaluated; one whose ring is not in `policy.rings` is not,
            and gets a single info finding saying so.
        unknown: The deployment's `unknown_image_modules()`: Helm modules whose images strata does not know.
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

    if policy.origins is None or "module-service" in policy.origins:
        for module in unknown:
            _report_unknown_module(diagnostics, policy, module)
    return diagnostics


def unknown_module_message(module: UnknownImageModule) -> str:
    """What to say about a Helm module whose images strata does not know (`image_unknown_to_strata`)."""
    chart = " ".join(part for part in (module.chart, module.chart_version) if part)
    shown = f" (helm chart {chart})" if chart else " (helm)"
    return (
        f"module '{module.module}'{shown} in namespace '{module.namespace}' declares no images, so the images the "
        "chart deploys are known only to Helm and are not judged."
    )


def _report_unknown_module(diagnostics: Diagnostics, policy: ImageTagPolicyModel, module: UnknownImageModule) -> None:
    """`on_unknown_image` decides, apart from `enforcement`; `skip` is an info finding, as `image_gate_policy` has
    it, so the module still shows in verbose and JSON output."""
    message = (
        f"image_tag_policy: {unknown_module_message(module)} Declare them under spec.services[].image to have "
        "them judged, or set on_unknown_image: skip."
    )
    if policy.on_unknown_image == "block":
        diagnostics.error(message, code="image_unknown_to_strata")
    elif policy.on_unknown_image == "warn":
        diagnostics.warning(message, code="image_unknown_to_strata")
    else:
        diagnostics.info(message, code="image_unknown_to_strata")


def _is_active(entry: ImageTagAllowEntryModel, today: date) -> bool:
    return entry.expires is None or date.fromisoformat(entry.expires) >= today


def _problems(
    image: EffectiveImage, policy: ImageTagPolicyModel, rejected: set[str], pattern: re.Pattern[str] | None
) -> list[tuple[str, str]]:
    """`image_tag` is the tag part only and `image_digest` the digest part, so the tag rules judge a tag and never
    a digest, and a digest alone satisfies `require_tag`."""
    tag, digest = image.image_tag, image.image_digest
    if not tag and not digest:
        problems = [("image_without_tag", f"has no tag. {_fix(image)}")] if policy.require_tag else []
        if not problems and policy.require_pin and image.origin != "chart":
            problems.append(("image_not_pinned", f"is not pinned. {_fix(image)}"))
        if policy.require_digest:
            problems.append(("image_without_digest", f"has no digest. {_fix_digest(image)}"))
        return problems

    problems = []
    if tag:
        if tag.lower() in rejected:
            problems.append(("image_floating_tag", f"has the floating tag '{tag}'. {_fix(image)}"))
        if pattern is not None and pattern.search(tag) is None:
            problems.append(("image_tag_pattern", f"has the tag '{tag}', which does not match '{pattern.pattern}'."))
    if policy.require_digest and not digest:
        problems.append(("image_without_digest", f"has the tag '{tag}' but no digest. {_fix_digest(image)}"))
    # Strata cannot pin an image inside a chart; the chart's own pin (or a registry chart's digest) fixes it.
    if policy.require_pin and image.origin != "chart" and image.tag_source != "pin":
        shown = "@".join(part for part in (tag, digest) if part)
        problems.append(("image_not_pinned", f"is not pinned (its tag '{shown}' is {_source(image)}). {_fix(image)}"))
    return problems


def _fix_digest(image: EffectiveImage) -> str:
    """Where to put the digest, in the form that keeps the tag."""
    if image.origin == "artifact":
        return (
            f"Pin it as TAG@sha256:<hex> under spec.pins.artifacts.{image.name} of a kind: version document, or in "
            "the artifact's image_tag."
        )
    if image.origin == "chart":
        return "Set the image as TAG@sha256:<hex> in the chart's values through the module's configuration."
    return f"Write the service's image as NAME:TAG@sha256:<hex>, or pin it so under spec.pins.images.{image.name}."


def _describe(image: EffectiveImage) -> str:
    used_by = f"; used by {', '.join(image.via)}" if image.via else ""
    declared_only = "" if image.delivered else f"; declared only, {_declared_only_reason(image)}"
    return f"{image.origin} '{image.name}' ({image.image_name}{used_by}{declared_only})"


def _declared_only_reason(image: EffectiveImage) -> str:
    """Why a judged image may not be what runs: the chart decides, or no integration writes it."""
    if image.delivery is not None and image.delivery.startswith("helm module"):
        return "the chart decides which image runs"
    return image.delivery or "strata does not deliver it"


def _source(image: EffectiveImage) -> str:
    return "declared on the document" if image.tag_source == "declared" else "not set"


def _fix(image: EffectiveImage) -> str:
    """Where to put the tag: the pin that takes precedence, or the declaration itself."""
    if image.origin == "artifact":
        return f"Pin it under spec.pins.artifacts.{image.name} of a kind: version document, or set image_tag on the artifact."
    if image.origin == "chart":
        return (
            "Set the tag in the chart's values through the module's configuration, or pin a chart version that "
            "ships the tag you want."
        )
    return f"Pin it under spec.pins.images.{image.name} of a kind: version document, or set the tag in the service's image."
