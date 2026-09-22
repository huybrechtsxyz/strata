#!/usr/bin/env python3
"""Tests for SolutionModel (`strata.yaml`) validation.

The solution manifest is the bootstrap document: solution identity, where
Configuration lives, and the named remotes everything else resolves
`@<name>/...` references against.
"""

import pytest
from pydantic import ValidationError

from strata.models.solution_model import SolutionModel


def _minimal_solution() -> dict:
    return {
        "meta": {"name": "integration"},
        "spec": {},
    }


def test_solution_minimal_is_valid():
    """A solution with no remotes validates, and spec.configuration defaults to 'config'."""
    model = SolutionModel.model_validate(_minimal_solution())
    assert model.meta.name == "integration"
    assert model.spec.configuration == "config"
    assert model.spec.remotes is None
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "solution"


def test_solution_rejects_mismatched_kind():
    """A document declaring another kind is rejected (see validate_kind_matches)."""
    data = _minimal_solution()
    data["kind"] = "configuration"
    with pytest.raises(ValidationError, match="Expected kind 'solution'"):
        SolutionModel.model_validate(data)


def test_solution_accepts_configuration_file_pointer():
    """spec.configuration may point at a single file rather than a directory."""
    data = _minimal_solution()
    data["spec"]["configuration"] = "config/configuration.yaml"
    model = SolutionModel.model_validate(data)
    assert model.spec.configuration == "config/configuration.yaml"


def test_solution_rejects_configuration_outside_solution():
    """spec.configuration must stay inside the solution."""
    data = _minimal_solution()
    data["spec"]["configuration"] = "../elsewhere/config"
    with pytest.raises(ValidationError):
        SolutionModel.model_validate(data)


def test_solution_accepts_git_remote():
    """A git remote with a pinned reference and an Integration credential ref validates."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [
        {
            "name": "infra",
            "type": "git",
            "url": "https://github.com/org/infra.git",
            "reference": "v2.1.0",
            "integration": "corp-git",
        }
    ]
    model = SolutionModel.model_validate(data)
    assert model.spec.remotes[0].name == "infra"
    assert model.spec.remotes[0].type.value == "git"
    assert model.spec.remotes[0].reference == "v2.1.0"
    assert model.spec.remotes[0].integration == "corp-git"


def test_solution_remote_defaults_to_strata_fetch():
    """fetch defaults to 'strata' — strata materialises the remote itself."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [
        {"name": "infra", "type": "git", "url": "https://host/infra.git", "reference": "main"}
    ]
    model = SolutionModel.model_validate(data)
    assert model.spec.remotes[0].fetch.value == "strata"


def test_solution_remote_accepts_external_fetch():
    """A git remote already checked out by CI keeps its type and ref, with fetch: external."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [
        {
            "name": "env-int",
            "type": "git",
            "url": "https://host/env-int.git",
            "reference": "main",
            "fetch": "external",
        }
    ]
    model = SolutionModel.model_validate(data)
    assert model.spec.remotes[0].type.value == "git"
    assert model.spec.remotes[0].fetch.value == "external"


def test_solution_git_remote_requires_reference():
    """A git remote without a pinned reference is rejected."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [{"name": "infra", "type": "git", "url": "https://host/infra.git"}]
    with pytest.raises(ValidationError, match="'reference' is required"):
        SolutionModel.model_validate(data)


def test_solution_helm_remote_rejects_reference():
    """A helm remote serves many chart versions — a remote-level reference is rejected."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [
        {"name": "goauthentik", "type": "helm", "url": "https://charts.goauthentik.io", "reference": "1.0.0"}
    ]
    with pytest.raises(ValidationError, match="'reference' is not valid"):
        SolutionModel.model_validate(data)


def test_solution_accepts_helm_remote_without_reference():
    """A helm remote without a reference is the valid shape."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [{"name": "goauthentik", "type": "helm", "url": "https://charts.goauthentik.io"}]
    model = SolutionModel.model_validate(data)
    assert model.spec.remotes[0].reference is None


def test_solution_local_remote_rejects_absolute_path():
    """A local remote's url must be a solution-relative path, not absolute."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [{"name": "bundled", "type": "local", "url": "/etc/strata/modules"}]
    with pytest.raises(ValidationError):
        SolutionModel.model_validate(data)


def test_solution_local_remote_rejects_traversal():
    """A local remote's url must not escape the solution via '..'."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [{"name": "bundled", "type": "local", "url": "../../etc/passwd"}]
    with pytest.raises(ValidationError):
        SolutionModel.model_validate(data)


def test_solution_local_remote_rejects_integration():
    """On-disk local remotes need no credentials."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [
        {"name": "bundled", "type": "local", "url": "modules", "integration": "corp-git"}
    ]
    with pytest.raises(ValidationError, match="'integration' is not valid"):
        SolutionModel.model_validate(data)


def test_solution_local_remote_rejects_external_fetch():
    """There is nothing to fetch for a local remote."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [{"name": "bundled", "type": "local", "url": "modules", "fetch": "external"}]
    with pytest.raises(ValidationError, match="'fetch' is not valid"):
        SolutionModel.model_validate(data)


def test_solution_rejects_duplicate_remote_names():
    """Remote names are the '@<name>' resolution key — duplicates are rejected."""
    data = _minimal_solution()
    data["spec"]["remotes"] = [
        {"name": "infra", "type": "git", "url": "https://host/a.git", "reference": "main"},
        {"name": "infra", "type": "git", "url": "https://host/b.git", "reference": "main"},
    ]
    with pytest.raises(ValidationError, match="Duplicate"):
        SolutionModel.model_validate(data)
