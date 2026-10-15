#!/usr/bin/env python3
"""Binding a `ProvisionerModel`/workload module `type` to a real
`InfraIntegration` instance (ADR-0022 D2, D5).

Reads only strings — `provisioner.integration`/`.tool`, or a bare module
`type` string — never a tool identity the orchestrator would have to know
about. Two entry points, sharing one auto-bind rule:

- `resolve_integration()` (D2, provisioners) — `provisioner.integration`
  set wins outright: resolve that exact `Integration` document by name
  (existence already validated by Phase 2's `validate_references`) and
  construct via `registry.get(doc.spec.type, config=doc)`. Unset falls back
  to auto-bind on `provisioner.tool`.
- `resolve_module_integration()` (D5, workload modules) — a Module has no
  `integration:` binding field at all (D5's own finding: `module.spec.type`
  is looked up directly, no `ProvisionerModel` involved), so only the
  auto-bind half applies, keyed on the module's bare `type` string.
- `resolve_output_store_integration()` (docs/work/cross-run-step-output-
  delivery.md's Implementation Plan step 1) — same named-binding-wins-
  else-auto-bind duality as `resolve_integration()`, keyed on
  `WorkspaceModel.spec.output_store_integration`/`.output_store` instead
  of a Provisioner's fields. The one real difference: both fields unset
  is not an error here — it means the feature is disabled for this
  workspace, so it returns `None` rather than falling through to any
  default (unlike a Provisioner, which always names a `tool`).

Auto-bind itself (`bind_integration_config()`): zero or one *enabled*
`Integration` document whose `spec.type` matches construct the same way
(zero falls back to the registry's env/PATH-only default, matching what
Phases 4-6 already default to without any `Integration` document at all);
more than one is an error naming every candidate by name, never a guess.
Public (no leading underscore) since `value_controller.py`'s store
resolution (docs/design/store-integration-configuration.md) reuses this
exact rule too, not just the two `InfraIntegration` entry points here.
"""

from typing import cast

from strata.controllers.solution_controller import DocumentIndex
from strata.integrations.base import Integration
from strata.integrations.capabilities import InfraIntegration, KeyValueStoreIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.registry import get as get_integration
from strata.models.common_models import PlatformKind
from strata.models.integration_model import IntegrationModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.errors import UsageError


def resolve_integration(index: DocumentIndex, provisioner: ProvisionerModel) -> InfraIntegration:
    """Return the `InfraIntegration` instance `provisioner` should run against.

    Calls `integration.bind(provisioner)` (base no-op, `base.py`) before
    returning — `ScriptIntegration`'s own use case (docs/design/
    script-provisioner.md): which interpreter to use depends on the specific
    provisioner's `script.file`, not a fixed class-level `COMMAND`. Harmless
    for every other integration.

    Args:
        index: The loaded, already-`require_valid()`-ed `DocumentIndex`.
        provisioner: The provisioner naming a tool (`.tool`) and, optionally,
            a specific `Integration` document (`.integration`).

    Returns:
        A new `InfraIntegration` instance (never cached — matches
        `registry.get()`'s own no-cache design).

    Raises:
        UsageError: A named `provisioner.integration` is missing from the
            index (should not happen post-validation; defensive), more than
            one enabled `Integration` document auto-bind-matches
            `provisioner.tool`, `provisioner.tool` is not a registered
            integration type (built-in or plugin), or it resolves to an
            integration that is not infrastructure/container-capable (e.g.
            a store type used by mistake).
    """
    if provisioner.integration:
        entry = index.get(PlatformKind.INTEGRATION, provisioner.integration)
        if entry is None:
            raise UsageError(
                f"Provisioner '{provisioner.name}' names integration '{provisioner.integration}', "
                "which is not in the index."
            )
        named_config = cast(IntegrationModel, entry.model)
        integration = _construct(named_config.spec.type, named_config, context=f"Provisioner '{provisioner.name}'")
    else:
        config = bind_integration_config(index, provisioner.tool, requester=f"Provisioner '{provisioner.name}'")
        integration = _construct(provisioner.tool, config, context=f"Provisioner '{provisioner.name}'")

    infra_integration = _require_infra_integration(
        f"Provisioner '{provisioner.name}': tool '{provisioner.tool}'", integration
    )
    infra_integration.bind(provisioner)
    return infra_integration


def resolve_module_integration(index: DocumentIndex, module_type: str) -> InfraIntegration:
    """Return the `InfraIntegration` instance a workload module of
    `module_type` should render against (ADR-0022 D5).

    Bare type lookup only — a Module has no `integration:` field to bind by
    name, unlike a Provisioner, so there is no named-binding branch here to
    mirror `resolve_integration()`'s.

    Args:
        index: The loaded, already-`require_valid()`-ed `DocumentIndex`.
        module_type: `ModuleSpecModel.type` (e.g. `"helm"`, `"compose"`).

    Returns:
        A new `InfraIntegration` instance.

    Raises:
        UsageError: more than one enabled `Integration` document auto-bind-
            matches `module_type`, `module_type` is not a registered
            integration type, or it resolves to an integration that is not
            infrastructure/container-capable.
    """
    config = bind_integration_config(index, module_type, requester=f"Module type '{module_type}'")
    integration = _construct(module_type, config, context=f"Module type '{module_type}'")
    return _require_infra_integration(f"Module type '{module_type}'", integration)


def bind_integration_config(index: DocumentIndex, integration_type: str, *, requester: str) -> IntegrationModel | None:
    """Zero-or-one-enabled-match auto-bind, shared by every caller that
    resolves an `Integration` document by **type** rather than by name —
    `resolve_integration()`/`resolve_module_integration()` below
    (`InfraIntegration`), and `value_controller.py`'s store resolution
    (`StoreIntegration`, docs/design/store-integration-configuration.md) —
    the same rule applies regardless of which capability tier is asking.

    Raises:
        UsageError: more than one enabled `Integration` document declares
            `spec.type == integration_type` — named in the message so the
            fix (`integration:`/named binding, where that field exists;
            otherwise disabling all but one) is obvious, never a guess.
    """
    candidates = [
        entry
        for entry in index.all_of(PlatformKind.INTEGRATION)
        if cast(IntegrationModel, entry.model).spec.enabled
        and cast(IntegrationModel, entry.model).spec.type == integration_type
    ]
    if len(candidates) > 1:
        names = [entry.ref.name for entry in candidates]
        raise UsageError(
            f"{requester}: multiple '{integration_type}' integrations declared ({names}) - "
            "set 'integration:' explicitly."
        )
    return cast(IntegrationModel, candidates[0].model) if candidates else None


def _construct(integration_type: str, config: IntegrationModel | None, *, context: str) -> Integration:
    """`registry.get()`, with `IntegrationError` translated into `UsageError`.

    `IntegrationError` (`strata.integrations.errors`) is a plain `Exception`,
    not a `StrataError` — deliberately, since it also serves `deploy run`
    call sites (`plan`/`deploy`/`destroy`) where a caught construction
    failure may want different handling. Left unguarded, it escapes
    `command_run()`'s `except StrataError` entirely and surfaces to a user
    as a raw traceback instead of a clean exit code — confirmed reachable
    today via `provisioner.tool: "ansible"` (a real, documented-but-not-yet-
    ported v1 type, `registry.get()` raises `IntegrationNotFoundError`).
    Every construction happens at this one call site for that reason.

    Raises:
        UsageError: `integration_type` is not registered (built-in or
            plugin), or its class rejects `config` (a caller/config bug).
    """
    try:
        return get_integration(integration_type, config=config)
    except IntegrationError as exc:
        raise UsageError(f"{context}: {exc}") from exc


def _require_infra_integration(context: str, integration: Integration) -> InfraIntegration:
    if not isinstance(integration, InfraIntegration):
        raise UsageError(
            f"{context} resolves to '{type(integration).__name__}', which is not an infrastructure/container integration."
        )
    return integration


def resolve_output_store_integration(
    index: DocumentIndex, workspace: WorkspaceModel
) -> KeyValueStoreIntegration | None:
    """Return the `KeyValueStoreIntegration` instance `workspace`'s cross-run
    step-output store should read/write against, or `None` when the
    feature is disabled for this workspace (docs/work/
    cross-run-step-output-delivery.md's Implementation Plan step 1).

    Same named-binding-wins-else-auto-bind duality as `resolve_integration()`
    above, just keyed on `output_store_integration`/`output_store` instead
    of a Provisioner's `.integration`/`.tool`. **Unlike `resolve_integration()`,
    both fields unset is not an error** — it means cross-run output
    persistence is simply disabled for this workspace (today's in-memory-
    only behavior), so this returns `None` rather than falling through to
    any default.

    Args:
        index: The loaded, already-`require_valid()`-ed `DocumentIndex`.
        workspace: The workspace naming a backend type (`.output_store`)
            and/or a specific `Integration` document
            (`.output_store_integration`).

    Returns:
        A new `KeyValueStoreIntegration` instance (never cached — matches
        `registry.get()`'s own no-cache design), or `None` when neither
        field is set.

    Raises:
        UsageError: A named `output_store_integration` is missing from the
            index (should not happen post-validation; defensive), more than
            one enabled `Integration` document auto-bind-matches
            `output_store`, `output_store` is not a registered integration
            type (built-in or plugin), it resolves to an integration
            that does not implement `KeyValueStoreIntegration` (e.g. a
            typo'd `output_store: "terraform"`), or it resolves to one
            that does but does not support compare-and-swap at all (e.g.
            `azure-keyvault` — should not happen post-validation, a static
            check already catches it in `semantic_checks.py`; defensive
            for a caller that skipped validation).
    """
    context = f"Workspace '{workspace.meta.name}'"
    if workspace.spec.output_store_integration:
        entry = index.get(PlatformKind.INTEGRATION, workspace.spec.output_store_integration)
        if entry is None:
            raise UsageError(
                f"{context} names output_store_integration '{workspace.spec.output_store_integration}', "
                "which is not in the index."
            )
        named_config = cast(IntegrationModel, entry.model)
        integration = _construct(named_config.spec.type, named_config, context=context)
    elif workspace.spec.output_store:
        config = bind_integration_config(index, workspace.spec.output_store, requester=context)
        integration = _construct(workspace.spec.output_store, config, context=context)
    else:
        return None

    return _require_key_value_integration(context, integration)


def _require_key_value_integration(context: str, integration: Integration) -> KeyValueStoreIntegration:
    if not isinstance(integration, KeyValueStoreIntegration):
        raise UsageError(
            f"{context} resolves to '{type(integration).__name__}', which does not implement the key/value "
            "store capability."
        )
    if not integration.SUPPORTS_CAS:
        raise UsageError(
            f"{context} resolves to '{type(integration).__name__}', which does not support compare-and-swap "
            "— required for cross-run step-output persistence's read-modify-write. Use a backend with real "
            "CAS support instead (e.g. 'azure-appconfig', 'azure-blob')."
        )
    return integration
