#!/usr/bin/env python3
"""The container images a deployment will run, after `kind: version` pins
(docs/work/effective-images.md).

Three places used to answer "which images does this deployment use", each
incompletely: the SBOM (no pins, no artifacts), the audit manifest's bill of
materials (pins, no artifacts) and the render path (everything, but the pin
source thrown away). This is the one answer; the `deploy images` command,
the SBOM's image collector and the audit manifest read it.

Only what the deployment reaches is listed: artifacts named by a
`store: artifact` variable the deployment can receive, by an enabled module
service's `artifact` or by an `${artifact:NAME.FIELD}` token in a document
the deployment reaches, and enabled module services' literal `image`. An
artifact nothing references is not deployed, so it is not listed.

A `store: artifact` variable counts when a `${var:KEY}` token in a reached
document reads it, or an execution step receives it without a token
(`_received_variable_keys()`, docs/work/artifact-gap-analysis.md, design 7).
Where strata cannot tell, it counts: over-reporting an image is the safe side.

Never resolves a store: a `store: artifact` value is an in-solution document
lookup, so this needs no network and reveals no secret.
"""

from pathlib import Path
from typing import Any, cast

from strata.controllers.deployment_resolution import deployment_workspace
from strata.controllers.reachable_documents import documents_reached_by_deployment
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import (
    reachable_environments,
    resolve_artifact,
    resolve_artifact_tag,
    resolve_version,
)
from strata.controllers.workload_controller import apply_version_pins, resolve_module
from strata.integrations.registry import IntegrationNotFoundError
from strata.integrations.registry import get_class as get_integration_class
from strata.integrations.resolved_context import EffectiveImage
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.namespace_model import NamespaceModel
from strata.models.store_model import VariableStoreType
from strata.models.workspace_model import WorkspaceModel
from strata.services.environment_service import merge_environment_models
from strata.services.version_service import VersionService
from strata.utils.sbom_utils import compose_image_ref, parse_image_ref
from strata.utils.templater import template_variable_keys
from strata.utils.value_tokens import referenced_artifact_keys, referenced_variable_keys, split_artifact_token_key


def effective_images(
    context: SolutionContext, deployment: DeploymentModel, *, pinned_by_flag: bool = False
) -> list[EffectiveImage]:
    """Every image `deployment` runs, artifacts first, in the order they are first used.

    `deployment` is the resolved model, already carrying a `--pin` override in `spec.version`
    (`value_controller.resolve_deployment()`); `pinned_by_flag` says whether it did, so a row can tell
    a pin from `--pin` apart from one from the deployment's own `spec.version`.
    """
    index = context.controller.index
    version = resolve_version(context, deployment)
    version_service = VersionService.from_model(version) if version is not None else None

    workspace = deployment_workspace(index, deployment)
    deployment_entry = index.get(PlatformKind.DEPLOYMENT, deployment.meta.name)
    source = deployment_entry.source if deployment_entry is not None else Path()
    reached: list[tuple[str, dict[str, Any]]] = []
    for document in documents_reached_by_deployment(index, deployment, source, enabled_only=True):
        dumped = document.model.model_dump(by_alias=True, mode="json")
        dumped.pop("meta", None)
        reached.append((f"token:{document.kind.value}/{document.name}", dumped))

    artifact_uses: dict[str, list[str]] = {}
    variables, _, _ = merge_environment_models(reachable_environments(context, deployment))
    artifact_keys = {key for key, store in variables.items() if store.store == VariableStoreType.ARTIFACT}
    used_keys = {key for _, dumped in reached for key in referenced_variable_keys(dumped)}
    delivered = (used_keys & artifact_keys) | _received_variable_keys(context, workspace, artifact_keys)
    for key, store in variables.items():
        if key in delivered:
            artifact_uses.setdefault(str(store.value), []).append(f"variable:{key}")

    service_rows: list[EffectiveImage] = []
    for namespace_name in (workspace.spec.namespaces if workspace is not None else None) or []:
        namespace_entry = index.get(PlatformKind.NAMESPACE, namespace_name)
        if namespace_entry is None:
            continue
        namespace = cast(NamespaceModel, namespace_entry.model)
        for reference in namespace.spec.modules or []:
            if not reference.enabled:
                continue
            declared = resolve_module(index, reference)
            effective = apply_version_pins(declared, version)
            module_name = declared.meta.name
            for service in effective.spec.services or []:
                use = f"service:{module_name}/{service.name}"
                if service.artifact is not None:
                    artifact_uses.setdefault(str(service.artifact), []).append(use)
                elif service.image:
                    pinned = version_service is not None and version_service.resolve("images", service.name) is not None
                    name, tag, digest = parse_image_ref(service.image)
                    found = tag or digest
                    service_rows.append(
                        EffectiveImage(
                            origin="module-service",
                            name=str(service.name),
                            image_name=name,
                            image_tag=found,
                            image_ref=service.image,
                            tag_source="pin" if pinned else ("declared" if found else "none"),
                            module=module_name,
                            via=(use,),
                            pin_version=version.meta.name if pinned and version is not None else None,
                            pin_flag=pinned and pinned_by_flag,
                        )
                    )

    # `${artifact:NAME.FIELD}` anywhere `deploy run` resolves tokens (docs/work/artifact-gap-analysis.md, design 3):
    # without this, an image delivered only through the token would escape the policy, the SBOM and the gates.
    for use, dumped in reached:
        for artifact_name in sorted({split_artifact_token_key(key)[0] for key in referenced_artifact_keys(dumped)}):
            uses = artifact_uses.setdefault(artifact_name, [])
            if use not in uses:
                uses.append(use)

    artifact_rows: list[EffectiveImage] = []
    for artifact_name, via in artifact_uses.items():
        artifact = resolve_artifact(context, artifact_name)
        artifact_tag = resolve_artifact_tag(context, deployment, artifact_name)
        if artifact is None or artifact_tag is None:
            continue
        artifact_rows.append(
            EffectiveImage(
                origin="artifact",
                name=artifact_name,
                image_name=artifact.spec.image_name,
                image_tag=artifact_tag.value,
                image_ref=compose_image_ref(artifact.spec.image_name, artifact_tag.value),
                tag_source=artifact_tag.source,
                artifact=artifact_name,
                via=tuple(via),
                registry=artifact.spec.registry,
                configuration=artifact.spec.configuration,
                pin_version=artifact_tag.version,
                pin_flag=artifact_tag.source == "pin" and pinned_by_flag,
                integration=artifact.spec.integration,
            )
        )
    return [*artifact_rows, *service_rows]


def _received_variable_keys(context: SolutionContext, workspace: WorkspaceModel | None, keys: set[str]) -> set[str]:
    """Which of `keys` an execution step receives without a `${var:KEY}` token (docs/work/artifact-gap-analysis.md,
    design 7), with `step_secret_keys()`'s rules for secrets:

    - a tool that takes the strata payload (`ENV_VAR_PREFIX`: Terraform, and OpenTofu once ported): every variable,
      through `strata.variables` and `TF_VAR_<key>`. The root module is not read, so a variable it does not declare
      still counts;
    - a script without `script.values`: every variable (`STRATA_VALUES`); with it, the keys it lists;
    - an `output.template`: the `variables` keys it reads, or every variable when it reads them dynamically or
      cannot be read;
    - a tool strata does not know: every variable.

    Only `execution` steps count: `deploy run` runs nothing else, and `build run` renders nothing else.
    """
    if workspace is None or not keys:
        return set()
    provisioners = {provisioner.name: provisioner for provisioner in workspace.spec.provisioners or []}
    received: set[str] = set()
    for step in workspace.spec.execution or []:
        provisioner = provisioners.get(step.provisioner)
        if provisioner is None:
            continue
        try:
            integration_class = get_integration_class(str(provisioner.tool))
        except IntegrationNotFoundError:
            return set(keys)
        if integration_class.ENV_VAR_PREFIX is not None:
            return set(keys)
        if provisioner.script is not None:
            if provisioner.script.values is None:
                return set(keys)
            received |= set(provisioner.script.values)
        if provisioner.output is not None and provisioner.output.template:
            try:
                used = template_variable_keys((context.root / provisioner.output.template).read_text(encoding="utf-8"))
            except OSError:
                used = None
            if used is None:
                return set(keys)
            received |= used
    return received & keys
