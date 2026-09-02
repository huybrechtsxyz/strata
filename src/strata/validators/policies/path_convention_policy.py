#!/usr/bin/env python3
"""Built-in policy: path convention enforcement.

Validates that files on disk follow the directory structure conventions declared
in ``configuration.spec.paths``.  Each convention entry specifies a ``scope``
glob, a ``pattern`` with ``{segment}`` captures, and optional per-segment
``validate`` rules (model field membership or file existence).

Convention sources (resolved in priority order):
1. **Inline** — ``policy.configuration.scope`` + ``policy.configuration.pattern``
   are present → use as a single inline convention (deploy-repo mode, no
   configuration model required).
2. **spec.paths** — read from the loaded configuration service's
   ``model.spec.paths`` list.

Both sources support the ``configuration.conventions`` filter list to restrict
which named conventions are evaluated.

Graceful degradation
--------------------
- No conventions configured (neither inline nor spec.paths) → pass with skip reason
- ``spec.*`` rules without a configuration service → warn + skip (never fail)
- File existence rules work in surface validation (no configuration needed)
- ``file_path`` not in PolicyContext → pass with skip reason

Examples::

    # All conventions, deny enforcement
    policies:
      - name: enforce-paths
        type: path_convention
        phase: validate
        enforcement: deny

    # Selective convention check with warn level
    policies:
      - name: advisory-landscape
        type: path_convention
        phase: validate
        enforcement: warn
        configuration:
          conventions: [landscape-registry]

    # Deploy-repo: inline convention (no spec.paths required)
    policies:
      - name: deploy-layout
        type: path_convention
        phase: validate
        enforcement: deny
        configuration:
          scope: "deploy/**"
          pattern: "deploy/{landscape}/{ring}"
          validate:
            landscape: "deploy/{landscape}/landscape.yaml"
"""

from typing import Any, Dict, List, Optional

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class PathConventionPolicy(BasePolicy):
    """Validate that files follow the declared directory structure conventions."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        # Must have file_path and work_path to evaluate
        if context.file_path is None or context.work_path is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no file_path in context"},
            )

        try:
            rel_path = context.file_path.relative_to(context.work_path).as_posix()
        except ValueError:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": f"file_path not under work_path: {context.file_path}"},
            )

        configuration: Dict[str, Any] = self.policy.configuration or {}

        # Resolve convention list
        conventions = self._resolve_conventions(configuration, context)
        if not conventions:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no path conventions configured"},
            )

        # Apply optional filter
        filter_names: Optional[List[str]] = configuration.get("conventions")
        if filter_names:
            conventions = [c for c in conventions if str(c.name) in filter_names]
            if not conventions:
                return PolicyResult(
                    passed=True,
                    policy_name=self.name,
                    enforcement=self.enforcement,
                    details={"skipped": f"no conventions matched filter: {filter_names}"},
                )

        # Resolve configuration model for spec.* rules (may be None — graceful)
        config_model = None
        if context.configuration_service is not None:
            config_model = getattr(context.configuration_service, "model", None)

        # Resolve this file's deployment.spec.layers, if the file currently being
        # validated is the loaded deployment (may be None — graceful; see
        # evaluate_conventions()'s ADR-0072 resolved-value validation for
        # resolves: layers conventions).
        deployment_layers = None
        if context.deployment_service is not None:
            deployment_model = getattr(context.deployment_service, "model", None)
            deployment_spec = getattr(deployment_model, "spec", None)
            deployment_layers = getattr(deployment_spec, "layers", None)

        # Evaluate all matching conventions
        from strata.utils.path_convention import evaluate_conventions

        violations = evaluate_conventions(
            rel_path=rel_path,
            conventions=conventions,
            work_path=context.work_path,
            configuration_model=config_model,
            deployment_layers=deployment_layers,
        )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )

    def _resolve_conventions(self, configuration: Dict[str, Any], context: PolicyContext):
        """Resolve the list of PathConventionModel entries to evaluate.

        Priority:
        1. Inline convention on policy (deploy-repo mode)
        2. spec.paths from configuration service
        """
        from strata.models.configuration_model import PathConventionModel

        # Inline convention (deploy-repo mode)
        if "scope" in configuration and "pattern" in configuration:
            try:
                inline = PathConventionModel(
                    name="inline",
                    scope=configuration["scope"],
                    pattern=configuration["pattern"],
                    rules=configuration.get("validate"),
                )
                return [inline]
            except Exception:
                return []

        # spec.paths from configuration service
        if context.configuration_service is not None:
            model = getattr(context.configuration_service, "model", None)
            if model is not None:
                spec = getattr(model, "spec", None)
                if spec is not None:
                    paths = getattr(spec, "paths", None)
                    if paths:
                        return paths

        return []
