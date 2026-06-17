#!/usr/bin/env python3
"""Built-in policy: restrict container images to approved registries.

Evaluates at the ``build`` phase.  Checks that container image components
originate from an explicitly allowed registry prefix.

Graceful degradation
--------------------
- No SBOM components in context → pass (skip)
- Empty ``allowed`` list in configuration → pass (skip, nothing to enforce)
"""

from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

# Default collectors that produce container image components.
_IMAGE_COLLECTORS = ("image", "compose")


def _extract_registry(purl: str) -> str:
    """Extract the registry/namespace prefix from a docker or OCI purl.

    Package URLs for containers look like:
        pkg:docker/library/nginx@1.25
        pkg:docker/ghcr.io/myorg/app@v1.2
        pkg:oci/mcr.microsoft.com/dotnet/sdk@8.0

    Returns the registry portion (everything between the type qualifier and
    the image name/version).
    """
    # Strip scheme: pkg:docker/ or pkg:oci/
    for prefix in ("pkg:docker/", "pkg:oci/"):
        if purl.startswith(prefix):
            remainder = purl[len(prefix) :]
            # Remove version/qualifiers
            remainder = remainder.split("@")[0].split("?")[0]
            # The registry is everything up to (but not including) the last path segment
            parts = remainder.split("/")
            if len(parts) >= 2:
                return "/".join(parts[:-1])
            # Single-segment names (e.g. "nginx") → default registry
            return "docker.io/library"
    return ""


class SbomAllowedRegistriesPolicy(BasePolicy):
    """Deny builds where container images originate from unapproved registries."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        # --- Guard: sbom components required ---
        if not context.sbom_components:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no SBOM components available"},
            )

        # --- Read configuration ---
        configuration: Dict[str, Any] = self.policy.configuration or {}
        allowed: List[str] = configuration.get("allowed") or []
        collectors: List[str] = configuration.get("collectors") or list(_IMAGE_COLLECTORS)

        if not allowed:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no allowed registries configured"},
            )

        # --- Check container components ---
        violations: List[str] = []
        for component in context.sbom_components:
            if component.source_collector not in collectors:
                continue

            # Only check docker/oci purls
            if not (component.purl.startswith("pkg:docker/") or component.purl.startswith("pkg:oci/")):
                continue

            registry = _extract_registry(component.purl)
            if not any(registry.startswith(prefix) or registry == prefix for prefix in allowed):
                violations.append(
                    f"Image '{component.name}' uses unapproved registry '{registry}' (allowed: {', '.join(allowed)})"
                )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )
