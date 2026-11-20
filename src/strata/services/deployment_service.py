#!/usr/bin/env python3
"""Service for loading and validating deployment configuration."""

from typing import Any

from strata.models.deployment_model import DeploymentModel, DeploymentTemplateModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.base_service import BaseService
from strata.utils.diagnostics import Diagnostics
from strata.utils.dict_merge import deep_merge

#: Spec fields consumed by `extends` resolution and stripped from the result,
#: so a merged payload looks like a plain, fully-resolved deployment.
_EXTENDS_CONTROL_FIELDS = ("partial", "extends")

#: Lists of named items merged by their identity key across `extends`
#: (docs/design/provider-slots.md, docs/design/asset-management.md). Not listed -> replaced wholesale.
LIST_MERGE_KEYS = {"stages": "step", "providers": "slot", "assets": "slot"}

#: Keyed lists inside an item of a keyed list, merged the same way when two items merge: a stage's
#: `health_checks` by `name`. Not listed -> replaced wholesale, as `deep_merge` does.
NESTED_LIST_MERGE_KEYS: dict[str, dict[str, str]] = {"stages": {"health_checks": "name"}}

#: Name lists that append base-first, each name once, like `environments`.
APPEND_LISTS = ("environments", "configurations")


def _merge_keyed_list(
    base: list[dict[str, Any]], child: list[dict[str, Any]], key: str, nested: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Base items first; a child item with the same `key` deep-merges over it, new keys append. A `nested` list
    in both items (field -> key) is merged by its own key instead of being replaced."""
    by_key: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    for item in [*base, *child]:
        identity = item.get(key)
        if identity is None:
            identity = object()  # a keyless item is never merged into another
        if identity in by_key:
            earlier = by_key[identity]
            merged = deep_merge(earlier, item)
            for field, field_key in (nested or {}).items():
                if earlier.get(field) and item.get(field):
                    merged[field] = _merge_keyed_list(earlier[field], item[field], field_key)
            by_key[identity] = merged
        else:
            by_key[identity] = dict(item)
            order.append(identity)
    return [by_key[identity] for identity in order]


def _as_list(value: Any) -> list[Any]:
    """A spec value that may be one item or a list, as a list: `None` (or an empty value) is `[]`."""
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, list) else [value]


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
    - **Keyed lists** (`LIST_MERGE_KEYS`: `stages` by `step`, `providers` and `assets` by
      `slot`) — a child item with the same key overrides the base's
      field-by-field; new keys are appended. Same "later wins per key" rule
      environments already use for variables/secrets/features. Inside a
      merged stage, `health_checks` merge by `name` the same way
      (`NESTED_LIST_MERGE_KEYS`).
    - **`environments`, `configurations`** (`APPEND_LISTS`) — base list first,
      then the child's names it lacks. Later environments win at
      value-resolution time, so the child still takes precedence.
    - **`version`** — base list first, then the child's, each document once
      (docs/design/version-composition.md V2): a shared `prd-core` in a base
      plus the product's own `prd-datahub` gives both. A single name on
      either side counts as a one-element list.
    - **Other lists** — replaced wholesale by the child's.
    - **`partial`/`extends`** — consumed and stripped from the result.

    Args:
        base: The `spec` dict of the base (`partial: true`) deployment.
        child: The `spec` dict of the extending deployment.

    Returns:
        A new merged `spec` dict. Neither input is mutated.
    """
    merged: dict[str, Any] = deep_merge(base, child)

    for field, key in LIST_MERGE_KEYS.items():
        base_items = base.get(field) or []
        child_items = child.get(field) or []
        if base_items or child_items:
            merged[field] = _merge_keyed_list(base_items, child_items, key, NESTED_LIST_MERGE_KEYS.get(field))

    for field in APPEND_LISTS:
        base_names = base.get(field) or []
        child_names = child.get(field) or []
        if base_names or child_names:
            merged[field] = [*base_names, *[name for name in child_names if name not in base_names]]

    # docs/design/version-composition.md V2: version lists append base-first, a repeat dropped silently. A single
    # name on either side counts as a one-element list; one name in the result stays a plain name.
    base_versions = _as_list(base.get("version"))
    child_versions = _as_list(child.get("version"))
    if base_versions or child_versions:
        versions = list(dict.fromkeys([*base_versions, *child_versions]))
        merged["version"] = versions[0] if len(versions) == 1 else versions

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


class DeploymentTemplateService(BaseService[DeploymentTemplateModel]):
    """Loads a deployment whose spec holds `${param:}`: meta validated, spec kept as written.

    The loader picks it over `DeploymentService` for such a document (`solution_controller.py`); the resolver
    validates the spec per deployable deployment, once the values are substituted.
    """

    def _get_model_class(self) -> type[DeploymentTemplateModel]:
        """Return the DeploymentTemplateModel class for validation."""
        return DeploymentTemplateModel
