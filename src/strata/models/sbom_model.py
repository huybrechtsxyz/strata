"""Pydantic models for SBOM references and internal component representation."""

from typing import Dict, List, Optional

from pydantic import Field

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
