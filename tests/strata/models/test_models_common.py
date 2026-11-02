#!/usr/bin/env python3
"""Tests for shared script-path validation in common_models."""

import pytest
from pydantic import ValidationError

from strata.models.common_models import ScriptPathModel, ScriptsModel, SourceModel

#: Paths that escape the solution. Scripts are executed, so these must be
#: refused exactly as they are for every other declared path.
ESCAPING_PATHS = [
    "/etc/cron.d/evil.sh",
    "../../../../tmp/pwn.sh",
    "C:/Windows/Temp/x.ps1",
    "scripts/../../outside.sh",
]


def test_valid_script_path_is_accepted():
    """A solution-relative script with a runnable extension is fine."""
    model = ScriptPathModel(file="scripts/deploy.sh", scope="module")
    assert model.file == "scripts/deploy.sh"
    assert model.priority == 100


@pytest.mark.parametrize("path", ESCAPING_PATHS)
def test_script_path_rejects_paths_escaping_the_solution(path):
    """An absolute or traversing script path would execute code from outside."""
    with pytest.raises(ValidationError):
        ScriptPathModel(file=path, scope="module")


def test_script_path_rejects_non_script_extension():
    """Only runnable extensions are accepted."""
    with pytest.raises(ValidationError, match="valid extension"):
        ScriptPathModel(file="scripts/notes.txt", scope="module")


@pytest.mark.parametrize("extension", [".sh", ".bash", ".py", ".ps1", ".js", ".mjs", ".go"])
def test_script_path_accepts_every_supported_extension(extension):
    """The documented extension set is the one actually enforced."""
    assert ScriptPathModel(file=f"scripts/run{extension}", scope="module").file.endswith(extension)


# ---------------------------------------------------------------------------
# Bare strings in ScriptsModel must follow the same rule
# ---------------------------------------------------------------------------


def test_scripts_accepts_bare_valid_path():
    """A bare string entry is a valid shorthand."""
    assert ScriptsModel(scripts=["scripts/init.ps1"]).scripts == ["scripts/init.ps1"]


@pytest.mark.parametrize("path", ESCAPING_PATHS)
def test_scripts_rejects_paths_escaping_the_solution(path):
    """The shorthand form cannot be a weaker path check than the long form."""
    with pytest.raises(ValidationError):
        ScriptsModel(scripts=[path])


def test_scripts_rejects_non_script_extension():
    """Extension rule applies to bare entries too."""
    with pytest.raises(ValidationError, match="valid extension"):
        ScriptsModel(scripts=["scripts/notes.txt"])


def test_scripts_accepts_mixed_shorthand_and_structured_entries():
    """Both entry forms may appear in one list."""
    model = ScriptsModel(scripts=["scripts/a.sh", ScriptPathModel(file="scripts/b.py", scope="module")])
    assert len(model.scripts) == 2


def test_scripts_allows_none():
    """The field is optional."""
    assert ScriptsModel(scripts=None).scripts is None


# ---------------------------------------------------------------------------
# SourceModel — git-based vs chart-based mode, and chart-based mode's own
# `remote`/`chart_repository` mutual exclusivity (docs/design/gap_fit_v1.md gap #2).
# ---------------------------------------------------------------------------


def test_source_model_git_based_with_no_remote_means_this_solution():
    model = SourceModel(source_path="terraform/modules/vpc")
    assert model.source_path == "terraform/modules/vpc"
    assert model.remote is None


def test_source_model_git_based_with_a_remote():
    model = SourceModel(remote="my-infra-repo", source_path="terraform/modules/vpc")
    assert model.remote == "my-infra-repo"


def test_source_model_requires_either_git_or_chart_mode():
    with pytest.raises(ValidationError, match="requires either a git-based source"):
        SourceModel()


def test_source_model_rejects_mixing_git_and_chart_selectors():
    with pytest.raises(ValidationError, match="cannot mix git-based"):
        SourceModel(source_path="charts/authentik", chart_name="authentik", remote="goauthentik")


def test_source_model_chart_based_with_remote():
    model = SourceModel(remote="goauthentik", chart_name="authentik", chart_version="2024.12.0")
    assert model.remote == "goauthentik"
    assert model.chart_repository is None


def test_source_model_chart_based_with_inline_chart_repository():
    """gap #2 Option 1: the common single-consumer, unauthenticated case
    needs no `spec.remotes` entry at all."""
    model = SourceModel(chart_repository="https://charts.goauthentik.io", chart_name="authentik")
    assert model.chart_repository == "https://charts.goauthentik.io"
    assert model.remote is None


def test_source_model_chart_based_accepts_an_oci_inline_chart_repository():
    model = SourceModel(chart_repository="oci://ghcr.io/org/charts", chart_name="authentik")
    assert model.chart_repository == "oci://ghcr.io/org/charts"


def test_source_model_chart_based_requires_remote_or_chart_repository():
    with pytest.raises(ValidationError, match="requires exactly one of `remote` or `chart_repository`"):
        SourceModel(chart_name="authentik")


def test_source_model_chart_based_rejects_both_remote_and_chart_repository():
    with pytest.raises(ValidationError, match="cannot mix `remote` and `chart_repository`"):
        SourceModel(chart_name="authentik", remote="goauthentik", chart_repository="https://charts.goauthentik.io")


def test_source_model_rejects_chart_repository_on_a_git_based_source():
    with pytest.raises(ValidationError, match="chart_repository is only valid for chart-based sources"):
        SourceModel(source_path="charts/authentik", chart_repository="https://charts.goauthentik.io")


def test_source_model_rejects_chart_version_on_a_git_based_source():
    with pytest.raises(ValidationError, match="chart_version is only valid for chart-based sources"):
        SourceModel(source_path="terraform/modules/vpc", chart_version="1.0.0")
