#!/usr/bin/env python3
"""The container images a deployment will run, after `kind: version` pins
(docs/work/effective-images.md).

Three places used to answer "which images does this deployment use", each
incompletely: the SBOM (no pins, no artifacts), the audit manifest's bill of
materials (pins, no artifacts) and the render path (everything, but the pin
source thrown away). This is the one answer; the `deploy images` command,
the SBOM's image collector and the audit manifest read it.

Only what the deployment reaches is listed: artifacts named by a
`store: artifact` variable in its environments or by an enabled module
service's `artifact`, and enabled module services' literal `image`. An
artifact nothing references is not deployed, so it is not listed.

Never resolves a store: a `store: artifact` value is an in-solution document
lookup, so this needs no network and reveals no secret.
"""

from typing import cast

from strata.controllers.deployment_resolution import deployment_workspace
from strata.controllers.solution_context import SolutionContext
from strata.controllers.value_controller import (
    compose_image_ref,
    reachable_environments,
    resolve_artifact,
    resolve_artifact_tag,
    resolve_version,
)
from strata.controllers.workload_controller import apply_version_pins, resolve_module
from strata.integrations.resolved_context import EffectiveImage
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.namespace_model import NamespaceModel
from strata.models.store_model import VariableStoreType
from strata.services.environment_service import merge_environment_models
from strata.services.version_service import VersionService
from strata.utils.sbom_utils import parse_image_ref


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

    artifact_uses: dict[str, list[str]] = {}
    variables, _, _ = merge_environment_models(reachable_environments(context, deployment))
    for key, store in variables.items():
        if store.store == VariableStoreType.ARTIFACT:
            artifact_uses.setdefault(str(store.value), []).append(f"variable:{key}")

    service_rows: list[EffectiveImage] = []
    workspace = deployment_workspace(index, deployment)
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
            )
        )
    return [*artifact_rows, *service_rows]
