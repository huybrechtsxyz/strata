#!/usr/bin/env python3
"""Capability ABCs (ADR-0021 D1/D9) — the contract half of the design.

`Integration` (`base.py`) owns identity, configuration, and every transport
a class may speak. These ABCs own only *what the capability requires*,
nothing about how the work travels — that split is what lets one class
(e.g. a future Consul integration) serve both a CLI-configured and an
API-configured solution without a second class.

Core, closed, dispatched-on capabilities only. An `x-`-prefixed capability
(ADR-0021 D9) is carried by `IntegrationModel` but never reaches here —
there is nothing to dispatch it to.
"""

from abc import abstractmethod
from pathlib import Path
from typing import Any

from strata.integrations.base import Integration
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.models.provisioning_model import ProvisionerModel
from strata.utils.transport import CommandResult


class StoreIntegration(Integration):
    """Capability: `variables` / `secrets` / `features`. Resolves a key to a value."""

    @abstractmethod
    def resolve(self, key: str) -> str:
        """Return the value for `key`.

        Raises:
            strata.integrations.errors.ValueResolutionError: `key` does not
                exist, or the store could not be reached/authenticated.
        """


class InfraIntegration(Integration):
    """Capability: `infrastructure` / `container`. Plans and applies change.

    One ABC for both capability strings (ADR-0021 D6): the label says what
    *kind* of thing is provisioned (Terraform/Ansible/Bicep vs. Compose/
    Helm) for a human reading the document; the contract is identical.
    """

    def prepare(
        self,
        path: Path,
        *,
        resolved: ValueResolution,
        provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph,
        **kwargs: Any,
    ) -> Path:
        """Render whatever this tool needs into `path` from already-resolved
        values/documents and this provisioner's own typed config. Returns
        the path `plan`/`deploy`/`destroy` should be called against.

        Base-implemented, not abstract (ADR-0023 D5) — every subclass gets
        the same dispatch for free; only `default_output()` varies per
        tool. The `provisioner.output.template` escape hatch (D3) and the
        `provisioner.backend` token-substitution step (D2) are later
        phases (ADR-0023 Phase 3/4) and are not implemented here yet — this
        phase only wires the `default_output()` branch.
        """
        for filename, content in self.default_output(resolved, provisioner, graph).items():
            (path / filename).write_text(content)
        return path

    def default_output(
        self,
        resolved: ValueResolution,
        provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph,
    ) -> dict[str, str]:
        """Filename -> content pairs to write when no `output.template` is
        set (D3 does not exist yet — Phase 4). Base default: nothing
        generated — Bicep's real behaviour (v1's `bicep_builder.py`: copy
        the source, generate nothing); subclasses override only this hook.
        """
        del resolved, provisioner, graph
        return {}

    @abstractmethod
    def plan(self, path: Path, **kwargs: Any) -> CommandResult:
        """Preview the change `path`'s code would make, without applying it."""

    @abstractmethod
    def deploy(self, path: Path, **kwargs: Any) -> CommandResult:
        """Apply the change `path`'s code describes."""

    @abstractmethod
    def destroy(self, path: Path, **kwargs: Any) -> CommandResult:
        """Tear down what `path`'s code previously created."""


#: Capability string -> the ABC a class declaring it must implement.
#: `"sources"` has no entry yet — remote fetching is not built (ADR-0021
#: D9); a capability with no entry here is declarable but not dispatchable,
#: which `find_capability_mismatches` treats as compliant, not an error.
CAPABILITY_ABCS: dict[str, type[Integration]] = {
    "variables": StoreIntegration,
    "secrets": StoreIntegration,
    "features": StoreIntegration,
    "infrastructure": InfraIntegration,
    "container": InfraIntegration,
}


def find_capability_mismatches(integration_cls: type[Integration]) -> list[str]:
    """Every core capability `integration_cls` declares whose ABC it does not implement.

    A class declaring a capability whose contract it doesn't satisfy is a
    programming error, not something a document author can trigger — this
    is what catches it. Used directly here (Phase 3, against fake test
    classes) and reused by the registry's own test once real classes exist
    to walk (ADR-0021 Phase 4).

    Args:
        integration_cls: The class to check, not an instance — the pairing
            is a property of the class, checkable before anything is
            configured or constructed.

    Returns:
        One message per mismatch. Empty when `integration_cls` is compliant,
        which includes declaring only capabilities with no ABC yet.
    """
    mismatches = []
    for capability in integration_cls.CAPABILITIES:
        abc = CAPABILITY_ABCS.get(capability)
        if abc is not None and not issubclass(integration_cls, abc):
            mismatches.append(f"{integration_cls.__name__} declares '{capability}' but does not implement {abc.__name__}")
    return mismatches
