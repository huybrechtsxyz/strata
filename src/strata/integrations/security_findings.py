#!/usr/bin/env python3
"""Vendor-neutral types for the security gate capabilities (docs/work/security-gates-design.md D1).

`release_gate` and `image_findings` integrations return these, so the gate controller never sees a
vendor's wire format. Frozen dataclasses, not `PlatformBaseModel`: they are runtime results, never
YAML, and must not reject fields a vendor adds later.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

#: Same values and order as `CVE_SEVERITY_ORDER`/`CHECKOV_SEVERITY_ORDER`, kept separate as
#: `checkov_model` does. Anything a vendor cannot map is UNKNOWN, never counted against a threshold.
SECURITY_SEVERITY_ORDER: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


@dataclass(frozen=True)
class ReleaseGateRequest:
    """What a release gate is asked to judge. Vendor thresholds live on the Integration document (D4)."""

    repository: str
    commit: str
    timeout: int
    poll_interval: int


@dataclass(frozen=True)
class ReleaseGateOutcome:
    """A platform's verdict on one repository at one commit."""

    passed: bool
    reference: str | None = None
    open_issues: int | None = None
    breakdown: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    links: tuple[str, ...] = ()
    detail: str | None = None


@dataclass(frozen=True)
class ImageRef:
    """One container image from the SBOM, split the way `parse_image_ref()` splits a reference.

    A digest-pinned image carries no tag: `image_to_purl()` keeps only the digest.
    """

    service: str
    registry: str | None
    repository: str
    tag: str | None = None
    digest: str | None = None

    @property
    def path(self) -> str:
        """Registry plus repository, without tag or digest."""
        return f"{self.registry}/{self.repository}" if self.registry else self.repository


@dataclass(frozen=True)
class SecurityIssue:
    """One open issue a platform reports for an image."""

    id: str
    severity: str
    status: str
    cve_id: str | None = None
    package: str | None = None
    link: str | None = None


@dataclass(frozen=True)
class ImageLookup:
    """What a platform knows about one image. Unknown or ambiguous is a result, not an error."""

    status: Literal["found", "unknown", "ambiguous"]
    platform_name: str | None = None
    issues: tuple[SecurityIssue, ...] = ()
    detail: str | None = None
