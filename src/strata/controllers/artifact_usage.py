#!/usr/bin/env python3
"""Which artifacts some deployment uses, and a warning for the ones none does
(docs/work/artifact-gap-analysis.md, design 8).

"Uses" is exactly what `effective_images()` lists: a `store: artifact` variable a step receives, an enabled module
service's `artifact`, or an `${artifact:}` token in a document the deployment reaches. An artifact that no
deployment lists is not deployed anywhere, usually a leftover or a mistyped reference.

Not a semantic check: `semantic_checks.py` holds only a document index, and `effective_images()` needs the
solution context (and would close an import cycle through `solution_context.py`). The commands call this instead:
`strata validate` reports the warning, `strata version update` leaves the artifacts out of its candidates.
"""

from typing import cast

from strata.controllers.effective_images import effective_images
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import resolve_deployment
from strata.models.artifact_model import ArtifactModel
from strata.models.common_models import PlatformKind
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import StrataError


def used_artifacts(context: SolutionContext) -> set[str] | None:
    """The names of the artifacts some deployment's `effective_images()` lists, each deployment with its own
    `spec.version`. A `partial: true` base and a deployment with no workspace deploy nothing, so they are skipped.

    `None` when a deployment cannot be evaluated: an artifact it uses would otherwise be called unused, so the
    caller then reports nothing.
    """
    used: set[str] = set()
    for name in sorted(context.controller.index.names_of(PlatformKind.DEPLOYMENT)):
        try:
            deployment = resolve_deployment(context, name)
            if deployment.spec.partial or deployment.spec.workspace is None:
                continue
            rows = effective_images(context, deployment)
        except StrataError:
            return None
        used |= {row.artifact for row in rows if row.artifact is not None}
    return used


def unused_artifacts(context: SolutionContext) -> list[str]:
    """The artifacts no deployment uses, sorted; empty when that cannot be told (`used_artifacts()` is `None`)."""
    artifacts = set(context.controller.index.names_of(PlatformKind.ARTIFACT))
    if not artifacts:
        return []  # nothing to report, so no deployment needs resolving
    used = used_artifacts(context)
    if used is None:
        return []
    return sorted(artifacts - used)


def check_unused_artifacts(context: SolutionContext) -> Diagnostics:
    """One warning per artifact no deployment uses (`artifact_unused`), in the artifact's own file."""
    diagnostics = Diagnostics()
    index = context.controller.index
    for name in unused_artifacts(context):
        entry = index.get(PlatformKind.ARTIFACT, name)
        if entry is None:
            continue
        artifact = cast(ArtifactModel, entry.model)
        diagnostics.warning(
            f"Artifact '{name}' ({artifact.spec.image_name}) is used by no deployment: no store: artifact variable "
            "that a step receives, no module service artifact and no ${artifact:" + name + ".FIELD} token in a "
            "document a deployment reaches. Reference it, or remove it.",
            source=str(entry.source),
            location="meta.name",
            code="artifact_unused",
        )
    return diagnostics
