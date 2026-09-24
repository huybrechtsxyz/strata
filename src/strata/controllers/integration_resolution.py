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

Auto-bind itself (`_auto_bind_config()`): zero or one *enabled*
`Integration` document whose `spec.type` matches construct the same way
(zero falls back to the registry's env/PATH-only default, matching what
Phases 4-6 already default to without any `Integration` document at all);
more than one is an error naming every candidate by name, never a guess.
"""

from typing import cast

from strata.controllers.solution_controller import DocumentIndex
from strata.integrations.base import Integration
from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.registry import get as get_integration
from strata.models.common_models import PlatformKind
from strata.models.integration_model import IntegrationModel
from strata.models.provisioning_model import ProvisionerModel
from strata.utils.errors import UsageError


def resolve_integration(index: DocumentIndex, provisioner: ProvisionerModel) -> InfraIntegration:
    """Return the `InfraIntegration` instance `provisioner` should run against.

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
        config = _auto_bind_config(index, provisioner.tool, requester=f"Provisioner '{provisioner.name}'")
        integration = _construct(provisioner.tool, config, context=f"Provisioner '{provisioner.name}'")

    return _require_infra_integration(f"Provisioner '{provisioner.name}': tool '{provisioner.tool}'", integration)


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
    config = _auto_bind_config(index, module_type, requester=f"Module type '{module_type}'")
    integration = _construct(module_type, config, context=f"Module type '{module_type}'")
    return _require_infra_integration(f"Module type '{module_type}'", integration)


def _auto_bind_config(index: DocumentIndex, integration_type: str, *, requester: str) -> IntegrationModel | None:
    """Zero-or-one-enabled-match auto-bind, shared by both entry points above.

    Raises:
        UsageError: more than one enabled `Integration` document declares
            `spec.type == integration_type` — named in the message so the
            fix (`integration:`/named binding) is obvious, never a guess.
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
        raise UsageError(f"{context} resolves to '{type(integration).__name__}', which is not an infrastructure/container integration.")
    return integration
