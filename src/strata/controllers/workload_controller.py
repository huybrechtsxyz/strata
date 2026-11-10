#!/usr/bin/env python3
"""ADR-0022 D5-D7's workload pipeline — `Namespace.spec.modules` (Compose/
Helm), resolved and rendered independently of `build_controller.py`'s
provisioner loop.

**A second, disconnected input shape, not a variant of the first one**
(D5's own finding, confirmed by reading v1's real `ComposeBuilder`/
`HelmBuilder` directly): a workload module is never a `ProvisionerModel`/
`ProvisioningStepModel`, and its integration is resolved from a bare
`module.spec.type` string, never `ProvisionerModel.integration`'s named
binding (`resolve_module_integration()`, ADR-0022 D5).

Same "controller resolves paths/remotes, the integration only renders"
split the provisioner path already establishes (ADR-0021 D2,
`build_controller.py`/`source_sync.py`): this module computes every
module's build directory and materialises its chart source *before*
handing an `InfraIntegration` a `ResolvedModule` list — `HelmIntegration`/
`ComposeIntegration.prepare_namespace()` never touch `DocumentIndex` or a
remote directly.
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from strata.controllers.integration_resolution import resolve_module_integration
from strata.controllers.remote_credentials import (
    CredentialsNeedResolveError,
    RemoteCredentialError,
    RemoteCredentialResolver,
)
from strata.controllers.solution_context import SolutionContext
from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.source_sync import describe_source, sync_module_source
from strata.controllers.value_controller import resolve_artifact_field
from strata.controllers.version_pins import log_pin_applied
from strata.integrations.errors import IntegrationError, RegistryAuthError
from strata.integrations.helm import (
    CHART_NOT_PINNED,
    HelmIntegration,
    chart_cache_source,
    chart_coordinates,
    check_configuration_keys,
    read_chart_values,
)
from strata.integrations.helm_cache import ChartPin, ChartPullError, cached_pin, is_exact_version, read_chart_yaml
from strata.integrations.helm_dependencies import (
    DEPENDENCY_MISSING,
    DEPENDENCY_NOT_PINNED,
    OUTSIDE_SOURCE,
    WRONG_VERSION,
    Action,
    Dependency,
    DependencyRecord,
    committed_subcharts,
    dependency_list_problems,
    plan_dependencies,
    values_keys,
)
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.common_models import ModuleReferenceModel, PlatformKind
from strata.models.deployment_model import DeploymentModel
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.models.version_model import VersionModel
from strata.services.version_service import VersionService
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError
from strata.utils.layout import helm_cache_dir
from strata.utils.path_safety import resolve_within


def resolve_module(index: DocumentIndex, reference: ModuleReferenceModel) -> ModuleModel:
    """Return the `ModuleModel` `reference` points at.

    Raises:
        UsageError: `reference.module` is not in the index — should not
            happen, `validate_references` already guarantees every
            `ModuleReferenceModel.module` resolves; this is a defensive
            backstop, not primary validation.
    """
    entry = index.get(PlatformKind.MODULE, reference.module)
    if entry is None:
        raise UsageError(
            f"Module reference '{reference.name}' names module '{reference.module}', which is not in the index."
        )
    return cast(ModuleModel, entry.model)


def apply_version_pins(module: ModuleModel, version: VersionModel | None) -> ModuleModel:
    """Overlay `version.spec.pins.charts`/`.images` onto `module` (docs/design/
    version-pin-overlay.md Phase 4).

    Returns `module` unchanged (same object) when `version` is `None` or
    neither category pins anything reachable from this module — avoids an
    unconditional copy on the common no-pin path.

    Deliberately conservative about *where* each pin applies, since neither
    category has a Phase-2 check guaranteeing its target is always
    overlay-safe:

    - `charts` only overrides `spec.source.chart_version` when the module
      is already chart-based (`chart_name` set) — mirrors
      `check_version_pins()`'s own `pin_not_applicable` check for this
      exact case.
    - `images` only overrides a service's `.image` when that service
      already declares one — a service may instead use `.artifact`
      (mutually exclusive with `.image`, docs/config/artifact.md,
      itself pinned via the `artifacts` category through a different
      path), and a pin changes a version, never which field is in use.

    Logs every application (ADR-0019 decision 7).
    """
    if version is None:
        return module

    version_service = VersionService.from_model(version)
    spec_updates: dict[str, Any] = {}

    if module.spec.source.chart_name is not None:
        chart_pin = version_service.resolve("charts", module.meta.name)
        if chart_pin is not None:
            log_pin_applied(
                "charts", module.meta.name, module.spec.source.chart_version, chart_pin.version, version.meta.name
            )
            spec_updates["source"] = module.spec.source.model_copy(update={"chart_version": chart_pin.version})

    services = module.spec.services or []
    service_overrides: dict[str, Any] = {}
    for service in services:
        if service.image is None:
            continue
        image_pin = version_service.resolve("images", service.name)
        if image_pin is not None:
            log_pin_applied("images", service.name, service.image, image_pin.version, version.meta.name)
            service_overrides[service.name] = service.model_copy(update={"image": image_pin.version})

    if service_overrides:
        spec_updates["services"] = [service_overrides.get(s.name, s) for s in services]

    if not spec_updates:
        return module

    return module.model_copy(update={"spec": module.spec.model_copy(update=spec_updates)})


def resolve_artifact_services(
    module: ModuleModel, context: SolutionContext | None, deployment: DeploymentModel | None
) -> ModuleModel:
    """Resolve every `service.artifact` reference into `service.image`
    (docs/archive/artifact-references.md Path 1).

    Mirrors `apply_version_pins()`'s own overlay shape — resolve something
    external, overlay it onto a service via `model_copy()`, return a new
    `ModuleModel`. Clears `.artifact` on the returned service once resolved,
    so every render function (`compose.py`'s `_render_namespace_services()`,
    `helm.py`'s `_render_values()`) keeps checking only `.image` — no second
    field for either to learn about.

    `context`/`deployment` being `None` is a graceful no-op — same
    precedent `build_value_references()`'s own optional `context`/
    `deployment` keyword params already established — so every existing
    caller that doesn't pass them keeps working unchanged.

    Raises:
        UsageError: a service's `.artifact` does not resolve. Unreachable in
            a `require_valid()`-passed solution — `.artifact` is an
            unconditional `References(PlatformKind.ARTIFACT)` field, already
            guaranteed to resolve by Phase 2 `validate_references()` (unlike
            `store: artifact`'s conditionally-meaningful `value`). Same
            defensive-backstop treatment as `resolve_module()`'s own
            `UsageError`.
    """
    if context is None or deployment is None:
        return module

    services = module.spec.services or []
    overrides: dict[str, Any] = {}
    for service in services:
        if service.artifact is None:
            continue
        image_ref = resolve_artifact_field(context, deployment, str(service.artifact), "image_ref")
        if image_ref is None:
            raise UsageError(
                f"Module '{module.meta.name}' service '{service.name}': artifact reference "
                f"'{service.artifact}' did not resolve."
            )
        overrides[service.name] = service.model_copy(update={"image": image_ref, "artifact": None})

    if not overrides:
        return module
    return module.model_copy(
        update={"spec": module.spec.model_copy(update={"services": [overrides.get(s.name, s) for s in services]})}
    )


def build_workload_modules(
    index: DocumentIndex,
    root: Path,
    remotes: dict[str, SolutionRemoteModel],
    namespace: NamespaceModel,
    resolved: ValueResolution,
    build_path: Path,
    *,
    version: VersionModel | None = None,
    context: SolutionContext | None = None,
    deployment: DeploymentModel | None = None,
    dry_run: bool = False,
    on_step: Callable[[str], None] | None = None,
    credentials: RemoteCredentialResolver | None = None,
    offline: bool = False,
) -> Diagnostics:
    """Render every module `namespace` declares (ADR-0022 D6).

    Resolves and materialises each module's source first, then groups by
    `module.spec.type` and hands each same-type group to whichever
    `InfraIntegration` that type resolves to — grouping/merging behaviour
    (Compose merges every module into one file; Helm never merges) is
    entirely that class's own concern via `prepare_namespace()`, never
    encoded here.

    Skips a `ModuleReferenceModel` with `enabled=False` entirely — neither
    resolved, materialised, nor rendered. Matches
    `terraform_projection._strata_resources()`'s identical treatment
    of `WorkspaceResourceModel.enabled=False` (that field's own docstring:
    "excludes it from the built platform artifact, and therefore from every
    provisioner that consumes it") — the same v1 parity gap, on the
    module-reference side of the same `enabled` field shared by both models.

    Each module's build directory is `build_path/namespace.meta.name/
    reference.name` — keyed by the *reference's* name, not
    `module.meta.name`, so the same Module document attached twice under
    different reference names in one namespace (explicitly anticipated by
    `ModuleReferenceModel`'s own docstring) never collides; both names are
    already guaranteed unique in the scopes that matter
    (`NamespaceSpecModel.validate_namespace_spec()` for the reference name,
    document discovery for `module.meta.name`, but only within its own
    scope, not across attachments of the same document).

    Args:
        index: The loaded, already-`require_valid()`-ed `DocumentIndex`.
        root: The solution root (where `strata.yaml` lives).
        remotes: Every declared remote, keyed by name.
        namespace: The namespace whose `spec.modules` to render.
        resolved: Build-time values, threaded through unchanged to every
            `prepare_namespace()` call (ADR-0022 D1a) — currently unused by
            Helm (its tokens are deploy-time, see `helm.py`), kept for
            signature symmetry and for a future Compose value-substitution
            phase that may need it (ADR-0023 Remaining Work).
        build_path: The build output root for this `build run` invocation.
        version: The deployment's resolved `Version` document, or `None`
            (docs/design/version-pin-overlay.md Phase 4) — overlays any
            `pins.charts`/`pins.images` reachable from each module before
            it's rendered. `None` is the default and the common case
            (a deployment with no `spec.version`).
        context: The loaded `SolutionContext`, or `None` (docs/archive/
            artifact-references.md Path 1) — resolves any `service.artifact`
            reference reachable from each module before it's rendered.
            `None` is a graceful no-op, same as `deployment` below.
        deployment: The deployment being built, or `None` — required
            alongside `context` to resolve a `service.artifact` reference
            (needs `deployment.spec.version` for pin lookup); `None` skips
            artifact resolution entirely, same as `context` above.
        dry_run: Report what would happen instead of doing it — skips
            materialising a module's source and skips `prepare_namespace()`.
            `resolve_module()`/`resolve_module_integration()` still run, so
            a dry run still catches an unresolvable module reference or an
            unsupported module type.
        on_step: Called with a one-line progress message per module
            materialised and per type-group rendered — real work when
            `dry_run` is `False`, planned work when it's `True`.
        credentials: Resolves `remote.integration`'s credentials when a module's source needs a clone or its
            registry chart a pull; `None` (default) uses ambient git credentials and Helm's own login only.
        offline: Pull no registry chart; one that is not cached is left unpinned (ADR-0054).

    Returns:
        Warnings (a `configuration` key a Helm chart does not declare, a registry chart left unpinned) and
        errors (a registry chart that could not be pulled).

    Raises:
        UsageError: a module reference does not resolve, `module.spec.type`
            is unset, its resolved integration is not infra/container-capable,
            or a `service.artifact` reference does not resolve.
        SourceSyncError: a module's source could not be materialised.
    """

    def _step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    by_type: dict[str, list[ResolvedModule]] = {}
    diagnostics = Diagnostics()

    for reference in namespace.spec.modules or []:
        if not reference.enabled:
            continue

        module = resolve_module(index, reference)
        module = apply_version_pins(module, version)
        module = resolve_artifact_services(module, context, deployment)
        if module.spec.type is None:
            raise UsageError(
                f"Namespace '{namespace.meta.name}', module '{reference.name}': "
                "spec.type is required to render this module."
            )

        module_dir = build_path / namespace.meta.name / reference.name
        copied = None
        if dry_run:
            _step(
                f"would materialise module '{reference.name}' ({describe_source(module.spec.source)}) "
                f"for namespace '{namespace.meta.name}'"
            )
        else:
            copied = sync_module_source(root, module_dir, module.spec.source, remotes, credentials=credentials)
            _step(f"materialised module '{reference.name}' for namespace '{namespace.meta.name}' at {module_dir}")

        label = f"Namespace '{namespace.meta.name}', module '{reference.name}'"
        dependency_records: list[DependencyRecord] = []
        unpinned_dependencies: list[str] = []
        if module.spec.type == "helm" and copied is not None and (module_dir / "Chart.yaml").is_file():
            repo_root, origin = copied
            dependency_records, unpinned_dependencies = _place_chart_dependencies(
                index,
                root,
                remotes,
                module_dir,
                origin=origin,
                repo_root=repo_root,
                label=label,
                location=reference.name,
                offline=offline,
                credentials=credentials,
                diagnostics=diagnostics,
                on_step=_step,
            )
            if dependency_records and not unpinned_dependencies and diagnostics.ok:
                _check_dependency_list(index, module_dir, label=label, location=reference.name, diagnostics=diagnostics)
        chart_pin = None
        if module.spec.type == "helm" and module.spec.source.chart_name is not None:
            chart_pin = _pin_registry_chart(
                index,
                root,
                remotes,
                module,
                label=label,
                location=reference.name,
                dry_run=dry_run,
                offline=offline,
                credentials=credentials,
                diagnostics=diagnostics,
                on_step=_step,
            )
        chart_dir = module_dir if module.spec.source.chart_name is None else (chart_pin and chart_pin.chart_dir)
        if not dry_run and module.spec.type == "helm" and chart_dir and module.spec.configuration:
            for finding in check_configuration_keys(
                module.spec.configuration, read_chart_values(chart_dir), values_keys(chart_dir)
            ):
                diagnostics.warning(
                    f"{label}: {finding}", location=reference.name, code="helm_unknown_configuration_key"
                )

        by_type.setdefault(module.spec.type, []).append(
            ResolvedModule(
                reference=reference,
                module=module,
                source_path=module_dir,
                chart_pin=chart_pin,
                chart_dependencies=tuple(dependency_records),
                unpinned_dependencies=tuple(unpinned_dependencies),
            )
        )

    for module_type, group in by_type.items():
        integration = resolve_module_integration(index, module_type)
        if dry_run:
            names = ", ".join(item.reference.name for item in group)
            _step(f"would render {module_type} workload for namespace '{namespace.meta.name}' ({names})")
            continue
        try:
            integration.prepare_namespace(namespace, group, resolved=resolved)
        except IntegrationError as exc:
            # A plain Exception, not a StrataError (shared with deploy-side
            # call sites that may want different handling) - left unguarded
            # it would escape command_run()'s `except StrataError` entirely.
            # Reachable today: module_type 'compose' is a real, registered
            # integration that has not implemented prepare_namespace() yet.
            raise UsageError(f"Namespace '{namespace.meta.name}', module type '{module_type}': {exc}") from exc
        _step(f"rendered {module_type} workload for namespace '{namespace.meta.name}'")
    return diagnostics


def can_pull_charts(integration: HelmIntegration) -> bool:
    """Whether `helm` is installed to pull registry charts with.

    Its own function so the test suite can close it: a build test must not pull from the network just because the
    machine running it has `helm` (tests/conftest.py).
    """
    return integration.is_available()


def _pin_registry_chart(
    index: DocumentIndex,
    root: Path,
    remotes: dict[str, SolutionRemoteModel],
    module: ModuleModel,
    *,
    label: str,
    location: str,
    dry_run: bool,
    offline: bool,
    credentials: RemoteCredentialResolver | None,
    diagnostics: Diagnostics,
    on_step: Callable[[str], None],
) -> ChartPin | None:
    """Pin a registry chart: from the `.strata/helm/` cache, else by pulling it (ADR-0054).

    A pull the machine cannot attempt (`--offline`, no `helm`, a credential that needs `--resolve`) leaves the
    chart unpinned with a warning, since only a deploy needs the pin and the deploy refuses an unpinned chart. A
    pull that fails is an error. Returns `None` for an unpinned chart and in a dry run that would pull.
    """
    source = module.spec.source
    wanted = f"chart '{source.chart_name}'" + (f" ({source.chart_version})" if source.chart_version else "")

    def unpinned(reason: str) -> None:
        diagnostics.warning(
            f"{label}: {wanted} is not pinned: {reason}. A deploy of this build stops at this chart; build again "
            "where the chart can be pulled.",
            location=location,
            code=CHART_NOT_PINNED,
        )

    pin, _ = _pin_chart(
        index,
        root,
        remotes,
        chart_coordinates(source),
        source.chart_version,
        wanted=wanted,
        label=label,
        location=location,
        dry_run=dry_run,
        offline=offline,
        credentials=credentials,
        diagnostics=diagnostics,
        on_step=on_step,
        unpinned=unpinned,
    )
    return pin


def _pin_chart(
    index: DocumentIndex,
    root: Path,
    remotes: dict[str, SolutionRemoteModel],
    meta: dict[str, Any],
    version: str | None,
    *,
    wanted: str,
    label: str,
    location: str,
    dry_run: bool,
    offline: bool,
    credentials: RemoteCredentialResolver | None,
    diagnostics: Diagnostics,
    on_step: Callable[[str], None],
    unpinned: Callable[[str], None],
) -> tuple[ChartPin | None, bool]:
    """The chart `meta` names at `version`, from the cache or pulled; and whether it came from the cache.

    Shared by registry charts and local charts' dependencies. `unpinned` reports a pull the machine cannot attempt;
    a failed pull is an error on `diagnostics`. `meta` is `chart_coordinates()`'s shape; credentials come from its
    `chartRemote`, if that remote names an integration.
    """
    chart = str(meta["chartName"])
    cache_root = helm_cache_dir(root)

    if version is not None and is_exact_version(version):
        pin = cached_pin(cache_root, chart_cache_source(meta), chart, version)
        if pin is not None:
            on_step(f"{'would use' if dry_run else 'using'} cached {wanted} for {label}")
            return pin, True
    if dry_run:
        on_step(f"would pull {wanted} for {label}")
        return None, False

    if offline:
        unpinned("--offline was given and it is not in .strata/helm/")
        return None, False
    integration = resolve_module_integration(index, "helm")
    if not isinstance(integration, HelmIntegration):
        unpinned(f"module type 'helm' resolves to {type(integration).__name__}, which cannot pull a chart")
        return None, False
    if not can_pull_charts(integration):
        unpinned(f"'{integration.command}' is not installed")
        return None, False

    registry_credentials = None
    remote_name = meta.get("chartRemote")
    remote = remotes.get(str(remote_name)) if remote_name is not None else None
    if remote is not None and remote.integration and credentials is not None:
        owner = f"remote '{remote.name}' (integration '{remote.integration}')"
        try:
            registry_credentials = credentials.for_registry(remote.integration, owner=owner)
        except CredentialsNeedResolveError as exc:
            unpinned(str(exc))
            return None, False
        except RemoteCredentialError as exc:
            diagnostics.error(f"{label}: {exc}", location=location, code="helm_registry_credentials_invalid")
            return None, False

    try:
        pin = integration.pull_registry_chart(
            meta,
            remotes,
            version=version,
            cache_root=cache_root,
            credentials=registry_credentials,
            diagnostics=diagnostics,
            location=location,
        )
    except RegistryAuthError as exc:
        diagnostics.error(f"{label}: {exc}", location=location, code="helm_registry_auth_failed")
        return None, False
    except ChartPullError as exc:
        diagnostics.error(f"{label}: {exc}", location=location, code=exc.code)
        return None, False
    on_step(f"pinned {wanted} at {pin.version} ({pin.digest}) for {label}")
    return pin, False


#: How deep `file://` dependencies may nest before the build assumes a cycle.
_MAX_FILE_DEPENDENCY_DEPTH = 10


def _place_chart_dependencies(
    index: DocumentIndex,
    root: Path,
    remotes: dict[str, SolutionRemoteModel],
    chart_dir: Path,
    *,
    origin: Path,
    repo_root: Path,
    label: str,
    location: str,
    offline: bool,
    credentials: RemoteCredentialResolver | None,
    diagnostics: Diagnostics,
    on_step: Callable[[str], None],
    depth: int = 0,
) -> tuple[list[DependencyRecord], list[str]]:
    """Fill the build copy `chart_dir`'s `charts/` with its dependencies (docs/work/helm-chart-dependencies.md).

    `origin` is where the chart was copied from and `repo_root` the repository it lives in, so a `file://`
    dependency resolves as Helm would resolve it next to the source, and may not leave that repository. A
    `file://` chart's own dependencies are placed the same way. Returns what was placed or found, and the names of
    the dependencies the machine could not fetch (ADR-0054's unpinned rule).
    """
    records: list[DependencyRecord] = []
    missing: list[str] = []
    # Planned from the source, not the build copy: a copy kept by `--no-clean` holds what an earlier build placed.
    committed = committed_subcharts(origin)
    for plan in plan_dependencies(origin):
        dependency = plan.dependency
        if plan.action is Action.ERROR:
            diagnostics.error(f"{label}: {plan.message}", location=location, code=plan.code)
        elif plan.action is Action.COMMITTED:
            records.append(
                DependencyRecord(
                    dependency.name,
                    committed[dependency.name].version,
                    "committed",
                    repository=dependency.repository,
                )
            )
        elif plan.action is Action.FILE:
            assert dependency.repository is not None  # FILE is planned only for a file:// repository
            try:
                target = resolve_within(repo_root, origin / dependency.repository.removeprefix("file://"))
            except ValueError as exc:
                diagnostics.error(
                    f"{label}: dependency '{dependency.name}' ({dependency.repository}) is outside the chart's "
                    f"repository: {exc}",
                    location=location,
                    code=OUTSIDE_SOURCE,
                )
                continue
            if not (target / "Chart.yaml").is_file():
                diagnostics.error(
                    f"{label}: dependency '{dependency.name}' ({dependency.repository}) is not a chart directory",
                    location=location,
                    code=DEPENDENCY_MISSING,
                )
                continue
            if depth >= _MAX_FILE_DEPENDENCY_DEPTH:
                diagnostics.error(
                    f"{label}: file:// dependencies nest deeper than {_MAX_FILE_DEPENDENCY_DEPTH} levels at "
                    f"'{dependency.name}'; is there a cycle?",
                    location=location,
                    code=DEPENDENCY_MISSING,
                )
                continue
            placed = chart_dir / "charts" / dependency.name
            shutil.copytree(target, placed, dirs_exist_ok=True)
            version = read_chart_yaml(target / "Chart.yaml").get("version")
            records.append(
                DependencyRecord(dependency.name, str(version or ""), "file", repository=dependency.repository)
            )
            on_step(f"copied dependency '{dependency.name}' ({dependency.repository}) for {label}")
            nested, nested_missing = _place_chart_dependencies(
                index,
                root,
                remotes,
                placed,
                origin=target,
                repo_root=repo_root,
                label=label,
                location=location,
                offline=offline,
                credentials=credentials,
                diagnostics=diagnostics,
                on_step=on_step,
                depth=depth + 1,
            )
            records.extend(nested)
            missing.extend(nested_missing)
        else:
            record = _pull_dependency(
                index,
                root,
                remotes,
                chart_dir,
                dependency,
                plan.version,
                label=label,
                location=location,
                offline=offline,
                credentials=credentials,
                diagnostics=diagnostics,
                on_step=on_step,
                missing=missing,
            )
            if record is not None:
                records.append(record)
    return records, missing


def _pull_dependency(
    index: DocumentIndex,
    root: Path,
    remotes: dict[str, SolutionRemoteModel],
    chart_dir: Path,
    dependency: Dependency,
    version: str | None,
    *,
    label: str,
    location: str,
    offline: bool,
    credentials: RemoteCredentialResolver | None,
    diagnostics: Diagnostics,
    on_step: Callable[[str], None],
    missing: list[str],
) -> DependencyRecord | None:
    """Pin one repository dependency through the chart cache and copy its archive into `chart_dir/charts/`."""
    wanted = f"dependency '{dependency.name}'" + (f" ({version})" if version else "")

    def unpinned(reason: str) -> None:
        missing.append(dependency.name)
        diagnostics.warning(
            f"{label}: {wanted} is not pinned: {reason}. A deploy of this build stops at this chart; build again "
            "where the dependency can be pulled.",
            location=location,
            code=DEPENDENCY_NOT_PINNED,
        )

    pin, cached = _pin_chart(
        index,
        root,
        remotes,
        _dependency_coordinates(dependency, remotes),
        version,
        wanted=wanted,
        label=label,
        location=location,
        dry_run=False,
        offline=offline,
        credentials=credentials,
        diagnostics=diagnostics,
        on_step=on_step,
        unpinned=unpinned,
    )
    if pin is None:
        return None
    charts = chart_dir / "charts"
    charts.mkdir(exist_ok=True)
    shutil.copy2(pin.archive, charts / pin.archive.name)
    return DependencyRecord(
        dependency.name,
        pin.version,
        "cache" if cached else "pulled",
        repository=dependency.repository,
        constraint=dependency.version if dependency.version != pin.version else None,
        digest=pin.digest,
    )


def _dependency_coordinates(dependency: Dependency, remotes: dict[str, SolutionRemoteModel]) -> dict[str, Any]:
    """`chart_coordinates()`'s shape for a dependency: a declared helm/oci remote with the same URL, so its
    credentials apply, else the repository URL itself."""
    url = str(dependency.repository).rstrip("/")
    for remote in remotes.values():
        if remote.type in (RemoteType.HELM, RemoteType.OCI) and remote.url.rstrip("/") == url:
            return {"chartName": dependency.name, "chartRemote": remote.name}
    return {"chartName": dependency.name, "chartRepository": dependency.repository}


def _check_dependency_list(
    index: DocumentIndex, chart_dir: Path, *, label: str, location: str, diagnostics: Diagnostics
) -> None:
    """`helm dependency list` on the filled `charts/`, when `helm` is installed: every dependency must be in place."""
    integration = resolve_module_integration(index, "helm")
    if not isinstance(integration, HelmIntegration) or not can_pull_charts(integration):
        return
    result = integration.dependency_list(chart_dir)
    if not result.is_successful:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        diagnostics.error(
            f"{label}: 'helm dependency list' failed — {detail}", location=location, code=DEPENDENCY_MISSING
        )
        return
    for name, status in dependency_list_problems(result.stdout):
        diagnostics.error(
            f"{label}: dependency '{name}' is '{status}' in the chart's charts/ directory "
            "(helm dependency list); check Chart.yaml against Chart.lock and charts/",
            location=location,
            code=DEPENDENCY_MISSING if status == "missing" else WRONG_VERSION,
        )


def resolve_namespace_modules(
    index: DocumentIndex, namespace: NamespaceModel, build_path: Path
) -> dict[str, list[ResolvedModule]]:
    """`deploy_run()`'s counterpart to `build_workload_modules()` (docs/design/gap_fit_v1.md
    gap #13) — resolves `namespace`'s modules, grouped by `module.spec.type`,
    without re-materialising any source.

    Deploy time only *deploys* what `build run` already rendered — it never
    fetches/syncs a module's source again, so this skips
    `sync_module_source()`/`describe_source()` entirely and simply recomputes
    each module's already-known build directory (`build_path/
    namespace.meta.name/reference.name`, the exact same path
    `build_workload_modules()` wrote it to).

    Args:
        index: The loaded, already-`require_valid()`-ed `DocumentIndex`.
        namespace: The namespace whose `spec.modules` to resolve.
        build_path: The build output root `build run` already rendered into.

    Returns:
        Every enabled module reference, grouped by `module.spec.type` — the
        caller (`deploy_controller.py`) filters this to its own integration's
        `TYPE` before calling `deploy_namespace()`.

    Raises:
        UsageError: a module reference does not resolve, or `module.spec.type`
            is unset — same failure modes `build_workload_modules()` raises.
    """
    by_type: dict[str, list[ResolvedModule]] = {}
    for reference in namespace.spec.modules or []:
        if not reference.enabled:
            continue

        module = resolve_module(index, reference)
        if module.spec.type is None:
            raise UsageError(
                f"Namespace '{namespace.meta.name}', module '{reference.name}': "
                "spec.type is required to deploy this module."
            )

        module_dir = build_path / namespace.meta.name / reference.name
        by_type.setdefault(module.spec.type, []).append(
            ResolvedModule(reference=reference, module=module, source_path=module_dir)
        )
    return by_type
