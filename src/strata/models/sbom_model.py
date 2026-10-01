#!/usr/bin/env python3
"""SBOM component/reference models (docs/design/sbom-generation.md Phase 1).

Ported from v1's `models/sbom_model.py`, fields actually used by this
phase's collectors only — not the CVE/ignore-rule models (`CveFindingModel`,
`CveAuditResultModel`, `SbomIgnore*RuleModel`), which belong to a separate,
not-yet-designed concern (CVE scanning, `sbom-ignore.yaml`).
"""

from pydantic import Field

from strata.models.common_models import PlatformBaseModel


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
