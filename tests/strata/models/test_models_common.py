#!/usr/bin/env python3
"""Tests for shared script-path validation in common_models."""

import pytest
from pydantic import ValidationError

from strata.models.common_models import ScriptPathModel, ScriptsModel

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
    model = ScriptsModel(
        scripts=["scripts/a.sh", ScriptPathModel(file="scripts/b.py", scope="module")]
    )
    assert len(model.scripts) == 2


def test_scripts_allows_none():
    """The field is optional."""
    assert ScriptsModel(scripts=None).scripts is None
