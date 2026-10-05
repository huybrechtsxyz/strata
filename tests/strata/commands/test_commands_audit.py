#!/usr/bin/env python3
"""Tests for `strata audit status` (docs/design/audit-commands.md's Phase 4).

Phase 4 of that doc's Implementation Plan — the CLI surface over
`controllers/audit_read.py`'s `audit_status()`, already covered at the
controller layer by `test_audit_read.py`. These tests confirm the same
behaviour end to end through the real CLI: console rendering, the
`--output json` envelope shape, and — per that doc's explicit emphasis —
the zero/multiple-sink error message and exit code asserted through the
real command, not only at the controller layer.
"""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_FAILURE, EXIT_SUCCESS, EXIT_USAGE
from strata.controllers.audit_push import push_audit_files
from strata.controllers.solution_context import open_solution
from strata.models.audit_model import AuditGitSinkTargetModel

MANIFEST_TEMPLATE = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
{remotes}
"""

CONFIGURATION_WITH_SINK = """apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: cfg
spec:
  audit:
    sinks:
      - name: compliance-archive
        git:
          remote: audit-repo
          branch: main
          path: records
"""

CONFIGURATION_WITH_TWO_SINKS = """apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: cfg
spec:
  audit:
    sinks:
      - name: compliance-archive
        git:
          remote: audit-repo
          branch: main
          path: records
      - name: team-mirror
        git:
          remote: audit-repo
          branch: audit
          path: records
"""

CONFIGURATION_WITHOUT_AUDIT = """apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: cfg
spec: {}
"""


@pytest.fixture
def runner():
    return CliRunner()


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=False)
        return True
    except FileNotFoundError:
        return False


def _manifest_json(*, deployment: str, started_at: str) -> str:
    return json.dumps(
        {
            "execution_id": "11111111-1111-1111-1111-111111111111",
            "action": "deploy",
            "deployment": deployment,
            "workspace": "main",
            "started_at": started_at,
            "completed_at": started_at,
            "status": "success",
            "deployed_by": "ci-runner",
            "version": "2.4.1",
            "artifacts": {"platform": {"hash": "sha256:abc123", "path": "resolved.yaml"}},
        }
    )


def _solution_with_sink(tmp_path: Path, remote_url: str, configuration_yaml: str = CONFIGURATION_WITH_SINK) -> Path:
    root = tmp_path / "sln"
    remotes = f"  remotes:\n    - name: audit-repo\n      type: git\n      url: {remote_url}\n      reference: main"
    _write(root, "strata.yaml", MANIFEST_TEMPLATE.format(remotes=remotes))
    _write(root, "configuration.yaml", configuration_yaml)
    return root


def _push_manifest(root: Path, *, deployment: str, started_at: str, relative: str) -> None:
    context = open_solution(root).require_valid()
    solution = context.controller.solution
    assert solution is not None
    sink = AuditGitSinkTargetModel(remote="audit-repo", branch="main", path="records")
    manifest_src = root / "_manifest_src.json"
    manifest_src.write_text(_manifest_json(deployment=deployment, started_at=started_at), encoding="utf-8")
    result = push_audit_files(root, sink, solution, {"_manifest.json": manifest_src}, Path(relative), "actor")
    assert result.success, result.detail


# ---------------------------------------------------------------------------
# Config-level failures — fail fast, before any network access
# ---------------------------------------------------------------------------


def test_outside_a_solution_exits_two(runner, tmp_path):
    result = runner.invoke(cli, ["audit", "status", "--path", str(tmp_path)])
    assert result.exit_code == EXIT_USAGE


def test_no_configuration_document_exits_two(runner, tmp_path):
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST_TEMPLATE.format(remotes="  {}"))
    result = runner.invoke(cli, ["audit", "status", "--path", str(root)])
    assert result.exit_code == EXIT_USAGE
    assert "No audit git sink is configured" in result.output


def test_no_audit_sink_configured_exits_two_with_a_clear_message(runner, tmp_path):
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST_TEMPLATE.format(remotes="  {}"))
    _write(root, "configuration.yaml", CONFIGURATION_WITHOUT_AUDIT)
    result = runner.invoke(cli, ["audit", "status", "--path", str(root)])
    assert result.exit_code == EXIT_USAGE
    assert "No audit git sink is configured" in result.output
    assert "spec.audit.sinks" in result.output


def test_multiple_audit_sinks_exits_two_naming_both(runner, tmp_path):
    root = tmp_path / "sln"
    remotes = "  remotes:\n    - name: audit-repo\n      type: git\n      url: https://example.invalid/x.git\n      reference: main"
    _write(root, "strata.yaml", MANIFEST_TEMPLATE.format(remotes=remotes))
    _write(root, "configuration.yaml", CONFIGURATION_WITH_TWO_SINKS)
    result = runner.invoke(cli, ["audit", "status", "--path", str(root)])
    assert result.exit_code == EXIT_USAGE
    assert "compliance-archive" in result.output
    assert "team-mirror" in result.output


def test_unreachable_remote_exits_one(runner, tmp_path):
    root = tmp_path / "sln"
    remotes = (
        "  remotes:\n    - name: audit-repo\n      type: git\n      "
        "url: https://example.invalid/does-not-exist.git\n      reference: main"
    )
    _write(root, "strata.yaml", MANIFEST_TEMPLATE.format(remotes=remotes))
    _write(root, "configuration.yaml", CONFIGURATION_WITH_SINK)
    result = runner.invoke(cli, ["audit", "status", "--path", str(root)])
    assert result.exit_code == EXIT_FAILURE


# ---------------------------------------------------------------------------
# Real round-trip through the CLI
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_console_output_lists_the_latest_record_per_deployment(runner, tmp_path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = _solution_with_sink(tmp_path, str(bare_repo))

    _push_manifest(root, deployment="web-frontend", started_at="2026-10-05T14:02:11+00:00", relative="run1")
    _push_manifest(root, deployment="api-backend", started_at="2026-10-05T13:47:02+00:00", relative="run2")

    result = runner.invoke(cli, ["audit", "status", "--path", str(root)])
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "web-frontend" in result.output
    assert "api-backend" in result.output
    assert "DEPLOYMENT" in result.output and "STATUS" in result.output  # header present


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_named_deployment_argument_filters_to_one(runner, tmp_path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = _solution_with_sink(tmp_path, str(bare_repo))

    _push_manifest(root, deployment="web-frontend", started_at="2026-10-05T14:02:11+00:00", relative="run1")
    _push_manifest(root, deployment="api-backend", started_at="2026-10-05T13:47:02+00:00", relative="run2")

    result = runner.invoke(cli, ["audit", "status", "web-frontend", "--path", str(root)])
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "web-frontend" in result.output
    assert "api-backend" not in result.output


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_named_deployment_with_no_records_is_not_an_error(runner, tmp_path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = _solution_with_sink(tmp_path, str(bare_repo))
    _push_manifest(root, deployment="web-frontend", started_at="2026-10-05T14:02:11+00:00", relative="run1")

    result = runner.invoke(cli, ["audit", "status", "never-deployed", "--path", str(root)])
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "no audit records found" in result.output


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_json_output_carries_the_deployments_list(runner, tmp_path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = _solution_with_sink(tmp_path, str(bare_repo))
    _push_manifest(root, deployment="web-frontend", started_at="2026-10-05T14:02:11+00:00", relative="run1")

    result = runner.invoke(cli, ["audit", "status", "--path", str(root), "--output", "json"])
    assert result.exit_code == EXIT_SUCCESS, result.output

    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    deployments = envelope["data"]["deployments"]
    assert len(deployments) == 1
    record = deployments[0]
    assert record["deployment"] == "web-frontend"
    assert record["status"] == "success"
    assert record["version"] == "2.4.1"
    assert record["deployed_by"] == "ci-runner"
    assert record["completed_at"] == "2026-10-05T14:02:11+00:00"
    assert record["change_reference"] is None


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_json_output_on_an_empty_sink(runner, tmp_path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = _solution_with_sink(tmp_path, str(bare_repo))

    result = runner.invoke(cli, ["audit", "status", "--path", str(root), "--output", "json"])
    assert result.exit_code == EXIT_SUCCESS, result.output
    envelope = json.loads(result.output)
    assert envelope["data"]["deployments"] == []
