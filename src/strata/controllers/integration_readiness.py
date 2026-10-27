#!/usr/bin/env python3
"""Is an integration usable here? (docs/work/integration-readiness.md D5-D18).

One offline answer per integration - enabled, local tool on PATH, installed version against
`spec.version` - shared by deploy preflight and the `strata integration` commands so they cannot
disagree. Never resolves credentials and never contacts a remote service: that is
`ConnectionCheckIntegration.check_connection()`'s job.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from strata.controllers.build_controller import find_provisioner
from strata.controllers.integration_resolution import (
    bind_integration_config,
    resolve_integration,
    resolve_output_store_integration,
)
from strata.controllers.solution_context import SolutionContext
from strata.controllers.solution_controller import DocumentIndex
from strata.controllers.value_controller import is_direct_store, reachable_environments, resolve_deployment_workspace
from strata.integrations.base import Integration
from strata.integrations.capabilities import ConnectionCheckIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.registry import IntegrationNotFoundError, known_types
from strata.integrations.registry import get as get_integration
from strata.models.common_models import PlatformKind
from strata.models.integration_model import Capability, IntegrationModel
from strata.models.store_model import FeatureStoreModel, SecretStoreModel, VariableStoreModel
from strata.services.environment_service import merge_environment_models
from strata.utils.errors import UsageError
from strata.utils.version_constraints import version_satisfies

#: Capabilities a document may declare without any integration class: its `authentication` is read directly.
_CREDENTIAL_HOLDER_CAPABILITIES = frozenset({Capability.SOURCES})


@dataclass(frozen=True)
class ReadinessProblem:
    code: str
    message: str


@dataclass(frozen=True)
class IntegrationReadiness:
    """What was found about one integration; `problems` is empty when it is usable."""

    name: str
    type: str
    declared: bool
    registered: bool
    enabled: bool
    local_tool: bool
    command: str | None
    available: bool | None
    required_version: str | None
    installed_version: str | None
    connection_check: bool
    problems: tuple[ReadinessProblem, ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def credential_holder(self) -> bool:
        """A `sources`-only document with no class: nothing local to check (D15)."""
        return not self.registered and self.ok


@dataclass(frozen=True)
class CheckSubject:
    """An assessed integration, and the instance when its class could be constructed."""

    integration: Integration | None
    readiness: IntegrationReadiness


@dataclass(frozen=True)
class IntegrationUse:
    """One `strata integration list` row (D14, D16)."""

    readiness: IntegrationReadiness
    used_for: tuple[str, ...]


def assess_named(index: DocumentIndex, name: str, *, read_version: bool = True) -> CheckSubject:
    """The integration `name` means: a document by that name, else what deploy binds for that type (D9).

    Raises:
        UsageError: `name` is neither a document nor a known type, or several enabled documents have that type.
    """
    entry = index.get(PlatformKind.INTEGRATION, name)
    if entry is not None:
        return assess_document(cast(IntegrationModel, entry.model), read_version=read_version)
    types = known_types()
    if name not in types:
        declared = ", ".join(sorted(index.names_of(PlatformKind.INTEGRATION))) or "none"
        raise UsageError(
            f"'{name}' is not declared and is not a known integration type. "
            f"Declared: {declared}. Known types: {', '.join(types)}."
        )
    same_type = sorted(
        entry.ref.name
        for entry in index.all_of(PlatformKind.INTEGRATION)
        if cast(IntegrationModel, entry.model).spec.type == name and cast(IntegrationModel, entry.model).spec.enabled
    )
    if len(same_type) > 1:
        raise UsageError(
            f"Several enabled '{name}' Integration documents ({', '.join(same_type)}); check one of them by name."
        )
    config = bind_integration_config(index, name, requester=f"Integration '{name}'")
    if config is not None:
        return assess_document(config, read_version=read_version)
    return _assess_type(name, read_version=read_version)


def assess_document(config: IntegrationModel, *, read_version: bool = True) -> CheckSubject:
    """Assess a declared document, including one whose class cannot be constructed (D15)."""
    try:
        integration = get_integration(config.spec.type, config=config)
    except IntegrationNotFoundError as exc:
        holder = bool(config.spec.capabilities) and all(
            capability in _CREDENTIAL_HOLDER_CAPABILITIES or capability.startswith("x-")
            for capability in config.spec.capabilities
        )
        problem = None if holder else ReadinessProblem("integration_type_unregistered", f"{config.meta.name}: {exc}")
        return CheckSubject(None, _unconstructed(config.meta.name, config.spec.type, config, problem, registered=False))
    except IntegrationError as exc:
        problem = ReadinessProblem("integration_configuration_invalid", str(exc))
        return CheckSubject(None, _unconstructed(config.meta.name, config.spec.type, config, problem, registered=True))
    return CheckSubject(integration, assess_integration(integration, read_version=read_version))


def assess_integration(integration: Integration, *, read_version: bool = True) -> IntegrationReadiness:
    """Check `integration` without credentials or network.

    Args:
        integration: A constructed integration (already `bind()`-ed for a provisioner, if any).
        read_version: Run the tool to read its version; `False` for `--smoke-test` (no tool contact).
    """
    config = integration.config
    enabled = config.spec.enabled if config is not None else True
    required = config.spec.version if config is not None else None
    problems = _disabled(integration.name, enabled)

    try:
        local_tool = integration.transport == "cli"
    except IntegrationError as exc:
        local_tool = False
        problems.append(ReadinessProblem("integration_configuration_invalid", str(exc)))

    command = integration.command if local_tool else None
    available = integration.is_available() if local_tool else None
    if available is False:
        problems.append(ReadinessProblem("integration_unavailable", f"'{command}' is not available on PATH."))

    installed: str | None = None
    if required is not None:
        if not local_tool:
            problems.append(
                ReadinessProblem(
                    "integration_version_unenforceable",
                    f"{integration.name}: spec.version '{required}' cannot be checked over a networked transport.",
                )
            )
        elif available and read_version:
            installed = integration.get_version()
            if installed is None:
                problems.append(
                    ReadinessProblem(
                        "integration_version_mismatch",
                        f"{integration.name}: could not determine the installed version to check against '{required}'.",
                    )
                )
            elif not version_satisfies(installed, required):
                problems.append(
                    ReadinessProblem(
                        "integration_version_mismatch",
                        f"{integration.name}: installed version {installed} does not satisfy '{required}'.",
                    )
                )

    return IntegrationReadiness(
        name=integration.name,
        type=integration.TYPE,
        declared=config is not None,
        registered=True,
        enabled=enabled,
        local_tool=local_tool,
        command=command,
        available=available,
        required_version=required,
        installed_version=installed,
        connection_check=isinstance(integration, ConnectionCheckIntegration),
        problems=tuple(problems),
    )


def list_integrations(
    context: SolutionContext, deployment_name: str | None = None, *, version_pin: str | None = None
) -> list[IntegrationUse]:
    """Every declared integration, plus (with a deployment) every one it binds, with what it is used for.

    Raises:
        UsageError: Unknown deployment or `--pin`, or an ambiguous auto-bind (as `deploy run` raises).
    """
    used: dict[tuple[str, str | None], tuple[IntegrationReadiness, list[str]]] = {}
    if deployment_name is not None:
        for key, assess, purpose in _deployment_bindings(context, deployment_name, version_pin):
            if key not in used:
                used[key] = (assess(), [])
            used[key][1].append(purpose)
    rows = [IntegrationUse(readiness, tuple(purposes)) for readiness, purposes in used.values()]
    # A type-default row may share a name with a document (R9); only the document's own row hides it.
    used_documents = {readiness.name for readiness, _ in used.values() if readiness.declared}
    for entry in context.controller.index.all_of(PlatformKind.INTEGRATION):
        if entry.ref.name not in used_documents:
            rows.append(IntegrationUse(assess_document(cast(IntegrationModel, entry.model)).readiness, ()))
    return sorted(rows, key=lambda row: (row.readiness.name, row.readiness.command or ""))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_Binding = tuple[tuple[str, str | None], Callable[[], IntegrationReadiness], str]


def _deployment_bindings(context: SolutionContext, deployment_name: str, version_pin: str | None) -> list[_Binding]:
    """What `deploy run` binds for this deployment (D16), each with a lazy assessment."""
    index = context.controller.index
    deployment, workspace = resolve_deployment_workspace(context, deployment_name, version_pin=version_pin)
    bindings: list[_Binding] = []

    def _add_constructed(integration: Integration, purpose: str) -> None:
        bindings.append(((integration.name, integration.command), lambda: assess_integration(integration), purpose))

    def _add_subject(subject: CheckSubject, purpose: str) -> None:
        readiness = subject.readiness
        bindings.append(((readiness.name, readiness.command), lambda: readiness, purpose))

    for step in workspace.spec.execution or []:
        provisioner = find_provisioner(workspace, step.provisioner)
        integration = resolve_integration(index, provisioner)
        _add_constructed(integration, f"provisioner '{provisioner.name}' (step '{step.name}')")

    variables, secrets, features = merge_environment_models(reachable_environments(context, deployment))
    stores: list[VariableStoreModel | SecretStoreModel | FeatureStoreModel] = [
        *variables.values(),
        *secrets.values(),
        *features.values(),
    ]
    counts: dict[str, int] = {}
    for store in stores:
        if not is_direct_store(store):
            counts[store.store.value] = counts.get(store.store.value, 0) + 1
    for store_type, count in sorted(counts.items()):
        config = bind_integration_config(index, store_type, requester=f"Store type '{store_type}'")
        subject = assess_document(config) if config is not None else _assess_type(store_type)
        _add_subject(subject, f"value store ({count} key(s))")

    output_store = resolve_output_store_integration(index, workspace)
    if output_store is not None:
        _add_constructed(output_store, "output store")

    for field_name in ("release_gate_policy", "image_gate_policy"):
        policy = getattr(workspace.spec, field_name)
        entry = index.get(PlatformKind.INTEGRATION, policy.integration) if policy is not None else None
        if entry is not None:
            _add_subject(assess_document(cast(IntegrationModel, entry.model)), field_name)
    return bindings


def _assess_type(integration_type: str, *, read_version: bool = True) -> CheckSubject:
    """A type with no enabled document: its class with defaults, as deploy would construct it."""
    try:
        integration = get_integration(integration_type)
    except IntegrationError as exc:
        problem = ReadinessProblem("integration_type_unregistered", str(exc))
        return CheckSubject(None, _unconstructed(integration_type, integration_type, None, problem, registered=False))
    return CheckSubject(integration, assess_integration(integration, read_version=read_version))


def _unconstructed(
    name: str,
    integration_type: str,
    config: IntegrationModel | None,
    problem: ReadinessProblem | None,
    *,
    registered: bool,
) -> IntegrationReadiness:
    enabled = config.spec.enabled if config is not None else True
    problems = _disabled(name, enabled) + ([problem] if problem is not None else [])
    return IntegrationReadiness(
        name=name,
        type=integration_type,
        declared=config is not None,
        registered=registered,
        enabled=enabled,
        local_tool=False,
        command=None,
        available=None,
        required_version=config.spec.version if config is not None else None,
        installed_version=None,
        connection_check=False,
        problems=tuple(problems),
    )


def _disabled(name: str, enabled: bool) -> list[ReadinessProblem]:
    return [] if enabled else [ReadinessProblem("integration_disabled", f"{name} is disabled (spec.enabled: false).")]
