#!/usr/bin/env python3
"""Tests for `strata version new`/`update`/`set`."""

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""

PROVIDER = """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
"""

WORKSPACE = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
"""

MODULE = """apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: web
spec:
  source:
    source_path: modules/web
  services:
    - name: web
      image: nginx:1.27-alpine
"""

VERSION = """apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: prd
spec:
  pins: {}
"""


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def solution(tmp_path):
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "provider.yaml").write_text(PROVIDER, encoding="utf-8")
    (root / "workspace.yaml").write_text(WORKSPACE, encoding="utf-8")
    (root / "module.yaml").write_text(MODULE, encoding="utf-8")
    return root


def _new(runner, *args):
    return runner.invoke(cli, ["version", "new", *[str(a) for a in args]])


def _update(runner, *args):
    return runner.invoke(cli, ["version", "update", *[str(a) for a in args]])


def _set(runner, *args):
    return runner.invoke(cli, ["version", "set", *[str(a) for a in args]])


# ---------------------------------------------------------------------------
# version new
# ---------------------------------------------------------------------------


def test_new_creates_a_minimal_document(runner, solution):
    result = _new(runner, "dev", "--workspace", "main", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS

    destination = solution / "versions" / "dev.yaml"
    assert destination.is_file()
    data = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert data["meta"]["name"] == "dev"
    assert data["spec"]["workspace"] == "main"


def test_new_rejects_a_colliding_name(runner, solution):
    (solution / "versions").mkdir()
    (solution / "versions" / "prd.yaml").write_text(VERSION, encoding="utf-8")

    result = _new(runner, "prd", "--workspace", "main", "--path", solution)
    assert result.exit_code == EXIT_USAGE
    assert "already exists" in result.output


def test_new_rejects_an_unknown_workspace(runner, solution):
    result = _new(runner, "dev", "--workspace", "ghost", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_new_outside_a_solution_exits_usage_failure(runner, tmp_path):
    result = _new(runner, "dev", "--workspace", "main", "--path", tmp_path)
    assert result.exit_code == EXIT_USAGE


def test_new_json_output_reports_the_created_path(runner, solution):
    result = _new(runner, "dev", "--workspace", "main", "--path", solution, "--output", "json")
    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["data"]["name"] == "dev"
    assert envelope["data"]["workspace"] == "main"
    assert Path(envelope["data"]["path"]) == (solution / "versions" / "dev.yaml").resolve()


def test_new_from_seeds_pins_with_current_values(runner, solution):
    (solution / "versions").mkdir()
    (solution / "versions" / "prd.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
        "  pins:\n    images:\n      web: some-other-tag\n",
        encoding="utf-8",
    )

    result = _new(runner, "dev", "--workspace", "main", "--from", "prd", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS

    data = yaml.safe_load((solution / "versions" / "dev.yaml").read_text(encoding="utf-8"))
    assert data["spec"]["pins"]["images"]["web"] == "nginx:1.27-alpine"


# ---------------------------------------------------------------------------
# version update
# ---------------------------------------------------------------------------


def test_update_reports_add_candidate_and_exits_validation_failure(runner, solution):
    (solution / "versions").mkdir()
    (solution / "versions" / "prd.yaml").write_text(VERSION, encoding="utf-8")

    result = _update(runner, "prd", "--path", solution)
    assert result.exit_code == EXIT_VALIDATION
    assert "images.web" in result.output


def test_update_on_a_fully_reconciled_document_exits_zero(runner, solution):
    (solution / "versions").mkdir()
    (solution / "versions" / "prd.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
        "  pins:\n    images:\n      web: nginx:1.27-alpine\n",
        encoding="utf-8",
    )

    result = _update(runner, "prd", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS
    assert "fully reconciled" in result.output


def test_update_unknown_name_exits_usage_failure(runner, solution):
    result = _update(runner, "ghost", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_update_json_output_carries_every_candidate(runner, solution):
    (solution / "versions").mkdir()
    (solution / "versions" / "prd.yaml").write_text(VERSION, encoding="utf-8")

    result = _update(runner, "prd", "--path", solution, "--output", "json")
    assert result.exit_code == EXIT_VALIDATION
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    candidates = envelope["data"]["candidates"]
    assert any(c["category"] == "images" and c["name"] == "web" and c["action"] == "add" for c in candidates)


# ---------------------------------------------------------------------------
# version set
# ---------------------------------------------------------------------------

SET_VERSION_DOC = """apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: prd
spec:
  pins:
    images:
      # Shorthand — nothing is being held back.
      caddy: caddy:2-alpine
      db:
        version: docker.io/library/postgres:16-alpine
        status: held
        available: 18.6-alpine
        reason: "postgres majors need pg_upgrade/dump-restore"
        reviewed: 2026-09-08
"""


def _set_solution(solution: Path) -> Path:
    (solution / "versions").mkdir()
    path = solution / "versions" / "prd.yaml"
    path.write_text(SET_VERSION_DOC, encoding="utf-8")
    return path


def test_set_shorthand_pin_and_preserves_comments(runner, solution):
    path = _set_solution(solution)
    before = path.read_text(encoding="utf-8")

    result = _set(runner, "prd", "images", "caddy", "caddy:2.1-alpine", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS

    after = path.read_text(encoding="utf-8")
    assert after == before.replace("caddy:2-alpine", "caddy:2.1-alpine")


def test_set_refuses_held_pin_without_force(runner, solution):
    _set_solution(solution)

    result = _set(runner, "prd", "images", "db", "17.0-alpine", "--path", solution)
    assert result.exit_code == EXIT_USAGE
    assert "--force" in result.output


def test_set_held_pin_with_force_succeeds(runner, solution):
    path = _set_solution(solution)

    result = _set(runner, "prd", "images", "db", "17.0-alpine", "--force", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    db = data["spec"]["pins"]["images"]["db"]
    assert db["version"] == "17.0-alpine"
    assert db["status"] == "held"
    assert db["reason"] == "postgres majors need pg_upgrade/dump-restore"


def test_set_available_only_is_allowed_without_force(runner, solution):
    path = _set_solution(solution)

    result = _set(runner, "prd", "images", "db", "--available", "19.0-alpine", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    db = data["spec"]["pins"]["images"]["db"]
    assert db["available"] == "19.0-alpine"
    assert db["version"] == "docker.io/library/postgres:16-alpine"


def test_set_creates_a_new_pin(runner, solution):
    path = _set_solution(solution)

    result = _set(runner, "prd", "images", "redis", "redis:7-alpine", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["spec"]["pins"]["images"]["redis"] == "redis:7-alpine"


def test_set_rejects_creating_a_pin_with_only_available(runner, solution):
    _set_solution(solution)

    result = _set(runner, "prd", "images", "redis", "--available", "redis:7-alpine", "--path", solution)
    assert result.exit_code == EXIT_USAGE
    assert "does not exist yet" in result.output


def test_set_unknown_name_exits_usage_failure(runner, solution):
    result = _set(runner, "ghost", "images", "caddy", "1.0.0", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_set_unknown_category_exits_usage_failure(runner, solution):
    _set_solution(solution)

    result = _set(runner, "prd", "tools", "caddy", "1.0.0", "--path", solution)
    assert result.exit_code == EXIT_USAGE


def test_set_json_output_reports_the_new_pin(runner, solution):
    _set_solution(solution)

    result = _set(runner, "prd", "images", "caddy", "caddy:2.1-alpine", "--path", solution, "--output", "json")
    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["data"] == {
        "name": "prd",
        "category": "images",
        "target": "caddy",
        "version": "caddy:2.1-alpine",
        "available": None,
        "status": "current",
    }


# ---------------------------------------------------------------------------
# bare `strata version` still works (naming-collision regression guard)
# ---------------------------------------------------------------------------


def test_bare_version_still_prints_the_cli_version(runner):
    from strata.utils.version import get_version

    result = runner.invoke(cli, ["version"])
    assert result.exit_code == EXIT_SUCCESS
    assert result.output.strip() == get_version()
