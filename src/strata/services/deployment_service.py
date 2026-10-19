#!/usr/bin/env python3
"""Service for loading and validating deployment configuration."""

from typing import Any

from strata.models.deployment_model import DeploymentModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics
from strata.utils.dict_merge import deep_merge

#: Spec fields consumed by `extends` resolution and stripped from the result,
#: so a merged payload looks like a plain, fully-resolved deployment.
_EXTENDS_CONTROL_FIELDS = ("partial", "extends")

#: Lists of named items merged by their identity key across `extends`
#: (docs/design/provider-slots.md). Not listed -> replaced wholesale.
_LIST_MERGE_KEYS = {"stages": "step", "providers": "slot"}


def _merge_keyed_list(base: list[dict[str, Any]], child: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Base items first; a child item with the same `key` deep-merges over it, new keys append."""
    by_key: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    for item in [*base, *child]:
        identity = item.get(key)
        if identity is None:
            identity = object()  # a keyless item is never merged into another
        if identity in by_key:
            by_key[identity] = deep_merge(by_key[identity], item)
        else:
            by_key[identity] = dict(item)
            order.append(identity)
    return [by_key[identity] for identity in order]


def merge_deployment_specs(base: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Merge a base deployment spec into a child's. Child always wins.

    Operates on **raw dicts, before Pydantic validation** — deliberately, and
    for the same reason v1's `DeploymentExtensionResolver` does: a
    `partial: true` base is missing required fields by design, so it cannot be
    validated on its own first. Only the merged result is a valid document.

    Rules:

    - **Nested blocks** — deep-merged per leaf key, so a child setting one
      field of `locking` keeps the base's other fields. This diverges from
      v1, which replaced whole top-level values: there, a child overriding
      `locking.wait_timeout` silently dropped `locking.strategy`, which then
      fell back to its schema default — a real change to a value nobody
      wrote, reported as nothing. Helm values and Kustomize merge per leaf
      key for the same reason.
    - **Keyed lists** (`_LIST_MERGE_KEYS`: `stages` by `step`, `providers` by
      `slot`) — a child item with the same key overrides the base's
      field-by-field; new keys are appended. Same "later wins per key" rule
      environments already use for variables/secrets/features.
    - **`environments`** — base list first, then the child's. Later entries
      win at value-resolution time, so the child still takes precedence.
    - **Other lists** — replaced wholesale by the child's.
    - **`partial`/`extends`** — consumed and stripped from the result.

    Args:
        base: The `spec` dict of the base (`partial: true`) deployment.
        child: The `spec` dict of the extending deployment.

    Returns:
        A new merged `spec` dict. Neither input is mutated.
    """
    merged: dict[str, Any] = deep_merge(base, child)

    for field, key in _LIST_MERGE_KEYS.items():
        base_items = base.get(field) or []
        child_items = child.get(field) or []
        if base_items or child_items:
            merged[field] = _merge_keyed_list(base_items, child_items, key)

    base_envs = base.get("environments") or []
    child_envs = child.get("environments") or []
    if base_envs or child_envs:
        merged["environments"] = [*base_envs, *[e for e in child_envs if e not in base_envs]]

    for field in _EXTENDS_CONTROL_FIELDS:
        merged.pop(field, None)

    return merged


class DeploymentService(BaseService[DeploymentModel]):
    """Service for handling deployment configuration.

    Cross-document checks needing the solution index are public methods the
    controller calls once it has resolved the referenced documents — the same
    "assume the caller already loaded it" contract used by
    `WorkspaceService`/`ProviderService`.

    `extends` chain walking and cycle detection are controller work: they need
    the index and operate on raw dicts before validation (see
    `merge_deployment_specs`). The model catches only the trivial self-cycle.
    """

    def _get_model_class(self) -> type[DeploymentModel]:
        """Return the DeploymentModel class for validation."""
        return DeploymentModel

    def validate_stages_against_workspace(self, workspace: WorkspaceModel) -> Diagnostics:
        """Check every stage names a real execution step in the workspace.

        A stage supplies runtime parameters for a step in the workspace's
        recipe; naming a step that does not exist means those parameters
        silently apply to nothing.

        Args:
            workspace: The already-loaded `WorkspaceModel` this deployment
                names in `spec.workspace`.

        Returns:
            One error per stage naming an unknown step.
        """
        diagnostics = Diagnostics()
        if self.model is None:
            diagnostics.error("Deployment model is not initialized")
            return diagnostics
        if not self.model.spec.stages:
            return diagnostics

        known = {step.name for step in (workspace.spec.execution or [])}
        for index, stage in enumerate(self.model.spec.stages):
            if stage.step not in known:
                diagnostics.error(
                    f"Deployment '{self.model.meta.name}': stage references unknown execution step "
                    f"'{stage.step}'. Workspace '{workspace.meta.name}' defines: {sorted(known)}",
                    location=f"spec.stages.{index}.step",
                    code="unknown_execution_step",
                )
        return diagnostics
