#!/usr/bin/env python3
"""An artifact's image as a deployment pins it, and the ``${artifact:NAME.FIELD}`` entries of the token map
(docs/work/artifact-gap-analysis.md, design 1).

Index-based on purpose: `semantic_checks.py` judges the tokens with only a `DocumentIndex`, and cannot import
`value_controller.py` (which imports `solution_context.py`, which imports `semantic_checks.py`). The one pin lookup
for artifacts lives here, so `deploy run`, `strata validate`, `store: artifact` and `deploy images` cannot disagree
about an artifact's tag.
"""

from dataclasses import dataclass
from typing import Literal, cast

from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.version_pins import log_pin_applied
from strata.models.artifact_model import ArtifactModel
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.version_model import VersionModel
from strata.services.version_service import VersionService
from strata.utils.sbom_utils import compose_image_ref
from strata.utils.value_tokens import artifact_key


@dataclass(frozen=True)
class ArtifactTag:
    """An artifact's effective tag and where it came from.

    `source` is `pin` (a `kind: version` pin won), `declared` (the artifact's own `image_tag`) or `none`
    (neither supplied one; `value` is `None`). `version` names the version document for a `pin`.
    """

    value: str | None
    source: Literal["pin", "declared", "none"]
    version: str | None = None


def _deployment_version(index: DocumentIndex, deployment: DeploymentModel) -> VersionModel | None:
    """The version document `deployment.spec.version` names, or `None` (unset, or not in the index)."""
    if not deployment.spec.version:
        return None
    entry = index.get(PlatformKind.VERSION, deployment.spec.version)
    return cast(VersionModel, entry.model) if entry is not None else None


def _tag(artifact_name: str, artifact: ArtifactModel, version: VersionModel | None, *, log: bool) -> ArtifactTag:
    declared = artifact.spec.image_tag or None
    if version is not None:
        pin = VersionService.from_model(version).resolve("artifacts", artifact_name)
        if pin is not None:
            if log:
                log_pin_applied("artifacts", artifact_name, declared, pin.version, version.meta.name)
            return ArtifactTag(pin.version, "pin", version.meta.name)
    return ArtifactTag(declared, "declared" if declared else "none")


def effective_artifact_tag(
    index: DocumentIndex, deployment: DeploymentModel, artifact_name: str, *, log: bool = True
) -> ArtifactTag | None:
    """The effective tag of `artifact_name` for `deployment`, or `None` when the artifact does not exist.

    Consults `deployment.spec.version` -> `VersionModel.spec.pins.artifacts[artifact_name]` first (via
    `VersionService.resolve()`, the single shared pin lookup every overlay category uses,
    docs/design/version-pin-overlay.md), then the artifact's own `image_tag`. A pin that wins is logged when `log`
    is set (ADR-0019 decision 7). `None` means "no such artifact", never "no tag": that is an `ArtifactTag` with
    `source="none"`.
    """
    entry = index.get(PlatformKind.ARTIFACT, artifact_name)
    if entry is None:
        return None
    return _tag(artifact_name, cast(ArtifactModel, entry.model), _deployment_version(index, deployment), log=log)


def artifact_token_values(index: DocumentIndex, deployment: DeploymentModel, *, log: bool = False) -> dict[str, str]:
    """The ``artifact:NAME.FIELD`` entries of the flat token map for `deployment`, three per artifact.

    `image_tag` is left out when the artifact has no tag and the deployment pins none, so
    ``${artifact:NAME.image_tag}`` then fails like any other unresolved token instead of substituting an empty
    string into ``repo:${...}``; `image_ref` is the bare name then, as a `store: artifact` variable's `image_ref`
    is. One function builds the entries, so `deploy run` and `strata validate` cannot disagree about what exists.
    All in memory: no store, no network.

    Args:
        index: The loaded solution's documents.
        deployment: The resolved deployment, its `spec.version` already overridden by `--pin` when given.
        log: Log each pin that wins. `deploy run` sets it once per run; `strata validate` leaves it off.
    """
    version = _deployment_version(index, deployment)
    values: dict[str, str] = {}
    for entry in index.all_of(PlatformKind.ARTIFACT):
        artifact = cast(ArtifactModel, entry.model)
        name = entry.ref.name
        tag = _tag(name, artifact, version, log=log).value
        values[artifact_key(name, "image_name")] = artifact.spec.image_name
        if tag:
            values[artifact_key(name, "image_tag")] = tag
        values[artifact_key(name, "image_ref")] = compose_image_ref(artifact.spec.image_name, tag)
    return values
