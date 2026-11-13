#!/usr/bin/env python3
"""Which artifacts some deployment uses, and a finding for the ones none does
(docs/archive/artifact-gap-analysis.md, design 8).

"Uses" is exactly what `effective_images()` lists: a `store: artifact` variable a step receives, an enabled module
service's `artifact`, or an `${artifact:}` token in a document the deployment reaches. An artifact that no
deployment lists is not deployed anywhere. That is one of two things:

- nothing in the solution names it at all: usually a leftover or a mistyped reference, a warning;
- some document names it (a `store: artifact` variable, a token, a module service, a version pin), but no
  deployment reaches that document yet: an image staged before its first deployment, which is information, so
  `validate --strict` passes on it.

Not a semantic check: `semantic_checks.py` holds only a document index, and `effective_images()` needs the
solution context (and would close an import cycle through `solution_context.py`). The commands call this instead:
`strata validate` reports the finding, `strata version update` leaves the unreferenced artifacts out of its
candidates.
"""

from typing import Any, cast

from strata.controllers.effective_images import effective_images
from strata.controllers.solution_context import SolutionContext
from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.value_controller import resolve_deployment
from strata.models.artifact_model import ArtifactModel
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import AnyDeploymentModel
from strata.models.module_model import ModuleModel
from strata.models.store_model import VariableStoreType
from strata.models.version_model import VersionModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import StrataError
from strata.utils.value_tokens import referenced_artifact_keys, split_artifact_token_key


def used_artifacts(context: SolutionContext) -> set[str] | None:
    """The names of the artifacts some deployment's `effective_images()` lists, each deployment with its own
    `spec.version`. A `partial: true` base and a deployment with no workspace deploy nothing, so they are skipped.

    `None` when a deployment cannot be evaluated: an artifact it uses would otherwise be called unused, so the
    caller then reports nothing.
    """
    used: set[str] = set()
    index = context.controller.index
    for name in sorted(index.names_of(PlatformKind.DEPLOYMENT)):
        entry = index.get(PlatformKind.DEPLOYMENT, name)
        if entry is not None and cast(AnyDeploymentModel, entry.model).spec.partial:
            continue  # a base deploys nothing, and a template base would not resolve on its own
        try:
            deployment = resolve_deployment(context, name)
            if deployment.spec.partial or deployment.spec.workspace is None:
                continue
            rows = effective_images(context, deployment)
        except StrataError:
            return None
        used |= {row.artifact for row in rows if row.artifact is not None}
    return used


def referenced_artifacts(index: DocumentIndex) -> dict[str, list[str]]:
    """Every artifact some document names, whether or not a deployment reaches it: `{artifact: [where, ...]}`.

    Each document is read as written, so a partial base and a document no deployment reaches count too:
    a `store: artifact` variable (`variable:KEY`), an `${artifact:NAME.FIELD}` token (`token:KIND/NAME`), a module
    service's `artifact` (`service:MODULE/SERVICE`) and a version document's `pins.artifacts` (`pin:VERSION`).
    An artifact's own document does not count as a reference to itself.
    """
    found: dict[str, list[str]] = {}

    def add(name: str, where: str) -> None:
        uses = found.setdefault(name, [])
        if where not in uses:
            uses.append(where)

    for entry in index.all():
        model = entry.written or entry.model
        kind, name = entry.ref.kind, entry.ref.name
        if kind == PlatformKind.ARTIFACT:
            continue
        dumped = model.model_dump(by_alias=True, mode="json")
        dumped.pop("meta", None)
        for key in referenced_artifact_keys(dumped):
            add(split_artifact_token_key(key)[0], f"token:{kind.value}/{name}")
        for key, value in _artifact_variables(dumped):
            add(value, f"variable:{key}")
        if isinstance(model, ModuleModel):
            for service in model.spec.services or []:
                if service.artifact is not None:
                    add(str(service.artifact), f"service:{name}/{service.name}")
        if isinstance(model, VersionModel):
            for category, target, _pin in model.spec.pins.iter_pins():
                if category == "artifacts":
                    add(target, f"pin:{name}")
    return found


def _artifact_variables(node: Any) -> list[tuple[str, str]]:
    """`(key, artifact)` of every `store: artifact` variable anywhere in a dumped document."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        if node.get("store") == VariableStoreType.ARTIFACT.value and isinstance(node.get("value"), str):
            found.append((str(node.get("key")), node["value"]))
        for value in node.values():
            found.extend(_artifact_variables(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_artifact_variables(item))
    return found


def unused_artifacts(context: SolutionContext) -> list[str]:
    """The artifacts no deployment uses, sorted; empty when that cannot be told (`used_artifacts()` is `None`)."""
    artifacts = set(context.controller.index.names_of(PlatformKind.ARTIFACT))
    if not artifacts:
        return []  # nothing to report, so no deployment needs resolving
    used = used_artifacts(context)
    if used is None:
        return []
    return sorted(artifacts - used)


def unreferenced_artifacts(context: SolutionContext) -> list[str]:
    """The unused artifacts that no document names either (`referenced_artifacts()`), sorted: the leftovers."""
    referenced = referenced_artifacts(context.controller.index)
    return [name for name in unused_artifacts(context) if name not in referenced]


def check_unused_artifacts(context: SolutionContext) -> Diagnostics:
    """One finding per artifact no deployment uses (`artifact_unused`), in the artifact's own file: a warning when
    no document names it, information when one does but no deployment reaches it yet."""
    diagnostics = Diagnostics()
    index = context.controller.index
    unused = unused_artifacts(context)
    referenced = referenced_artifacts(index) if unused else {}
    for name in unused:
        entry = index.get(PlatformKind.ARTIFACT, name)
        if entry is None:
            continue
        artifact = cast(ArtifactModel, entry.model)
        uses = referenced.get(name)
        if uses:
            diagnostics.info(
                f"Artifact '{name}' ({artifact.spec.image_name}) is referenced ({', '.join(uses)}) but no deployment "
                "reaches it yet, so no deployment runs it. Fine for an image staged before its first deployment.",
                source=str(entry.source),
                location="meta.name",
                code="artifact_unused",
            )
            continue
        diagnostics.warning(
            f"Artifact '{name}' ({artifact.spec.image_name}) is referenced nowhere: no store: artifact variable, "
            "no module service artifact, no ${artifact:" + name + ".FIELD} token and no version pin names it. "
            "Reference it, or remove it.",
            source=str(entry.source),
            location="meta.name",
            code="artifact_unused",
        )
    return diagnostics
