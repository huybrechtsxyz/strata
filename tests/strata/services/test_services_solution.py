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
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.model is not None
    assert service.model.spec.remotes[0].name == "infra"
