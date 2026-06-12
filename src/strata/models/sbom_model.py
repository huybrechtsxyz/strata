"""Pydantic models for SBOM references and internal component representation."""

from typing import Dict, Optional

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
