#!/usr/bin/env python3
"""Binding a `ProvisionerModel` to a real `InfraIntegration` instance (ADR-0022 D2).

Reads only strings — `provisioner.integration`/`.tool` — never a tool
identity the orchestrator would have to know about. Two paths, named
binding always wins:

- `provisioner.integration` set — resolve that exact `Integration` document
  by name (existence already validated by Phase 2's `validate_references`,
  the same reference-checking pass every other named reference goes
  through) and construct via `registry.get(doc.spec.type, config=doc)`.
- Unset — auto-bind: zero or one *enabled* `Integration` document whose
  `spec.type == provisioner.tool` construct the same way (zero falls back
  to the registry's env/PATH-only default, matching what Phases 4-6 already
  default to without any `Integration` document at all); more than one is
  an error naming every candidate by name, never a guess.
"""

from typing import cast

from strata.controllers.solution_controller import DocumentIndex
from strata.integrations.base import Integration
from strata.integrations.capabilities import InfraIntegration
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
            `provisioner.tool`, or `provisioner.tool` resolves to an
            integration that is not infrastructure/container-capable (e.g.
            a store type used by mistake).
    """
    config: IntegrationModel | None
    if provisioner.integration:
        entry = index.get(PlatformKind.INTEGRATION, provisioner.integration)
        if entry is None:
            raise UsageError(
                f"Provisioner '{provisioner.name}' names integration '{provisioner.integration}', "
                "which is not in the index."
            )
        config = cast(IntegrationModel, entry.model)
        integration = get_integration(config.spec.type, config=config)
    else:
        candidates = [
            entry
            for entry in index.all_of(PlatformKind.INTEGRATION)
            if cast(IntegrationModel, entry.model).spec.enabled
            and cast(IntegrationModel, entry.model).spec.type == provisioner.tool
        ]
        if len(candidates) > 1:
            names = [entry.ref.name for entry in candidates]
            raise UsageError(
                f"Provisioner '{provisioner.name}': multiple '{provisioner.tool}' integrations declared "
                f"({names}) - set 'integration:' explicitly."
            )
        config = cast(IntegrationModel, candidates[0].model) if candidates else None
        integration = get_integration(provisioner.tool, config=config)

    return _require_infra_integration(provisioner, integration)


def _require_infra_integration(provisioner: ProvisionerModel, integration: Integration) -> InfraIntegration:
    if not isinstance(integration, InfraIntegration):
        raise UsageError(
            f"Provisioner '{provisioner.name}': tool '{provisioner.tool}' resolves to "
            f"'{type(integration).__name__}', which is not an infrastructure/container integration."
        )
    return integration
