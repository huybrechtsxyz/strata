#!/usr/bin/env python3
"""Tests for `audit_read` (docs/design/audit-commands.md's Phase 3) —
`strata audit status`'s controller layer.

Phase 3 of that doc's Implementation Plan. Mirrors `test_audit_push.py`'s
own split: fast, hand-built-model unit tests for the config-level checks
(`resolve_readable_sink`/`sync_read_checkout`'s defensive branches, zero
I/O), a pure-filesystem test group for `list_latest_per_deployment`'s
glob/parse/filter logic (no git needed at all), and one real end-to-end
test driving a real local bare git repository to prove the whole
`audit_status()` orchestration actually works wired together.
"""

import json
import subprocess
from pathlib import Path

import pytest

from strata.controllers.audit_push import push_audit_files
from strata.controllers.audit_read import (
    AuditReadError,
    DeploymentStatusRecord,
    audit_status,
    list_latest_per_deployment,
    resolve_readable_sink,
    sync_read_checkout,
)
from strata.controllers.solution_context import open_solution
from strata.models.audit_model import AuditConfigModel, AuditGitSinkTargetModel, AuditSinkModel
from strata.models.solution_model import RemoteType, SolutionModel, SolutionRemoteModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError


def _git_sink(name: str = "compliance-archive", remote: str = "audit-repo", branch: str = "main") -> AuditSinkModel:
    return AuditSinkModel(name=name, git=AuditGitSinkTargetModel(remote=remote, branch=branch, path="records"))


def _manifest_json(
    *, deployment: str = "app", execution_id: str = "11111111-1111-1111-1111-111111111111", started_at: str
) -> str:
    return json.dumps(
        {
            "execution_id": execution_id,
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


# ---------------------------------------------------------------------------
# resolve_readable_sink — zero I/O, always checked first
# ---------------------------------------------------------------------------


def test_resolve_readable_sink_raises_when_audit_is_not_configured_at_all():
    with pytest.raises(UsageError, match="No audit git sink is configured"):
        resolve_readable_sink(None)


def test_resolve_readable_sink_raises_when_sinks_list_is_empty():
    with pytest.raises(UsageError, match="No audit git sink is configured"):
        resolve_readable_sink(AuditConfigModel(sinks=None))


def test_resolve_readable_sink_raises_when_only_integration_sinks_exist():
    config = AuditConfigModel(sinks=[AuditSinkModel(name="webhook-main", integration="siem")])
    with pytest.raises(UsageError, match="No audit git sink is configured"):
        resolve_readable_sink(config)


def test_resolve_readable_sink_ignores_disabled_git_sinks():
    config = AuditConfigModel(sinks=[AuditSinkModel(name="archive", enabled=False, git=_git_sink().git)])
    with pytest.raises(UsageError, match="No audit git sink is configured"):
        resolve_readable_sink(config)


def test_resolve_readable_sink_raises_when_more_than_one_git_sink_exists():
    config = AuditConfigModel(sinks=[_git_sink(name="compliance-archive"), _git_sink(name="team-mirror")])
    with pytest.raises(UsageError, match="'compliance-archive', 'team-mirror'"):
        resolve_readable_sink(config)


def test_resolve_readable_sink_returns_the_one_enabled_git_sink_ignoring_integration_sinks():
    config = AuditConfigModel(
        sinks=[AuditSinkModel(name="webhook-main", integration="siem"), _git_sink(name="compliance-archive")]
    )
    result = resolve_readable_sink(config)
    assert result.remote == "audit-repo"
    assert result.branch == "main"


# ---------------------------------------------------------------------------
# sync_read_checkout — the defensive branches require_valid() already guards
# in the real command flow, exercised directly here with hand-built models
# ---------------------------------------------------------------------------


def test_sync_read_checkout_raises_for_an_undeclared_remote(tmp_path: Path):
    solution = SolutionModel.model_validate({"meta": {"name": "s"}, "spec": {"remotes": []}})
    sink = _git_sink().git
    assert sink is not None
    with pytest.raises(AuditReadError, match="not declared"):
        sync_read_checkout(tmp_path, solution, sink)


def test_sync_read_checkout_raises_for_a_non_git_remote_type(tmp_path: Path):
    remote = SolutionRemoteModel(name="audit-repo", type=RemoteType.LOCAL, url=".")
    solution = SolutionModel.model_validate({"meta": {"name": "s"}, "spec": {"remotes": [remote.model_dump()]}})
    sink = _git_sink().git
    assert sink is not None
    with pytest.raises(AuditReadError, match="needs 'git'"):
        sync_read_checkout(tmp_path, solution, sink)


# ---------------------------------------------------------------------------
# list_latest_per_deployment — pure filesystem, no git needed at all
# ---------------------------------------------------------------------------


def _write_manifest(root: Path, relative: str, **kwargs) -> None:
    path = root / relative / "_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_manifest_json(**kwargs), encoding="utf-8")


def test_list_latest_per_deployment_finds_nested_manifests(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(
        tmp_path / "records",
        "prod/eu-west/web-frontend/20261005-140000",
        deployment="web-frontend",
        started_at="2026-10-05T14:00:00+00:00",
    )
    _write_manifest(
        tmp_path / "records",
        "customer-a/db-migrations/20261004-090000",
        deployment="db-migrations",
        started_at="2026-10-04T09:00:00+00:00",
    )

    diagnostics = Diagnostics()
    records = list_latest_per_deployment(tmp_path, sink, deployment=None, diagnostics=diagnostics)

    assert {r.deployment for r in records} == {"web-frontend", "db-migrations"}
    assert diagnostics.ok


def test_list_latest_per_deployment_keeps_only_the_max_started_at(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-01T00:00:00+00:00")
    _write_manifest(tmp_path / "records", "app/run2", deployment="app", started_at="2026-10-05T00:00:00+00:00")

    records = list_latest_per_deployment(tmp_path, sink, deployment=None, diagnostics=Diagnostics())

    assert len(records) == 1
    assert records[0].completed_at == "2026-10-05T00:00:00+00:00"


def test_list_latest_per_deployment_filters_by_deployment_name(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-05T00:00:00+00:00")
    _write_manifest(tmp_path / "records", "other/run1", deployment="other", started_at="2026-10-05T00:00:00+00:00")

    records = list_latest_per_deployment(tmp_path, sink, deployment="app", diagnostics=Diagnostics())

    assert [r.deployment for r in records] == ["app"]


def test_list_latest_per_deployment_skips_a_corrupted_manifest_with_a_warning(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-05T00:00:00+00:00")
    corrupt = tmp_path / "records" / "app" / "run2" / "_manifest.json"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("{not valid json", encoding="utf-8")

    diagnostics = Diagnostics()
    records = list_latest_per_deployment(tmp_path, sink, deployment=None, diagnostics=diagnostics)

    assert len(records) == 1  # the corrupted one is skipped, not fatal
    assert diagnostics.ok  # a warning, never an error
    assert any(d.code == "audit_read_manifest_unparseable" for d in diagnostics.items)


def test_list_latest_per_deployment_named_deployment_with_no_records_is_not_an_error(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-05T00:00:00+00:00")

    diagnostics = Diagnostics()
    records = list_latest_per_deployment(tmp_path, sink, deployment="never-deployed", diagnostics=diagnostics)

    assert records == []
    assert diagnostics.ok
    assert any(d.code == "audit_read_no_records_for_deployment" for d in diagnostics.items)


def test_list_latest_per_deployment_no_records_at_all_is_not_an_error(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    (tmp_path / "records").mkdir()

    diagnostics = Diagnostics()
    records = list_latest_per_deployment(tmp_path, sink, deployment=None, diagnostics=diagnostics)

    assert records == []
    assert diagnostics.ok
    assert any(d.code == "audit_read_no_records" for d in diagnostics.items)


# ---------------------------------------------------------------------------
# Real end-to-end test — a real local bare git repository
# ---------------------------------------------------------------------------

MANIFEST_TEMPLATE = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
  remotes:
    - name: audit-repo
      type: git
      url: {remote_url}
      reference: main
"""

CONFIGURATION_TEMPLATE = """apiVersion: strata.huybrechts.xyz/v2
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


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solution_root(tmp_path: Path, remote_url: str) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST_TEMPLATE.format(remote_url=remote_url))
    _write(root, "configuration.yaml", CONFIGURATION_TEMPLATE)
    return root


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=False)
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_real_audit_status_round_trip(tmp_path: Path):
    """Push two manifests for the same deployment (an older and a newer run)
    plus one for a different deployment, via the real `push_audit_files()`,
    then confirm `audit_status()` reads them all back correctly: only the
    newer `app` run is kept, `other` shows up too, and a name filter for a
    deployment that was never deployed reports empty + info, not an error."""
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)

    root = _solution_root(tmp_path, str(bare_repo))
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    context = context.require_valid()

    sink = AuditGitSinkTargetModel(remote="audit-repo", branch="main", path="records")
    solution = context.controller.solution
    assert solution is not None

    older = tmp_path / "older.json"
    older.write_text(_manifest_json(deployment="app", started_at="2026-10-01T00:00:00+00:00"))
    newer = tmp_path / "newer.json"
    newer.write_text(_manifest_json(deployment="app", started_at="2026-10-05T00:00:00+00:00"))
    other = tmp_path / "other.json"
    other.write_text(_manifest_json(deployment="other", started_at="2026-10-03T00:00:00+00:00"))

    assert push_audit_files(root, sink, solution, {"_manifest.json": older}, Path("run1"), "actor").success
    assert push_audit_files(root, sink, solution, {"_manifest.json": newer}, Path("run2"), "actor").success
    assert push_audit_files(root, sink, solution, {"_manifest.json": other}, Path("run3"), "actor").success

    records, diagnostics = audit_status(context, None)

    assert diagnostics.ok
    by_deployment = {r.deployment: r for r in records}
    assert set(by_deployment) == {"app", "other"}
    assert by_deployment["app"].completed_at == "2026-10-05T00:00:00+00:00"  # the newer run, not the older
    assert isinstance(by_deployment["app"], DeploymentStatusRecord)

    # a deployment that genuinely has nothing recorded yet — not an error
    empty_records, empty_diagnostics = audit_status(context, "never-deployed")
    assert empty_records == []
    assert empty_diagnostics.ok
    assert any(d.code == "audit_read_no_records_for_deployment" for d in empty_diagnostics.items)
