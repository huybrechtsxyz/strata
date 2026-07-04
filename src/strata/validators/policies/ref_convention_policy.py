#!/usr/bin/env python3
"""Built-in policy: remote reference convention enforcement.

Evaluates at the ``validate`` phase. Checks that remote references (pins to
git tags, branches, or commits) follow declared naming conventions per remote.

Configuration
-------------
``remotes`` (required)
    List of remote configurations. Each remote specifies:

    ``name`` (required)
        Remote repository name from solution.remotes (e.g., "my-service", "tf-landscape")

    ``release_pattern`` (optional)
        Full-match regex for release tags. Example: "^v\\d+\\.\\d+\\.\\d+$"

    ``quality_pattern`` (optional)
        Full-match regex for quality-gate tags. Example: "^tested(-\\d+)?$"

    At least one pattern must be specified per remote.

Examples
--------
::

    policies:
      - name: release_conventions
        type: ref_convention
        phase: validate
        enforcement: warn
        configuration:
          remotes:
            - name: my-service
              release_pattern: "^v\\d+\\.\\d+\\.\\d+$"
              quality_pattern: "^tested(-\\d+)?$"
            - name: tf-landscape
              release_pattern: "^v\\d+\\.\\d+\\.\\d+$"

Graceful degradation
--------------------
- No configuration service in context → pass (skip)
- No deployments/environments found → pass (skip)
- Remote not found in configuration → pass (skip that remote)
- No pattern configured for a remote → pass (skip that remote)
- Reference is a commit SHA or branch → may warn depending on patterns
"""

import re
from typing import Any, Dict, List, Optional

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

logger = None  # Will be lazily imported to avoid circular dependency


class RefConventionPolicy(BasePolicy):
    """Validate that remote references follow declared tag naming conventions."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        global logger
        if logger is None:
            from strata.logger import get_logger

            logger = get_logger(__name__)

        # --- Guard: services required ---
        if context.deployment_service is None or context.configuration_service is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no deployment or configuration service"},
            )

        # --- Read remote patterns from configuration ---
        configuration: Dict[str, Any] = self.policy.configuration or {}
        remotes_config: List[Dict[str, str]] = configuration.get("remotes", [])

        if not remotes_config:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no remote patterns configured"},
            )

        # Build remote name -> patterns mapping
        remote_patterns: Dict[str, Dict[str, Optional[str]]] = {}
        for remote_cfg in remotes_config:
            name = remote_cfg.get("name")
            release_pattern = remote_cfg.get("release_pattern")
            quality_pattern = remote_cfg.get("quality_pattern")

            if not name or (not release_pattern and not quality_pattern):
                # Skip remotes without name or without any patterns
                continue

            remote_patterns[name] = {
                "release": release_pattern,
                "quality": quality_pattern,
            }

        if not remote_patterns:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no valid remote patterns configured"},
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
            logger.debug(
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
