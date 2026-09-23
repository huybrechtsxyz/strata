#!/usr/bin/env python3
"""Tests for SolutionService loading and validation."""

from strata.services.solution_service import SolutionService


def test_solution_service_validates_from_data():
    """A SolutionService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "integration"},
        "spec": {
            "configuration": "config",
            "remotes": [
                {"name": "infra", "type": "git", "url": "https://host/infra.git", "reference": "v2.1.0"}
            ],
        },
    }
    service = SolutionService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.model is not None
    assert service.model.spec.remotes[0].name == "infra"
