#!/usr/bin/env python3
"""Tests for ScaffoldController (docs/design/solution-scaffolding.md)."""

import pytest

from strata.controllers.scaffold_controller import ScaffoldController
from strata.utils.errors import UsageError


def test_init_creates_the_manifest(tmp_path):
    ScaffoldController(tmp_path).init("acme-platform")

    manifest = tmp_path / "strata.yaml"
    assert manifest.is_file()
    assert "name: acme-platform" in manifest.read_text(encoding="utf-8")


def test_init_creates_the_scaffolded_files(tmp_path):
    ScaffoldController(tmp_path).init("acme-platform")

    assert (tmp_path / ".strata" / "README.md").is_file()
    assert (tmp_path / ".github" / "pull_request_template.md").is_file()
    assert (tmp_path / ".github" / "ISSUE_TEMPLATE" / "deployment-change-request.yml").is_file()


def test_init_is_idempotent_and_never_overwrites_the_manifest(tmp_path):
    ScaffoldController(tmp_path).init("acme-platform")
    manifest = tmp_path / "strata.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "# hand-added comment\n", encoding="utf-8")

    ScaffoldController(tmp_path).init("a-different-name")

    content = manifest.read_text(encoding="utf-8")
    assert "name: acme-platform" in content
    assert "a-different-name" not in content
    assert "# hand-added comment" in content


def test_update_requires_an_existing_manifest(tmp_path):
    with pytest.raises(UsageError, match="strata.yaml"):
        ScaffoldController(tmp_path).update()


def test_update_after_init_is_a_no_op_when_nothing_upstream_changed(tmp_path):
    ScaffoldController(tmp_path).init("acme-platform")
    readme = tmp_path / ".strata" / "README.md"
    original = readme.read_text(encoding="utf-8")

    ScaffoldController(tmp_path).update()

    assert readme.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# The full "Worked example" from the design doc: init, a user customization,
# then update after a (simulated) package upgrade.
# ---------------------------------------------------------------------------


def test_worked_example_update_refreshes_strata_but_preserves_github_customization(tmp_path):
    ScaffoldController(tmp_path).init("acme-platform")

    manifest = tmp_path / "strata.yaml"
    manifest_before = manifest.read_text(encoding="utf-8")

    pr_template = tmp_path / ".github" / "pull_request_template.md"
    customized = pr_template.read_text(encoding="utf-8") + "\n## SOC2 control reference\n<!-- ... -->\n"
    pr_template.write_text(customized, encoding="utf-8")

    readme = tmp_path / ".strata" / "README.md"
    readme.write_text("this represents the OLD package content\n", encoding="utf-8")

    issue_template = tmp_path / ".github" / "ISSUE_TEMPLATE" / "deployment-change-request.yml"
    issue_template_before = issue_template.read_text(encoding="utf-8")

    ScaffoldController(tmp_path).update()

    # strata.yaml: untouched — update() never writes it at all.
    assert manifest.read_text(encoding="utf-8") == manifest_before
    # .strata/README.md: package-owned — overwritten back to the real package content.
    assert readme.read_text(encoding="utf-8") != "this represents the OLD package content\n"
    # .github/pull_request_template.md: user-owned — the customization survives.
    assert issue_template.read_text(encoding="utf-8") == issue_template_before
    assert pr_template.read_text(encoding="utf-8") == customized
    assert "SOC2 control reference" in pr_template.read_text(encoding="utf-8")
