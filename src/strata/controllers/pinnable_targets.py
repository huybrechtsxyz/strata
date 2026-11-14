#!/usr/bin/env python3
"""What a deployment can be pinned on: the `category/target` keys a version pin for it would take effect on
(docs/work/version-composition.md V5).

One answer for every question "what does this deployment need": the coverage checks in `strata validate`
(`version_document_unused`, `version_pin_unused`), the pins the build writes to `version.yaml`, `promote apply` and
`version update`. Built from the documents the deployment reaches (`documents_reached_by_deployment()`, enabled
modules only, as `effective_images()` walks them):

- `images`: an enabled module service that declares an `image` (a pin replaces it, `apply_version_pins()`);
- `charts`: an enabled chart-based module (`source.chart_name`);
- `remotes`: every remote a reached document names (a provisioner's or module's `source.remote`), and the remote of
  every asset the workspace's execution steps select;
- `artifacts`: a module service's `artifact`, an `${artifact:}` token in a reached document, and a `store:
  artifact` variable in a reached document (environment, tenant, deployment);
- `assets`: every asset the workspace's execution steps select that declares a `location` of its own.

Deliberately a little wider than what a run takes effect on (a `store: artifact` variable no step receives still
counts): a target counted that is not used only hides a `version_pin_unused` finding, while a target missed would
make a correct document look unused.
"""

from pathlib import Path
from typing import Any, cast

from strata.controllers.reachable_documents import documents_reached_by_deployment
from strata.controllers.solution_controller import DocumentIndex
from strata.models.asset_model import AssetModel
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.module_model import ModuleModel
from strata.models.store_model import VariableStoreType
from strata.models.workspace_model import WorkspaceModel
from strata.services.workspace_service import effective_assets
from strata.utils.value_tokens import referenced_artifact_keys, split_artifact_token_key

PinTarget = tuple[str, str]


def pinnable_targets(index: DocumentIndex, deployment: DeploymentModel) -> set[PinTarget]:
    """Every `(category, target)` a version pin could take effect on for `deployment` (the resolved model)."""
    targets: set[PinTarget] = set()
    entry = index.get(PlatformKind.DEPLOYMENT, deployment.meta.name)
    source = entry.source if entry is not None else Path()
    reached = documents_reached_by_deployment(index, deployment, source, enabled_only=True)
    for document in reached:
        dumped = document.model.model_dump(by_alias=True, mode="json")
        dumped.pop("meta", None)
        targets |= {("remotes", name) for name in _remote_names(dumped)}
        targets |= {("artifacts", split_artifact_token_key(key)[0]) for key in referenced_artifact_keys(dumped)}
        targets |= {("artifacts", name) for name in _artifact_variables(dumped)}
        if document.kind is PlatformKind.MODULE:
            targets |= _module_targets(cast(ModuleModel, document.model))

    workspace_entry = (
        index.get(PlatformKind.WORKSPACE, deployment.spec.workspace) if deployment.spec.workspace else None
    )
    if workspace_entry is not None:
        workspace = cast(WorkspaceModel, workspace_entry.model)
        by_slot = effective_assets(workspace, deployment)
        slots = {name for step in (workspace.spec.execution or []) for name in (step.assets or [])}
        for slot in slots:
            asset_entry = index.get(PlatformKind.ASSET, by_slot.get(slot, slot))
            if asset_entry is None:
                continue
            asset = cast(AssetModel, asset_entry.model)
            if asset.spec.location is not None:
                targets.add(("assets", asset.meta.name))
            if asset.spec.remote:
                targets.add(("remotes", asset.spec.remote))
    return targets


def _module_targets(module: ModuleModel) -> set[PinTarget]:
    targets: set[PinTarget] = set()
    if module.spec.source.chart_name is not None:
        targets.add(("charts", module.meta.name))
    for service in module.spec.services or []:
        if service.image:
            targets.add(("images", service.name))
        if service.artifact:
            targets.add(("artifacts", str(service.artifact)))
    return targets


def _remote_names(node: Any) -> set[str]:
    """Every string value of a `remote` key anywhere in a dumped document."""
    found: set[str] = set()
    if isinstance(node, dict):
        value = node.get("remote")
        if isinstance(value, str) and value:
            found.add(value)
        for child in node.values():
            found |= _remote_names(child)
    elif isinstance(node, list):
        for item in node:
            found |= _remote_names(item)
    return found


def _artifact_variables(node: Any) -> set[str]:
    """The artifact of every `store: artifact` variable anywhere in a dumped document."""
    found: set[str] = set()
    if isinstance(node, dict):
        if node.get("store") == VariableStoreType.ARTIFACT.value and isinstance(node.get("value"), str):
            found.add(node["value"])
        for child in node.values():
            found |= _artifact_variables(child)
    elif isinstance(node, list):
        for item in node:
            found |= _artifact_variables(item)
    return found
