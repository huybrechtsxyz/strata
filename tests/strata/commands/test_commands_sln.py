#!/usr/bin/env python3
"""Tests for `strata sln init`/`strata sln update`."""

import re

import click
import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE
from strata.controllers.scaffold_controller import _AGENT_SKILL_TEMPLATE


@pytest.fixture
def runner():
    return CliRunner()


def _init(runner, path, name="acme-platform"):
    return runner.invoke(cli, ["sln", "init", name, "--path", str(path)])


def _update(runner, path):
    return runner.invoke(cli, ["sln", "update", "--path", str(path)])


def test_init_exits_zero_and_creates_the_manifest(runner, tmp_path):
    result = _init(runner, tmp_path)
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert (tmp_path / "strata.yaml").is_file()


def test_init_creates_the_scaffolded_files(runner, tmp_path):
    _init(runner, tmp_path)
    assert (tmp_path / ".strata" / "README.md").is_file()
    assert (tmp_path / ".github" / "pull_request_template.md").is_file()
    assert (tmp_path / ".github" / "ISSUE_TEMPLATE" / "deployment-change-request.yml").is_file()
    assert (tmp_path / ".vscode" / "extensions.json").is_file()
    assert (tmp_path / ".claude" / "skills" / "strata" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "skills" / "project-gotchas" / "SKILL.md").is_file()


def _visible_commands(group: click.Group, prefix: str = "") -> list[tuple[str, str]]:
    """(parent path, name) for every visible leaf command, e.g. ("strata values", "list")."""
    found: list[tuple[str, str]] = []
    for name, command in group.commands.items():
        if command.hidden:
            continue
        if isinstance(command, click.Group):
            found += _visible_commands(command, f"{prefix} {name}")
        else:
            found.append((f"strata{prefix}", name))
    return found


@pytest.mark.parametrize(("parent", "name"), _visible_commands(cli))
def test_agent_skill_documents_every_visible_command(parent, name):
    """Drift guard (ADR-0046): a new command must be added to the skill's command table."""
    table = [line for line in _AGENT_SKILL_TEMPLATE.read_text(encoding="utf-8").splitlines() if line.startswith("|")]
    if parent == "strata":
        assert any(f"`strata {name}" in line for line in table)
    else:
        assert any(parent in line and re.search(rf"[ /]{name}\b", line.split(parent, 1)[1]) for line in table)


def test_init_is_safe_to_re_run(runner, tmp_path):
    _init(runner, tmp_path, name="acme-platform")
    result = _init(runner, tmp_path, name="a-different-name")

    assert result.exit_code == EXIT_SUCCESS, result.output
    content = (tmp_path / "strata.yaml").read_text(encoding="utf-8")
    assert "name: acme-platform" in content
    assert "a-different-name" not in content


def test_update_before_init_exits_with_usage_error(runner, tmp_path):
    result = _update(runner, tmp_path)
    assert result.exit_code == EXIT_USAGE, result.output


def test_update_after_init_exits_zero_and_preserves_a_github_customization(runner, tmp_path):
    _init(runner, tmp_path)
    pr_template = tmp_path / ".github" / "pull_request_template.md"
    customized = pr_template.read_text(encoding="utf-8") + "\n## Custom field\n"
    pr_template.write_text(customized, encoding="utf-8")

    result = _update(runner, tmp_path)

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert pr_template.read_text(encoding="utf-8") == customized


def test_init_output_reports_created_files(runner, tmp_path):
    result = _init(runner, tmp_path)
    assert "strata.yaml" in result.output
    assert ".strata" in result.output or "README.md" in result.output
