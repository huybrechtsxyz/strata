#!/usr/bin/env python3
"""Built-in policy: restrict SBOM components to approved licenses.

Evaluates at the ``build`` phase.  Checks each component's ``strata:license``
property (populated by collectors or lockfile parsers that capture license
metadata) against an allow-list and/or deny-list of SPDX identifiers.

Configuration
-------------
.. code-block:: yaml

   - name: license-check
     type: sbom_license
     phase: build
     enforcement: warn          # or deny / audit
     configuration:
       allowed:                 # if set, only these licenses are permitted
         - MIT
         - Apache-2.0
         - BSD-3-Clause
       denied:                  # if set, these licenses are always blocked
         - GPL-3.0-only
         - AGPL-3.0-only
       unknown_action: warn     # what to do when license is missing/unknown
                                # allow | warn | deny  (default: warn)

When *both* ``allowed`` and ``denied`` are configured, ``denied`` is checked
first (an explicit deny always wins).

Graceful degradation
--------------------
- No SBOM components in context → pass (skip)
- Neither ``allowed`` nor ``denied`` configured → pass (skip, nothing to enforce)
- Component has no ``strata:license`` property → governed by ``unknown_action``
"""

from fnmatch import fnmatch
from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

_LICENSE_PROPERTY = "strata:license"


class SbomLicensePolicy(BasePolicy):
    """Enforce license allow/deny lists on SBOM components."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        if not context.sbom_components:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no SBOM components available"},
            )

        configuration: Dict[str, Any] = self.policy.configuration or {}
        allowed: List[str] = configuration.get("allowed") or []
        denied: List[str] = configuration.get("denied") or []
        unknown_action: str = configuration.get("unknown_action", "warn")

        if not allowed and not denied:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no allowed or denied licenses configured"},
            )

        violations: List[str] = []
        warnings: List[str] = []

        for component in context.sbom_components:
            license_id = (component.properties or {}).get(_LICENSE_PROPERTY, "").strip()

            if not license_id:
                msg = f"Component '{component.name}' ({component.purl}) has no license metadata"
                if unknown_action == "deny":
                    violations.append(msg)
                elif unknown_action == "warn":
                    warnings.append(msg)
                # "allow" → silently skip
                continue

            # Denied check first — explicit deny always wins
            if denied and any(fnmatch(license_id, pattern) for pattern in denied):
                violations.append(f"Component '{component.name}' ({component.purl}) uses denied license '{license_id}'")
                continue

            # Allowed check — if allow-list is set, license must match
            if allowed and not any(fnmatch(license_id, pattern) for pattern in allowed):
                violations.append(
                    f"Component '{component.name}' ({component.purl}) uses license '{license_id}' not in allowed list"
                )

        details: Dict[str, Any] = {}
        if warnings:
            details["warnings"] = warnings

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            details=details if details else None,
        )
