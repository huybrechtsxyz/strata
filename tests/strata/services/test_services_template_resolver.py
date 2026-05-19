"""Tests for the template_resolver service."""

import textwrap
from pathlib import Path

import pytest

from strata.exceptions import ModelValidationError, PlatformError
from strata.services.template_resolver import (
    list_builtin_templates,
    resolve_template,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_template_folder(base: Path, name: str = "mytpl", with_manifest: bool = True) -> Path:
    """Create a minimal template folder structure under *base*."""
    folder = base / name
    scaffold = folder / "scaffold"
    scaffold.mkdir(parents=True)
    (scaffold / "README.md").write_text("# ${solution_name}\n", encoding="utf-8")
    if with_manifest:
        (folder / "template.yaml").write_text(
            textwrap.dedent("""\
                name: mytpl
                description: Test template
                variables:
                  - name: solution_name
                    default: my-sol
            """),
            encoding="utf-8",
        )
    return folder


# ---------------------------------------------------------------------------
# list_builtin_templates
# ---------------------------------------------------------------------------


class TestListBuiltinTemplates:
    def test_returns_aks(self):
        names = list_builtin_templates()
        assert "aks" in names

    def test_returns_sorted_list(self):
        names = list_builtin_templates()
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# resolve_template — git+ prefix
# ---------------------------------------------------------------------------


class TestResolveTemplateGitPrefix:
    def test_git_prefix_raises_platform_error(self):
        with pytest.raises(PlatformError) as exc_info:
            resolve_template("git+https://github.com/org/repo.git")
        assert "TEMPLATE_GIT_NOT_SUPPORTED" in exc_info.value.error_code


# ---------------------------------------------------------------------------
# resolve_template — local folder
# ---------------------------------------------------------------------------


class TestResolveTemplateLocalFolder:
    def test_valid_local_folder_with_manifest(self, tmp_path):
        folder = _make_template_folder(tmp_path)
        scaffold_dir, manifest = resolve_template(str(folder))
        assert scaffold_dir == (folder / "scaffold").resolve()
        assert manifest is not None
        assert manifest.name == "mytpl"

    def test_valid_local_folder_no_manifest(self, tmp_path):
        folder = _make_template_folder(tmp_path, with_manifest=False)
        scaffold_dir, manifest = resolve_template(str(folder))
        assert scaffold_dir.is_dir()
        assert manifest is None

    def test_local_folder_missing_scaffold_raises(self, tmp_path):
        folder = tmp_path / "notpl"
        folder.mkdir()
        with pytest.raises(PlatformError) as exc_info:
            resolve_template(str(folder))
        assert "TEMPLATE_NO_SCAFFOLD" in exc_info.value.error_code

    def test_invalid_manifest_yaml_raises(self, tmp_path):
        folder = tmp_path / "bad"
        (folder / "scaffold").mkdir(parents=True)
        (folder / "template.yaml").write_text("name: [unclosed", encoding="utf-8")
        with pytest.raises(PlatformError) as exc_info:
            resolve_template(str(folder))
        assert "TEMPLATE_MANIFEST_YAML_ERROR" in exc_info.value.error_code

    def test_manifest_missing_required_field_raises(self, tmp_path):
        folder = tmp_path / "bad2"
        (folder / "scaffold").mkdir(parents=True)
        (folder / "template.yaml").write_text("description: no name\n", encoding="utf-8")
        with pytest.raises(ModelValidationError):
            resolve_template(str(folder))


# ---------------------------------------------------------------------------
# resolve_template — built-in short name
# ---------------------------------------------------------------------------


class TestResolveTemplateBuiltin:
    def test_aks_resolves_to_scaffold_dir(self):
        scaffold_dir, manifest = resolve_template("aks")
        assert scaffold_dir.is_dir()
        assert manifest is not None
        assert manifest.name == "aks"

    def test_unknown_name_raises_platform_error(self):
        with pytest.raises(PlatformError) as exc_info:
            resolve_template("nonexistent_xyz_template")
        assert "TEMPLATE_NOT_FOUND" in exc_info.value.error_code
        assert "nonexistent_xyz_template" in exc_info.value.message

    def test_unknown_name_error_lists_available(self):
        with pytest.raises(PlatformError) as exc_info:
            resolve_template("nonexistent_xyz_template")
        assert "aks" in exc_info.value.message
