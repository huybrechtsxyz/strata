"""Pydantic models for SBOM references and internal component representation."""

from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from strata.models.common_models import PlatformBaseModel


class SbomComponentModel(PlatformBaseModel):
    """Internal representation of a single SBOM component.

    This model is the currency between collectors and ``SbomBuilder``.
    Collectors produce ``SbomComponentModel`` instances; ``SbomBuilder``
    converts them to CycloneDX objects.  No ``cyclonedx-python-lib`` imports
    here — collectors stay dependency-free.
    """

    component_type: str = Field(description="CycloneDX component type: container | library | framework")
    name: str = Field(description="Component name (service name, chart name, provider name, …)")
    version: Optional[str] = Field(
        None,
        description="Version string or constraint (e.g. 'v3.0.1', '~>5.0', '2024.12.0')",
    )
    purl: str = Field(description="Package URL string (pkg:docker/…, pkg:helm/…, pkg:terraform/…, pkg:ansible/…)")
    properties: Dict[str, str] = Field(
        default_factory=dict,
        description="Component properties keyed by name (e.g. {'strata:tag-stability': 'floating'})",
    )
    source_collector: str = Field(
        description="Short name of the collector that produced this component (image | helm | terraform | ansible)"
    )


class SbomReferenceModel(PlatformBaseModel):
    """Reference to a generated SBOM file — stored in the deployment manifest spec.

    Written by ``SbomBuilder`` after successfully producing ``sbom.json``.
    The path is workspace-relative so that the manifest is portable across
    machines.
    """

    path: str = Field(description="Workspace-relative path to sbom.json")
    format: str = Field(description="SBOM format and schema version (e.g. 'cyclonedx-1.6')")
    sha256: str = Field(description="SHA-256 hash of the sbom.json file (prefixed 'sha256:')")
    component_count: int = Field(description="Number of components listed in the SBOM")


class CveFindingModel(PlatformBaseModel):
    """A single CVE finding from vulnerability scanning."""

    vulnerability_id: str = Field(description="CVE identifier (e.g. 'CVE-2024-1234')")
    severity: str = Field(description="Severity level: CRITICAL | HIGH | MEDIUM | LOW | UNKNOWN")
    package_name: str = Field(description="Affected package name")
    installed_version: str = Field(description="Installed version of the affected package")
    fixed_version: Optional[str] = Field(None, description="Version that fixes the vulnerability, if available")
    title: Optional[str] = Field(None, description="Short description of the vulnerability")
    purl: Optional[str] = Field(None, description="Package URL of the affected component")


class CveAllowedEntryModel(PlatformBaseModel):
    """A single entry in the CVE allowlist (.strata/cve-allowed.yaml)."""

    id: str = Field(description="CVE identifier to suppress (e.g. 'CVE-2024-1234')")
    reason: str = Field(description="Justification for allowing this CVE")
    package: Optional[str] = Field(None, description="Scope to a specific package name (optional)")
    expires: Optional[str] = Field(None, description="ISO date after which this entry is ignored (e.g. '2026-12-31')")


class CveAuditResultModel(PlatformBaseModel):
    """Summary of CVE vulnerability scan results."""

    scanner: str = Field(description="Scanner backend used: 'trivy' | 'grype'")
    scanner_version: str = Field(description="Version of the scanner")
    sbom_path: str = Field(description="Path to the SBOM file that was scanned")
    total_findings: int = Field(description="Total number of vulnerabilities found")
    critical: int = Field(default=0, description="Count of CRITICAL severity findings")
    high: int = Field(default=0, description="Count of HIGH severity findings")
    medium: int = Field(default=0, description="Count of MEDIUM severity findings")
    low: int = Field(default=0, description="Count of LOW severity findings")
    unknown: int = Field(default=0, description="Count of UNKNOWN severity findings")
    findings: List[CveFindingModel] = Field(default_factory=list, description="Individual vulnerability findings")


# ---------------------------------------------------------------------------
# sbom-ignore.yaml models
# ---------------------------------------------------------------------------


class SbomIgnorePathRuleModel(PlatformBaseModel):
    """A single path glob ignore rule with justification."""

    pattern: str = Field(
        description="Glob pattern matched against the file path relative to the scan root (e.g. 'docs/**')"
    )
    justification: Optional[str] = Field(
        None, description="Reason this path is excluded from SBOM scanning (audit trail)"
    )


class SbomIgnoreFileRuleModel(PlatformBaseModel):
    """A single filename ignore rule — exact match or Python regex."""

    pattern: str = Field(
        description=(
            "Filename to exclude.  Exact match by default; set is_regex=true for a "
            "Python regex (e.g. '^.+-dev\\\\.txt$')"
        )
    )
    is_regex: bool = Field(default=False, description="Treat pattern as a Python regular expression when True")
    justification: Optional[str] = Field(
        None, description="Reason this file is excluded from SBOM scanning (audit trail)"
    )


class SbomIgnorePackageRuleModel(PlatformBaseModel):
    """A single package-name glob ignore rule."""

    pattern: str = Field(description="Glob pattern matched against the package name (e.g. 'pytest*', 'dev-*')")
    justification: Optional[str] = Field(
        None, description="Reason this package is excluded from the SBOM (audit trail)"
    )


class SbomIgnoreTypeRuleModel(PlatformBaseModel):
    """Ignore dependencies by semantic type."""

    type: str = Field(description="Dependency type to exclude: dev | optional | peer | test | build")
    justification: Optional[str] = Field(
        None, description="Reason this dependency type is excluded from the SBOM (audit trail)"
    )


class SbomIgnoreConfigModel(PlatformBaseModel):
    """Schema for ``.strata/sbom-ignore.yaml``.

    Controls which dependency files, paths, packages, and dependency types are
    excluded from the SBOM produced by ``DependencyFileCollector``.  Rules are
    **additive** — they extend the built-in default ignores rather than replace them.

    Each list item accepts either a bare string (backward-compatible shorthand
    for ``{pattern: "..."}`` / ``{type: "..."}``) or a full object with a
    ``justification`` field for audit traceability.
    """

    ignore_paths: List[SbomIgnorePathRuleModel] = Field(
        default_factory=list,
        description="Glob patterns matched against file paths relative to each scan root",
    )
    ignore_files: List[SbomIgnoreFileRuleModel] = Field(
        default_factory=list,
        description="Filenames (exact or regex) to exclude regardless of location",
    )
    ignore_packages: List[SbomIgnorePackageRuleModel] = Field(
        default_factory=list,
        description="Package name glob patterns to exclude after parsing",
    )
    ignore_dependency_types: List[SbomIgnoreTypeRuleModel] = Field(
        default_factory=list,
        description="Dependency types to exclude (requires parsers that populate dep_type)",
    )

    @field_validator("ignore_paths", mode="before")
    @classmethod
    def _coerce_paths(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [{"pattern": item} if isinstance(item, str) else item for item in v]
        return v

    @field_validator("ignore_files", mode="before")
    @classmethod
    def _coerce_files(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [{"pattern": item} if isinstance(item, str) else item for item in v]
        return v

    @field_validator("ignore_packages", mode="before")
    @classmethod
    def _coerce_packages(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [{"pattern": item} if isinstance(item, str) else item for item in v]
        return v

    @field_validator("ignore_dependency_types", mode="before")
    @classmethod
    def _coerce_types(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [{"type": item} if isinstance(item, str) else item for item in v]
        return v
