#!/usr/bin/env python3
"""Tests for `ArtifactService.validate_registry()` (docs/work/artifact-gap-analysis.md, design 5): an artifact's
`registry` agrees with the registry its `image_name` is pulled from."""

import pytest

from strata.models.artifact_model import ArtifactModel
from strata.services.artifact_service import ArtifactService


def _artifact(image_name: str, registry: str | None = None) -> ArtifactService:
    spec: dict = {"image_name": image_name}
    if registry is not None:
        spec["registry"] = registry
    return ArtifactService.from_model(ArtifactModel.model_validate({"meta": {"name": "api"}, "spec": spec}))


@pytest.mark.parametrize(
    ("image_name", "registry"),
    [
        ("registry.example.com/example/api", "registry.example.com"),
        ("Registry.Example.com/example/api", "https://registry.example.com/"),
        ("acr.example.io/team/api", "acr.example.io/team"),
        ("localhost:5000/api", "localhost:5000"),
        ("registry:5000/img", "registry:5000"),
        ("library/postgres", "docker.io"),
        ("redis", "index.docker.io"),
        ("docker.io/library/redis", "registry-1.docker.io"),
    ],
)
def test_a_registry_that_matches_the_image_name_passes(image_name, registry):
    assert _artifact(image_name, registry).validate_registry().ok
    assert _artifact(image_name, registry).validate_registry().warnings == []


def test_no_registry_means_nothing_to_check():
    assert _artifact("example/api").validate_registry().warnings == []
    assert _artifact("example/api", "  ").validate_registry().warnings == []


def test_an_image_name_without_a_host_is_pulled_from_docker_hub():
    result = _artifact("example/api", "registry.example.com").validate_registry()

    assert [(d.code, d.location) for d in result.warnings] == [("artifact_registry_mismatch", "spec.registry")]
    message = result.messages()[0]
    assert "'docker.io'" in message
    assert "Docker Hub" in message
    assert "image_name: registry.example.com/example/api" in message


def test_a_different_host_is_a_mismatch_with_the_repository_kept():
    result = _artifact("ghcr.io/example/api", "registry.example.com:5000").validate_registry()

    assert [d.code for d in result.warnings] == ["artifact_registry_mismatch"]
    message = result.messages()[0]
    assert "'ghcr.io'" in message
    assert "Docker Hub" not in message
    assert "image_name: registry.example.com:5000/example/api" in message


def test_a_mismatch_is_a_warning_not_an_error():
    assert _artifact("example/api", "registry.example.com").validate_registry().ok
