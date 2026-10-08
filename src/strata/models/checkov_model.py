#!/usr/bin/env python3
"""Checkov static-IaC-scanner models (docs/archive/checkov-integration.md).

A dedicated module, not folded into `sbom_model.py` the way CVE's models
were: Checkov never touches an SBOM, it scans rendered IaC output
directly, so the SBOM-specific grouping that justified CVE's placement
doesn't apply here. `CHECKOV_SEVERITY_ORDER` is its own constant (not a
reuse of `sbom_model.CVE_SEVERITY_ORDER`) even though the values coincide
today — two different domains' severity vocabularies that happen to
match should not be coupled through a shared import, in case one of them
diverges later (e.g. Checkov adding a severity CVE scanning never has).
"""

from typing import Literal

from pydantic import Field

from strata.models.common_models import PlatformBaseModel

#: Shared with `strata.integrations.checkov` and
#: `strata.controllers.checkov_controller` — one source of truth for
#: severity ordering (CRITICAL is most severe), matching
#: `sbom_model.CVE_SEVERITY_ORDER`'s own precedent for this scanner's
#: own, separate vocabulary.
CHECKOV_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


class CheckovFindingModel(PlatformBaseModel):
    """A single failed check from a Checkov scan.

    Field-for-field port of v1's real `CheckovFinding` dataclass
    (docs/archive/checkov-integration.md) — populated directly from
    Checkov's own `--output json` `results.failed_checks` entries
    (`strata.integrations.checkov`).
    """

    check_id: str = Field(description="Checkov check ID (e.g. 'CKV_AWS_20')")
    check_name: str = Field(description="Human-readable check name")
    resource: str = Field(description="Resource address the check failed against")
    file_path: str = Field(description="Path to the file containing the violating resource")
    file_line_range: list[int] | None = Field(
        None, description="[start_line, end_line] of the violating resource block, when available"
    )
    severity: str = Field(description="Severity level: CRITICAL | HIGH | MEDIUM | LOW | UNKNOWN")
    guideline: str | None = Field(None, description="Link or text explaining how to fix the finding, when available")


class CheckovScanResultModel(PlatformBaseModel):
    """Summary of a single Checkov scan — pre-aggregated pass/fail/skip
    counts (matches v1's own shape and `CveAuditResultModel`'s own
    "don't re-derive counts from findings" precedent), plus the raw
    findings themselves.
    """

    passed: int = Field(default=0, description="Count of checks that passed")
    failed: int = Field(default=0, description="Count of checks that failed")
    skipped: int = Field(default=0, description="Count of checks that were skipped")
    findings: list[CheckovFindingModel] = Field(default_factory=list, description="Individual failed checks")
    scanner_version: str = Field(description="Version of the checkov CLI that produced this result")
    framework: str = Field(description="Framework scanned: terraform | bicep | ansible | helm")
    scanned_path: str = Field(description="Path to the directory that was scanned")

    def findings_at_or_above(self, severity: str) -> list[CheckovFindingModel]:
        """Findings at `severity` or more severe (CRITICAL is most severe —
        `CHECKOV_SEVERITY_ORDER`). Ported as a real method from v1's own
        `CheckovScanResult` shape, so a future evaluator's severity-gate
        check (matching `sbom_controller.evaluate_cve_policy()`'s own
        `CVE_SEVERITY_ORDER.index()` comparison) doesn't re-derive this ad
        hoc at each call site.
        """
        threshold = CHECKOV_SEVERITY_ORDER.index(severity)
        return [
            finding
            for finding in self.findings
            if CHECKOV_SEVERITY_ORDER.index(
                finding.severity if finding.severity in CHECKOV_SEVERITY_ORDER else "UNKNOWN"
            )
            <= threshold
        ]


class CheckovPolicyModel(PlatformBaseModel):
    """Per-provisioner Checkov policy configuration (docs/work/
    checkov-integration.md's "v2 design decision" section).

    Field names/defaults/casing (`framework`, `severity_gate`, `scope`,
    lowercase severity values) are a direct, field-for-field port of v1's
    real `policies.checkov.configuration` YAML shape, plus v1's own
    top-level `enforcement` field (shown alongside `configuration:` in
    v1's real YAML, not nested inside it — folded directly onto this one
    model instead, since v2 has no generic policy wrapper, matching
    `CveMaxSeverityPolicyModel.enforcement`'s own placement).
    `on_missing_data` is the one genuinely new field v1 never had, added
    here to match `CveMaxSeverityPolicyModel.on_missing_data`'s own
    vocabulary (`skip | warn | block`), per the v2 design decision
    resolving this policy's own Open Question.
    """

    framework: Literal["terraform", "bicep", "ansible", "helm"] = Field(
        default="terraform", description="IaC framework to scan"
    )
    severity_gate: Literal["critical", "high", "medium", "low"] = Field(
        default="high", description="Fail when any finding at or above this severity is present"
    )
    scope: str = Field(
        default="staged",
        description="For terraform/bicep/ansible: 'staged' (default, provisioners with their own execution "
        "step) | 'all' (every declared provisioner of this framework) | the name of one specific "
        "execution step. For helm: 'staged' and 'all' are equivalent (every namespace this workspace "
        "references) | the name of one specific namespace (docs/archive/checkov-integration.md Phase 3).",
    )
    skip_checks: list[str] | None = Field(
        None, description="Check IDs to suppress (mutually exclusive with include_checks)"
    )
    include_checks: list[str] | None = Field(None, description="When set, run ONLY these check IDs")
    custom_checks_dir: str | None = Field(None, description="Extra custom-checks directory, if any")
    timeout: int = Field(default=120, ge=1, description="Scan subprocess timeout in seconds (default 120)")
    enforcement: Literal["deny", "warn"] = Field(
        default="deny", description="deny fails the build; warn reports and continues"
    )
    on_missing_data: Literal["skip", "warn", "block"] = Field(
        default="block",
        description="What to do when the provisioner this policy targets has no materialised source to scan. "
        "Defaults to 'block' (not the generic 'skip'), matching CveMaxSeverityPolicyModel's own rationale: "
        "'skip' makes 'never scanned' indistinguishable from 'scanned, found nothing'.",
    )
