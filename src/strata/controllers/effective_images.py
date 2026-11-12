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

Each row says whether strata delivers the image, that is writes it into what
runs (docs/work/helm-image-delivery.md, option B): only the Compose integration
writes a service `image`, a `store: artifact` variable reaches a step, and an
`${artifact:}` token is resolved into a document the deployment uses. A Helm
module's service image is a declaration; the chart decides what runs.
`unknown_image_modules()` names the Helm modules that declare no image at all.

Never resolves a store: a `store: artifact` value is an in-solution document
lookup, so this needs no network and reveals no secret.
"""

import json
from collections.abc import Iterator, Sequence
from dataclasses import replace
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
from strata.integrations.resolved_context import EffectiveImage, UnknownImageModule
from strata.models.common_models import PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.store_model import VariableStoreType
from strata.models.version_model import VersionModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.environment_service import merge_environment_models
from strata.services.version_service import VersionService
from strata.utils.sbom_utils import (
    FOUND_IN_PROPERTY,
    FOUND_IN_RENDERED_CHART,
    IMAGE_REF_PROPERTY,
    USED_BY_PROPERTY,
    compose_image_ref,
    image_to_purl,
    parse_image_ref,
    split_image_tag,
)
from strata.utils.templater import template_variable_keys
from strata.utils.value_tokens import referenced_artifact_keys, referenced_variable_keys, split_artifact_token_key

#: What `build run` writes (`sbom_controller.write_sbom()`); read back by `rendered_chart_images()`.
_SBOM_FILENAME = "sbom.json"


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
    # An artifact is delivered when any use delivers it; otherwise the first non-delivering service use says why.
    artifact_delivered: set[str] = set()
    artifact_undelivered: dict[str, str] = {}
    variables, _, _ = merge_environment_models(reachable_environments(context, deployment))
    artifact_keys = {key for key, store in variables.items() if store.store == VariableStoreType.ARTIFACT}
    used_keys = {key for _, dumped in reached for key in referenced_variable_keys(dumped)}
    delivered = (used_keys & artifact_keys) | _received_variable_keys(context, workspace, artifact_keys)
    for key, store in variables.items():
        if key in delivered:
            artifact_uses.setdefault(str(store.value), []).append(f"variable:{key}")
            artifact_delivered.add(str(store.value))

    service_rows: list[EffectiveImage] = []
    for _, declared, effective in _enabled_modules(context, workspace, version):
        module_name = declared.meta.name
        delivery = _not_delivered(effective)
        for service in effective.spec.services or []:
            use = f"service:{module_name}/{service.name}"
            if service.artifact is not None:
                artifact_uses.setdefault(str(service.artifact), []).append(use)
                if delivery is None:
                    artifact_delivered.add(str(service.artifact))
                else:
                    artifact_undelivered.setdefault(str(service.artifact), _artifact_not_delivered(effective))
            elif service.image:
                pinned = version_service is not None and version_service.resolve("images", service.name) is not None
                name, tag, digest = parse_image_ref(service.image)
                service_rows.append(
                    EffectiveImage(
                        origin="module-service",
                        name=str(service.name),
                        image_name=name,
                        image_tag=tag,
                        image_digest=digest,
                        image_ref=service.image,
                        tag_source="pin" if pinned else ("declared" if tag or digest else "none"),
                        module=module_name,
                        via=(use,),
                        pin_version=version.meta.name if pinned and version is not None else None,
                        pin_flag=pinned and pinned_by_flag,
                        delivered=delivery is None,
                        delivery=delivery,
                    )
                )

    # `${artifact:NAME.FIELD}` anywhere `deploy run` resolves tokens (docs/work/artifact-gap-analysis.md, design 3):
    # without this, an image delivered only through the token would escape the policy, the SBOM and the gates.
    for use, dumped in reached:
        for artifact_name in sorted({split_artifact_token_key(key)[0] for key in referenced_artifact_keys(dumped)}):
            uses = artifact_uses.setdefault(artifact_name, [])
            if use not in uses:
                uses.append(use)
            artifact_delivered.add(artifact_name)

    artifact_rows: list[EffectiveImage] = []
    for artifact_name, via in artifact_uses.items():
        artifact = resolve_artifact(context, artifact_name)
        artifact_tag = resolve_artifact_tag(context, deployment, artifact_name)
        if artifact is None or artifact_tag is None:
            continue
        tag, digest = split_image_tag(artifact_tag.value)
        artifact_rows.append(
            EffectiveImage(
                origin="artifact",
                name=artifact_name,
                image_name=artifact.spec.image_name,
                image_tag=tag,
                image_digest=digest,
                image_ref=compose_image_ref(artifact.spec.image_name, artifact_tag.value),
                tag_source=artifact_tag.source,
                artifact=artifact_name,
                via=tuple(via),
                registry=artifact.spec.registry,
                configuration=artifact.spec.configuration,
                pin_version=artifact_tag.version,
                pin_flag=artifact_tag.source == "pin" and pinned_by_flag,
                integration=artifact.spec.integration,
                delivered=artifact_name not in artifact_undelivered or artifact_name in artifact_delivered,
                delivery=None if artifact_name in artifact_delivered else artifact_undelivered.get(artifact_name),
            )
        )
    return [*artifact_rows, *service_rows]


def unknown_image_modules(context: SolutionContext, deployment: DeploymentModel) -> list[UnknownImageModule]:
    """The enabled Helm modules of `deployment` whose services declare no `image` and no `artifact`, so every
    image their chart deploys is known only to Helm (docs/work/helm-image-delivery.md, option B).

    Helm only: a GitOps, script or custom module may run no container at all. A Helm module that declares some
    images but whose chart runs more is not listed: telling that needs the chart rendered (option D).
    `chart_version` is the one that deploys, after a `charts` pin.
    """
    workspace = deployment_workspace(context.controller.index, deployment)
    version = resolve_version(context, deployment)
    found: list[UnknownImageModule] = []
    for namespace_name, declared, effective in _enabled_modules(context, workspace, version):
        if effective.spec.type != "helm":
            continue
        if any(service.image or service.artifact is not None for service in effective.spec.services or []):
            continue
        found.append(
            UnknownImageModule(
                module=declared.meta.name,
                namespace=namespace_name,
                chart=effective.spec.source.chart_name,
                chart_version=effective.spec.source.chart_version,
            )
        )
    return found


def _enabled_modules(
    context: SolutionContext, workspace: WorkspaceModel | None, version: VersionModel | None
) -> Iterator[tuple[str, ModuleModel, ModuleModel]]:
    """`(namespace, declared module, module after version pins)` for each enabled module reference of the
    workspace's namespaces: the one walk `effective_images()` and `unknown_image_modules()` share."""
    index = context.controller.index
    for namespace_name in (workspace.spec.namespaces if workspace is not None else None) or []:
        namespace_entry = index.get(PlatformKind.NAMESPACE, namespace_name)
        if namespace_entry is None:
            continue
        namespace = cast(NamespaceModel, namespace_entry.model)
        for reference in namespace.spec.modules or []:
            if not reference.enabled:
                continue
            declared = resolve_module(index, reference)
            yield str(namespace_name), declared, apply_version_pins(declared, version)


def _not_delivered(module: ModuleModel) -> str | None:
    """Why strata does not deliver a service `image` of `module`, or `None` when it does: only the Compose
    integration writes a service's image into what runs (`compose.py`)."""
    if module.spec.type == "compose":
        return None
    if module.spec.type == "helm":
        return "helm module: the chart decides"
    return f"{module.spec.type or 'untyped'} module: strata writes no image"


def _artifact_not_delivered(module: ModuleModel) -> str:
    """Why an artifact that `module`'s services name, and nothing else delivers, does not reach what runs."""
    if module.spec.type == "helm":
        return "helm module: no ${artifact:} token"
    return f"{module.spec.type or 'untyped'} module: strata writes no image"


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


def rendered_chart_images(context: SolutionContext, build_path: Path) -> list[EffectiveImage] | None:
    """The images a build's rendered charts run, read from its `sbom.json`, or `None` when there is no SBOM
    (docs/work/helm-image-delivery.md, plan item 9d).

    One row per `chart:NAMESPACE/REFERENCE/CONTAINER` use of a component the `chart-image` collector wrote, so an
    image two containers run is two rows. `origin` is `chart`, `name` is `REFERENCE/CONTAINER` (what `allow`
    names), `module` is the module the namespace's reference points at, and the reference is `strata:image`, the
    image as the chart writes it. `tag_source` is `chart`: strata did not choose the tag. A component without
    `strata:image` is skipped.

    Raises:
        ValueError: `sbom.json` exists but cannot be read as an SBOM.
    """
    path = build_path / _SBOM_FILENAME
    if not path.is_file():
        return None
    try:
        sbom = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"'{path}' is not valid JSON ({exc})") from None
    components = sbom.get("components") if isinstance(sbom, dict) else None
    if not isinstance(components, list):
        raise ValueError(f"'{path}' has no components list")

    rows: list[EffectiveImage] = []
    for component in components:
        properties = _properties(component)
        reference = properties.get(IMAGE_REF_PROPERTY)
        if properties.get(FOUND_IN_PROPERTY) != FOUND_IN_RENDERED_CHART or not reference:
            continue
        name, tag, digest = parse_image_ref(reference)
        for use in (part.strip() for part in properties.get(USED_BY_PROPERTY, "").split(",")):
            namespace, reference_name, container = _chart_use(use)
            if container is None:
                continue
            rows.append(
                EffectiveImage(
                    origin="chart",
                    name=f"{reference_name}/{container}",
                    image_name=name,
                    image_tag=tag,
                    image_digest=digest,
                    image_ref=reference,
                    tag_source="chart",
                    module=_module_of(context, namespace, reference_name),
                    via=(use,),
                )
            )
    return rows


def with_rendered_images(
    images: Sequence[EffectiveImage], unknown: Sequence[UnknownImageModule], rendered: Sequence[EffectiveImage]
) -> tuple[list[EffectiveImage], list[UnknownImageModule]]:
    """`images` and `unknown` once the build's rendered chart images are known (plan item 9d).

    - A rendered image that a declared row already has (the same purl) is not added: the declared row is judged,
      once, as delivered, because the chart runs it.
    - Every other rendered row is added after the declared ones.
    - A Helm module with at least one rendered row is no longer unknown.
    """
    rendered_purls = {image_to_purl(row.image_ref) for row in rendered}
    declared_purls = {image_to_purl(row.image_ref) for row in images}
    merged = [
        replace(row, delivered=True, delivery=None) if image_to_purl(row.image_ref) in rendered_purls else row
        for row in images
    ]
    merged.extend(row for row in rendered if image_to_purl(row.image_ref) not in declared_purls)
    rendered_modules = {(_chart_use(row.via[0])[0], row.module) for row in rendered if row.via}
    remaining = [module for module in unknown if (module.namespace, module.module) not in rendered_modules]
    return merged, remaining


def _properties(component: Any) -> dict[str, str]:
    """A CycloneDX component's `properties` as a mapping; empty for anything that is not a component."""
    if not isinstance(component, dict) or component.get("type") != "container":
        return {}
    found: dict[str, str] = {}
    for item in component.get("properties") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("value"), str):
            found.setdefault(item["name"], item["value"])
    return found


def _chart_use(use: str) -> tuple[str, str, str | None]:
    """`(namespace, reference, container)` of a `chart:NS/REFERENCE/CONTAINER` use; container `None` otherwise."""
    if not use.startswith("chart:"):
        return "", "", None
    parts = use.removeprefix("chart:").split("/")
    if len(parts) != 3 or not all(parts):
        return "", "", None
    return parts[0], parts[1], parts[2]


def _module_of(context: SolutionContext, namespace_name: str, reference_name: str) -> str:
    """The module a namespace's reference points at; the reference's own name when the solution no longer has it."""
    entry = context.controller.index.get(PlatformKind.NAMESPACE, namespace_name)
    if entry is not None:
        for reference in cast(NamespaceModel, entry.model).spec.modules or []:
            if reference.name == reference_name:
                return str(reference.module)
    return reference_name
