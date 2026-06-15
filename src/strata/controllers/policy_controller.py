#!/usr/bin/env python3
"""Controller for policy introspection and evaluation."""

from typing import List, Optional

from strata.controllers.base_controller import BaseController
from strata.models.policy_model import PolicyModel

# Maps provisioner type keywords to the lifecycle phases they trigger.
# "validate" is always triggered regardless of provisioner type.
# "tf_" is a common short-form prefix for terraform provisioner names.
_PROVISIONER_PHASE_MAP: dict[str, list[str]] = {
    "terraform": ["build", "plan", "deploy"],
    "tf_": ["build", "plan", "deploy"],
    "helm": ["build", "deploy"],
    "ansible": ["build", "deploy"],
    "compose": ["build", "deploy"],
    "script": ["build", "deploy"],
}


class PolicyController(BaseController):
    """Orchestrates policy introspection across services.

    Phase 1 — introspection (``policy list``):

    * :meth:`get_declared_policies` — extract ``configuration.spec.policies``
    * :meth:`get_deployment_phases` — determine which lifecycle phases a
      deployment's stages can trigger (used for annotation in ``list`` output)

    Phase 2 — evaluation (``policy check``, future):

    * ``evaluate(phase, context)`` — run :class:`~strata.validators.policies.PolicyEngine`
      for a given phase and return the results
    """

    def get_declared_policies(self, configuration_service) -> List[PolicyModel]:
        """Extract ``configuration.spec.policies``.

        Returns an empty list when the configuration service is not loaded,
        the model is absent, or no policies have been declared.
        """
        if configuration_service is None:
            return []
        model = getattr(configuration_service, "model", None)
        if model is None:
            return []
        spec = getattr(model, "spec", None)
        if spec is None:
            return []
        return list(getattr(spec, "policies", None) or [])

    def get_deployment_phases(self, deployment_service) -> List[str]:
        """Determine which lifecycle phases a deployment's stages can trigger.

        Inspects each stage's ``provisioner`` and ``topology`` name for
        known type keywords (``terraform``, ``helm``, ``ansible``, etc.) and
        maps them to the policy phases they gate.  ``validate`` is always
        included.

        The result is heuristic — it relies on provisioner names containing
        a recognisable keyword.  When the type cannot be inferred, ``build``
        and ``deploy`` are assumed.

        Returns a sorted, deduplicated list of phase names.
        """
        phases: set[str] = {"validate"}

        if deployment_service is None:
            return sorted(phases)

        model = getattr(deployment_service, "model", None)
        if model is None:
            return sorted(phases)

        spec = getattr(model, "spec", None)
        if spec is None:
            return sorted(phases)

        stages = getattr(spec, "stages", None) or []
        for stage in stages:
            inferred = self._infer_provisioner_type(stage)
            if inferred:
                phases.update(_PROVISIONER_PHASE_MAP.get(inferred, ["build", "deploy"]))
            else:
                # Unknown provisioner — assume build + deploy are relevant
                phases.update(["build", "deploy"])

        return sorted(phases)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _infer_provisioner_type(self, stage) -> Optional[str]:
        """Infer provisioner type from a stage's ``provisioner`` or ``topology`` name.

        Checks each candidate string for known type keywords.  Returns the
        first match, or ``None`` when no keyword is found.
        """
        candidates = [
            str(getattr(stage, "provisioner", "") or "").lower(),
            str(getattr(stage, "topology", "") or "").lower(),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            for known_type in _PROVISIONER_PHASE_MAP:
                if known_type in candidate:
                    return known_type
        return None
