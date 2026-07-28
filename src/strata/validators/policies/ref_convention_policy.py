#!/usr/bin/env python3
"""Built-in policy: remote reference convention enforcement.

Evaluates at the ``validate`` phase. Checks that remote references (pins to
git tags, branches, or commits) follow the naming conventions declared on
each remote itself.

Configuration
-------------
None required. This policy is config-free — patterns are declared once, on
the remote itself, via ``spec.remotes[].conventions`` (``RemoteConventionsModel``):

::

    spec:
      remotes:
        - name: my-service
          repository: https://github.com/acme/my-service
          reference: main
          conventions:
            release_pattern: "^v\\d+\\.\\d+\\.\\d+$"
            quality_pattern: "^tested(-\\d+)?$"

      policies:
        - name: release_conventions
          type: ref_convention
          phase: validate
          enforcement: warn

This keeps the naming convention declared in exactly one place — on the
remote — instead of duplicated inside a separate policy configuration
block that could drift out of sync with the remote's actual name.

Graceful degradation
--------------------
- No configuration service in context → pass (skip)
- No remotes declared, or none have ``conventions`` set → pass (skip)
- No deployments/environments found → pass (skip)
- Remote override references a remote with no ``conventions`` → pass (skip that remote)
"""

import re
from typing import Any, Dict, List, Optional

from strata.logger import get_logger
from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

logger = get_logger(__name__)


class RefConventionPolicy(BasePolicy):
    """Validate that remote references follow the conventions declared on each remote."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        # --- Guard: services required ---
        if context.deployment_service is None or context.configuration_service is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no deployment or configuration service"},
            )

        # --- Build remote name -> patterns mapping from spec.remotes[].conventions ---
        remotes = context.configuration_service.get_remotes() or []
        remote_patterns: Dict[str, Dict[str, Optional[str]]] = {}
        for remote in remotes:
            name = remote.name
            conventions = remote.conventions
            if not name or conventions is None:
                continue
            if not conventions.release_pattern and not conventions.quality_pattern:
                continue
            remote_patterns[name] = {
                "release": conventions.release_pattern,
                "quality": conventions.quality_pattern,
            }

        if not remote_patterns:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no remotes with conventions declared"},
            )

        violations: List[str] = []

        try:
            # Collect all deployments
            deployment_service = context.deployment_service
            all_deployments = list(deployment_service.all())

            for deployment in all_deployments:
                deployment_name = deployment.model.meta.name
                remotes = deployment.model.spec.overrides.remotes or []
                violations.extend(
                    self._check_remotes(
                        remotes,
                        remote_patterns,
                        f"deployment '{deployment_name}'",
                    )
                )

            # Collect all environments
            config_service = context.configuration_service
            if config_service and hasattr(config_service, "environments"):
                for environment in config_service.environments:
                    env_name = environment.meta.name
                    remotes = environment.spec.overrides.remotes or []
                    violations.extend(
                        self._check_remotes(
                            remotes,
                            remote_patterns,
                            f"environment '{env_name}'",
                        )
                    )

        except Exception as e:
            logger.warning(
                "Failed to check ref conventions",
                error=str(e),
                exc_info=True,
            )
            # Silently skip — this is not a ref convention violation
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": f"error during evaluation: {str(e)}"},
            )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )

    def _check_remotes(
        self,
        remotes: List[Any],
        patterns: Dict[str, Dict[str, Optional[str]]],
        context: str,
    ) -> List[str]:
        """Check each remote reference against its declared pattern.

        Args:
            remotes: List of RemoteOverride objects
            patterns: Dict mapping remote name -> {release, quality patterns}
            context: Human-readable context (e.g., "deployment 'acme'")

        Returns:
            List of violation strings
        """
        violations = []

        for remote in remotes:
            name = getattr(remote, "name", None)
            reference = getattr(remote, "reference", None)

            if not name or not reference:
                continue

            if name not in patterns:
                # No convention declared for this remote — skip
                continue

            pattern_config = patterns[name]
            release_pattern = pattern_config.get("release")
            quality_pattern = pattern_config.get("quality")

            # Check if reference matches any declared pattern
            matches_release = release_pattern and self._matches_pattern(reference, release_pattern)
            matches_quality = quality_pattern and self._matches_pattern(reference, quality_pattern)

            if not (matches_release or matches_quality):
                # Reference doesn't match any pattern
                pattern_strs = []
                if release_pattern:
                    pattern_strs.append(f"release: {release_pattern}")
                if quality_pattern:
                    pattern_strs.append(f"quality: {quality_pattern}")
                pattern_desc = " or ".join(pattern_strs)

                violations.append(
                    f"{context} → remote '{name}' reference '{reference}' "
                    f"does not match expected pattern ({pattern_desc})"
                )

        return violations

    @staticmethod
    def _matches_pattern(reference: str, pattern: Optional[str]) -> bool:
        """Check if reference matches the regex pattern (full match)."""
        if not pattern:
            return False
        try:
            return bool(re.fullmatch(pattern, reference))
        except re.error:
            return False
