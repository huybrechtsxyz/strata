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

from collections.abc import Callable
from pathlib import Path
from typing import cast

from strata.controllers.integration_resolution import resolve_module_integration
from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.source_sync import describe_source, sync_module_source
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.common_models import ModuleReferenceModel, PlatformKind
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.solution_model import SolutionRemoteModel
from strata.utils.errors import UsageError


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
            f"Module reference '{reference.name}' names module '{reference.module}', "
            "which is not in the index."
        )
    return cast(ModuleModel, entry.model)


def build_workload_modules(
    index: DocumentIndex,
    root: Path,
    remotes: dict[str, SolutionRemoteModel],
    namespace: NamespaceModel,
    resolved: ValueResolution,
    build_path: Path,
    *,
    dry_run: bool = False,
    on_step: Callable[[str], None] | None = None,
) -> None:
    """Render every module `namespace` declares (ADR-0022 D6).

    Resolves and materialises each module's source first, then groups by
    `module.spec.type` and hands each same-type group to whichever
    `InfraIntegration` that type resolves to — grouping/merging behaviour
    (Compose merges every module into one file; Helm never merges) is
    entirely that class's own concern via `prepare_namespace()`, never
    encoded here.

    Skips a `ModuleReferenceModel` with `enabled=False` entirely — neither
    resolved, materialised, nor rendered. Matches
    `terraform_projection._build_resources_payload()`'s identical treatment
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
        dry_run: Report what would happen instead of doing it — skips
            materialising a module's source and skips `prepare_namespace()`.
            `resolve_module()`/`resolve_module_integration()` still run, so
            a dry run still catches an unresolvable module reference or an
            unsupported module type.
        on_step: Called with a one-line progress message per module
            materialised and per type-group rendered — real work when
            `dry_run` is `False`, planned work when it's `True`.

    Raises:
        UsageError: a module reference does not resolve, `module.spec.type`
            is unset, or its resolved integration is not infra/container-capable.
        SourceSyncError: a module's source could not be materialised.
    """

    def _step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    by_type: dict[str, list[ResolvedModule]] = {}

    for reference in namespace.spec.modules or []:
        if not reference.enabled:
            continue

        module = resolve_module(index, reference)
        if module.spec.type is None:
            raise UsageError(
                f"Namespace '{namespace.meta.name}', module '{reference.name}': "
                "spec.type is required to render this module."
            )

        module_dir = build_path / namespace.meta.name / reference.name
        if dry_run:
            _step(
                f"would materialise module '{reference.name}' ({describe_source(module.spec.source)}) "
                f"for namespace '{namespace.meta.name}'"
            )
        else:
            sync_module_source(root, module_dir, module.spec.source, remotes)
            _step(f"materialised module '{reference.name}' for namespace '{namespace.meta.name}' at {module_dir}")

        by_type.setdefault(module.spec.type, []).append(
            ResolvedModule(reference=reference, module=module, source_path=module_dir)
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
