#!/usr/bin/env python3
"""Tests for the vendor-neutral security gate types (docs/design/security-gates.md D1)."""

from dataclasses import FrozenInstanceError

import pytest

from strata.integrations.errors import GateNotFoundError, GateTimeoutError, IntegrationError
from strata.integrations.security_findings import (
    SECURITY_SEVERITY_ORDER,
    ImageLookup,
    ImageRef,
    ReleaseGateOutcome,
    ReleaseGateRequest,
    SecurityIssue,
)
from strata.models.sbom_model import CVE_SEVERITY_ORDER


def test_severity_order_matches_the_cve_gate():
    """One vocabulary across gates keeps thresholds and a later cve_policy merge simple."""
    assert SECURITY_SEVERITY_ORDER == tuple(CVE_SEVERITY_ORDER)


def test_image_ref_path_includes_the_registry_when_there_is_one():
    image = ImageRef(service="api", registry="ghcr.io", repository="huybrechtsxyz/api", tag="1.4.2")
    assert image.path == "ghcr.io/huybrechtsxyz/api"


def test_image_ref_path_is_the_repository_on_docker_hub():
    image = ImageRef(service="proxy", registry=None, repository="traefik", tag="v3.0.1")
    assert image.path == "traefik"


def test_digest_pinned_image_carries_no_tag():
    image = ImageRef(service="db", registry=None, repository="postgres", digest="sha256:abc123")
    assert image.tag is None
    assert image.digest == "sha256:abc123"


@pytest.mark.parametrize(
    "instance",
    [
        ReleaseGateRequest(repository="org/repo", commit="a" * 40, timeout=900, poll_interval=10),
        ReleaseGateOutcome(passed=True),
        ImageRef(service="api", registry=None, repository="api"),
        SecurityIssue(id="1", severity="HIGH", status="open"),
        ImageLookup(status="unknown"),
    ],
)
def test_types_are_frozen(instance):
    with pytest.raises(FrozenInstanceError):
        instance.detail = "changed"  # type: ignore[misc]


def test_outcome_defaults_are_empty_not_shared():
    first, second = ReleaseGateOutcome(passed=False), ReleaseGateOutcome(passed=False)
    assert first.breakdown == {} and first.links == ()
    assert first.breakdown is not second.breakdown


def test_gate_errors_are_integration_errors():
    assert issubclass(GateNotFoundError, IntegrationError)
    assert issubclass(GateTimeoutError, IntegrationError)


def test_gate_not_found_error_keeps_the_platform_reason():
    error = GateNotFoundError("unknown repository", reason="Repository not found or commit does not exist")
    assert str(error) == "unknown repository"
    assert error.reason == "Repository not found or commit does not exist"
    assert GateNotFoundError("unknown repository").reason is None
