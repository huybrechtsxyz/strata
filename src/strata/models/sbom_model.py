#!/usr/bin/env python3
"""SBOM component/reference models (docs/design/sbom-generation.md Phase 1),
plus the CVE scanner models (docs/work/cve-scanner-integration.md).

Ported from v1's `models/sbom_model.py`. `SbomIgnore*RuleModel`
(`sbom-ignore.yaml`) remains a separate, not-yet-designed concern — the CVE
allowlist (`ConfigurationSpecModel.cve_allowed`, `CveAllowedEntryModel`
below) is a dedicated, different mechanism, confirmed never shared with it
in v1.
"""

from datetime import date
from typing import Literal

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import PlatformBaseModel

#: Shared with `strata.integrations.cve_scanner` and
#: `strata.controllers.sbom_controller` — one source of truth for severity
#: ordering (CRITICAL is most severe) rather than three independent copies.
CVE_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


class SbomComponentModel(PlatformBaseModel):
    """Internal representation of a single SBOM component.

    The currency between a collector (`strata.integrations.sbom_collectors`)
    and `sbom_controller.write_sbom()` — collectors produce these, the
    controller converts them to CycloneDX objects. No `cyclonedx-python-lib`
    import here — collectors stay dependency-free of the serialisation
    library (matches v1's own isolation convention).
    """

    component_type: str = Field(description="CycloneDX component type: container | library | framework")
    name: str = Field(description="Component name (service name, chart name, provider name, …)")
    version: str | None = Field(None, description="Version string or constraint (e.g. 'v3.0.1', '~>5.0')")
    purl: str = Field(description="Package URL string (pkg:docker/…, pkg:helm/…, pkg:terraform/…)")
    properties: dict[str, str] = Field(
        default_factory=dict,
        description="Component properties keyed by name (e.g. {'strata:tag-stability': 'floating'})",
    )
    source_collector: str = Field(
        description="Short name of the collector that produced this component (image | compose | helm | terraform)"
    )


class SbomReferenceModel(PlatformBaseModel):
    """Reference to a generated SBOM file.

    Written by `sbom_controller.write_sbom()` after successfully producing
    `sbom.json`. Not yet embedded anywhere (no v2 deployment manifest exists
    — docs/design/gap_fit_v1.md gap #7); kept so that feature can read the
    hash straight off the already-written file once it is designed.
    """

    path: str = Field(description="Workspace-relative path to sbom.json")
    format: str = Field(description="SBOM format and schema version (e.g. 'cyclonedx-1.6')")
    sha256: str = Field(description="SHA-256 hash of the sbom.json file (prefixed 'sha256:')")
    component_count: int = Field(description="Number of components listed in the SBOM")


class CveFindingModel(PlatformBaseModel):
    """A single CVE finding from vulnerability scanning.

    Field-for-field port of v1's real `CveFindingModel`
    (docs/work/cve-scanner-integration.md) — populated identically from
    either backend's own JSON output (`strata.integrations.cve_scanner`).
    """

    vulnerability_id: str = Field(description="CVE identifier (e.g. 'CVE-2024-1234')")
    severity: str = Field(description="Severity level: CRITICAL | HIGH | MEDIUM | LOW | UNKNOWN")
    package_name: str = Field(description="Affected package name")
    installed_version: str = Field(description="Installed version of the affected package")
    fixed_version: str | None = Field(None, description="Version that fixes the vulnerability, if available")
    title: str | None = Field(None, description="Short description of the vulnerability")
    purl: str | None = Field(None, description="Package URL of the affected component")


class CveAuditResultModel(PlatformBaseModel):
    """Summary of a CVE vulnerability scan — pre-aggregated severity counts,
    not re-derived from `findings` on every read (matches v1's own shape;
    `strata.controllers.sbom_controller`'s allowlist filtering recomputes
    these fields after suppression, rather than decrementing them)."""

    scanner: str = Field(description="Scanner backend used: 'trivy' | 'grype'")
    scanner_version: str = Field(description="Version of the scanner")
    sbom_path: str = Field(description="Path to the SBOM file that was scanned")
    total_findings: int = Field(description="Total number of vulnerabilities found")
    critical: int = Field(default=0, description="Count of CRITICAL severity findings")
    high: int = Field(default=0, description="Count of HIGH severity findings")
    medium: int = Field(default=0, description="Count of MEDIUM severity findings")
    low: int = Field(default=0, description="Count of LOW severity findings")
    unknown: int = Field(default=0, description="Count of UNKNOWN severity findings")
    findings: list[CveFindingModel] = Field(default_factory=list, description="Individual vulnerability findings")


class CveAllowedEntryModel(PlatformBaseModel):
    """A single entry in the CVE allowlist
    (`ConfigurationSpecModel.cve_allowed`, docs/work/
    cve-scanner-integration.md Phase 3).

    Ported field-for-field from v1's real `.strata/cve-allowed.yaml` shape
    — placement differs (a solution-wide `Configuration` document field,
    not a `.strata/` file: that directory is runtime-only state in v2,
    never document-discovered, and this is a reviewed governance decision,
    not transient state), the entry shape itself does not.
    """

    id: str = Field(description="CVE identifier to suppress, e.g. 'CVE-2024-1234'")
    reason: str = Field(description="Justification for accepting this CVE")
    package: str | None = Field(None, description="Scope to a specific package name (optional)")
    expires: str | None = Field(None, description="ISO date after which this entry is ignored (e.g. '2026-12-31')")

    @field_validator("expires")
    @classmethod
    def validate_expires_is_iso_date(cls, value: str | None) -> str | None:
        """Fail at document-load time, not at evaluation time — v1 left this
        as a bare, unvalidated string; a malformed date should not silently
        behave as 'never expires' (or, worse, 'already expired') the first
        time `evaluate_cve_policy()` tries to parse it.
        """
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"expires '{value}' is not a valid ISO date (e.g. '2026-12-31'): {exc}") from exc
        return value


class CveMaxSeverityPolicyModel(PlatformBaseModel):
    """`WorkspaceSpecModel.cve_policy` — gates `build run` on CVE findings in
    the SBOM it just generated (docs/work/cve-scanner-integration.md).

    A small, dedicated field rather than a generic `policies:` list (v2 has
    no policy engine at all yet, docs/work/policy-engine-architecture.md's
    own Open Question 1) — matches the `tenant_zone` static tier's own
    "model the one real check directly" precedent, just with real
    configurable knobs this policy genuinely needs and that check didn't.
    """

    max_severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = Field(
        description="Fail when findings at or above this severity exceed max_count"
    )
    max_count: int = Field(default=0, ge=0, description="Fail when the breaching count exceeds this (default 0)")
    severity_threshold: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"] | None = Field(
        default=None,
        description="Minimum severity sent to the scanner itself. Defaults to max_severity when unset — lowering "
        "it (e.g. to include UNKNOWN-severity noise in the raw result) is fine, but it can never be set "
        "*more* restrictive than max_severity: the scanner would then filter out the very findings the "
        "policy needs to see, silently under-counting a breach (enforced below).",
    )
    enforcement: Literal["deny", "warn"] = Field(
        default="deny", description="deny fails the build; warn reports and continues"
    )
    on_missing_data: Literal["skip", "warn", "block"] = Field(
        default="block",
        description="What to do when no SBOM/scanner is available to produce a result. Defaults to 'block' "
        "(not the generic 'skip') — v1's own docstring explicitly warns that 'skip' makes 'never scanned' "
        "indistinguishable from 'scanned, found nothing', a real risk for a security-flavored guardrail.",
    )

    @model_validator(mode="after")
    def validate_severity_threshold_covers_max_severity(self) -> "CveMaxSeverityPolicyModel":
        """`severity_threshold` must be at least as inclusive as `max_severity` —
        otherwise the scanner itself would filter out findings the policy
        needs to see before they ever reach evaluation (e.g. `max_severity:
        LOW` with `severity_threshold: HIGH` would never surface a LOW
        finding at all, silently under-counting every breach).
        """
        if self.severity_threshold is None:
            return self
        if CVE_SEVERITY_ORDER.index(self.severity_threshold) < CVE_SEVERITY_ORDER.index(self.max_severity):
            raise ValueError(
                f"severity_threshold '{self.severity_threshold}' is more restrictive than max_severity "
                f"'{self.max_severity}' — the scanner would filter out findings the policy needs to see. "
                "severity_threshold must be max_severity or lower (more inclusive)."
            )
        return self
