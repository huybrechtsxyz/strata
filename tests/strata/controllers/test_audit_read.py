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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from strata.controllers import audit_read
from strata.controllers.audit_push import push_audit_files
from strata.controllers.audit_read import (
    AuditReadError,
    DeploymentStatusRecord,
    _parse_range_bound,
    audit_changes,
    audit_status,
    list_latest_per_deployment,
    list_manifests_in_range,
    resolve_readable_sink,
    sync_read_checkout,
)
from strata.controllers.git_checkout import SyncResult
from strata.controllers.solution_context import open_solution
from strata.models.audit_model import AuditConfigModel, AuditGitSinkTargetModel, AuditSinkModel
from strata.models.solution_model import RemoteFetch, RemoteType, SolutionModel, SolutionRemoteModel
from strata.utils import layout
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError


def _git_sink(name: str = "compliance-archive", remote: str = "audit-repo", branch: str = "main") -> AuditSinkModel:
    return AuditSinkModel(name=name, git=AuditGitSinkTargetModel(remote=remote, branch=branch, path="records"))


def _manifest_json(
    *,
    deployment: str = "app",
    execution_id: str = "11111111-1111-1111-1111-111111111111",
    started_at: str,
    status: str = "success",
    action: str = "deploy",
    commit_sha: str | None = None,
    stages: list[dict] | None = None,
) -> str:
    return json.dumps(
        {
            "execution_id": execution_id,
            "action": action,
            "deployment": deployment,
            "workspace": "main",
            "started_at": started_at,
            "completed_at": started_at,
            "status": status,
            "deployed_by": "ci-runner",
            "version": "2.4.1",
            "artifacts": {"platform": {"hash": "sha256:abc123", "path": "resolved.yaml"}},
            **({"commit_sha": commit_sha} if commit_sha else {}),
            **({"stages": stages} if stages else {}),
        }
    )


# ---------------------------------------------------------------------------
# resolve_readable_sink — zero I/O, always checked first
# ---------------------------------------------------------------------------


def test_resolve_readable_sink_raises_when_audit_is_not_configured_at_all():
    with pytest.raises(UsageError, match="No audit git sink is configured"):
        resolve_readable_sink(None)


def test_resolve_readable_sink_zero_sink_message_names_both_commands_not_just_status():
    """Found during review: this check is shared by audit_status() AND
    audit_changes() — an earlier message hardcoded 'strata audit status',
    which was actively misleading when raised for 'strata audit changes'."""
    with pytest.raises(UsageError) as exc_info:
        resolve_readable_sink(None)
    message = str(exc_info.value)
    assert "strata audit status" in message
    assert "strata audit changes" in message


def test_resolve_readable_sink_multiple_sink_message_names_both_commands_not_just_status():
    config = AuditConfigModel(sinks=[_git_sink(name="compliance-archive"), _git_sink(name="team-mirror")])
    with pytest.raises(UsageError) as exc_info:
        resolve_readable_sink(config)
    message = str(exc_info.value)
    assert "supports exactly" not in message  # the old, status-specific phrasing
    assert "reading more than one is not supported yet" in message


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


def _recording_sync(calls: list[tuple], result: SyncResult | None = None):
    def fake_sync(url, branch, checkout_path, *, fetch=RemoteFetch.STRATA, env=None, fallback_to_default=True):
        calls.append((url, branch, checkout_path, fetch, fallback_to_default))
        return result or SyncResult(True)

    return fake_sync


def test_sync_read_checkout_clones_the_sink_branch_of_a_path_remote(tmp_path: Path, monkeypatch):
    """A path remote implies `fetch: external`, which covers only the pipeline's
    checkout: the read clones `remote.url` at `sink.branch` itself."""
    calls: list[tuple] = []
    fake_sync = _recording_sync(calls)

    monkeypatch.setattr(audit_read, "ensure_synced_checkout", fake_sync)
    remote = SolutionRemoteModel(name="audit-repo", type=RemoteType.GIT, url="https://h/x.git", path="repos/x")
    assert remote.fetch is RemoteFetch.EXTERNAL
    solution = SolutionModel.model_validate({"meta": {"name": "s"}, "spec": {"remotes": [remote.model_dump()]}})
    sink = _git_sink(branch="audit").git
    assert sink is not None

    checkout_path = sync_read_checkout(tmp_path, solution, sink)

    assert checkout_path == layout.audit_read_checkout_path(tmp_path, "audit-repo", "audit")
    assert calls == [("https://h/x.git", "audit", checkout_path, RemoteFetch.STRATA, False)]
    assert not (tmp_path / "repos" / "x").exists()  # the pipeline's checkout is never touched


def test_sync_read_checkout_fetches_itself_for_a_fetch_external_reference_remote(tmp_path: Path, monkeypatch):
    """`fetch: external` names a checkout a pipeline places for builds; nobody
    places strata's read checkout, so the read never waits for one."""
    calls: list[tuple] = []
    monkeypatch.setattr(audit_read, "ensure_synced_checkout", _recording_sync(calls))
    remote = SolutionRemoteModel(
        name="audit-repo", type=RemoteType.GIT, url="https://h/x.git", reference="main", fetch=RemoteFetch.EXTERNAL
    )
    solution = SolutionModel.model_validate({"meta": {"name": "s"}, "spec": {"remotes": [remote.model_dump()]}})
    sink = _git_sink().git
    assert sink is not None

    sync_read_checkout(tmp_path, solution, sink)

    assert [call[3] for call in calls] == [RemoteFetch.STRATA]


def test_sync_read_checkout_returns_none_when_the_sink_branch_is_missing(tmp_path: Path, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        audit_read, "ensure_synced_checkout", _recording_sync(calls, SyncResult(True, branch_found=False))
    )
    remote = SolutionRemoteModel(name="audit-repo", type=RemoteType.GIT, url="https://h/x.git", reference="main")
    solution = SolutionModel.model_validate({"meta": {"name": "s"}, "spec": {"remotes": [remote.model_dump()]}})
    sink = _git_sink(branch="audit").git
    assert sink is not None

    assert sync_read_checkout(tmp_path, solution, sink) is None
    assert [call[4] for call in calls] == [False]  # never the default branch in its place


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


def test_a_record_names_the_step_a_partial_run_stopped_at(tmp_path: Path):
    """docs/design/audit-trail.md, "Stages": the first failed stage, read from the record."""
    sink = _git_sink().git
    assert sink is not None
    stages = [
        {"name": "apply_infra", "status": "success"},
        {"name": "apply_apps", "status": "failed"},
        {"name": "apply_dns", "status": "skipped", "skip_reason": "an earlier step failed"},
    ]
    _write_manifest(
        tmp_path / "records", "app/run1", started_at="2026-10-05T00:00:00+00:00", status="partial", stages=stages
    )
    _write_manifest(tmp_path / "records", "other/run1", deployment="other", started_at="2026-10-05T00:00:00+00:00")

    status = list_latest_per_deployment(tmp_path, sink, deployment=None, diagnostics=Diagnostics())
    changes = list_manifests_in_range(
        tmp_path, sink, deployment=None, since=None, until=None, status=None, diagnostics=Diagnostics()
    )

    assert {r.deployment: r.failed_step for r in status} == {"app": "apply_apps", "other": None}
    assert {r.deployment: r.failed_step for r in changes} == {"app": "apply_apps", "other": None}


def test_the_status_cell_names_the_failed_step():
    from strata.commands.audit_command import _status_cell

    record = DeploymentStatusRecord(
        deployment="app",
        status="partial",
        action="deploy",
        version="2.8.0",
        deployed_by="ci",
        completed_at="t",
        change_reference=None,
        failed_step="apply_apps",
    )
    assert _status_cell(record) == "partial (apply_apps)"
    assert _status_cell(replace(record, status="success", failed_step=None)) == "success"


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
# _parse_range_bound — a third zero-I/O "tell the user first" check
# ---------------------------------------------------------------------------


def test_parse_range_bound_returns_none_for_an_omitted_flag():
    assert _parse_range_bound(None, "--since") is None


def test_parse_range_bound_keeps_an_already_aware_datetime():
    result = _parse_range_bound("2026-10-01T00:00:00+00:00", "--since")
    assert result == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_parse_range_bound_promotes_a_naive_date_to_utc():
    """The exact bug this design calls out: a bare '--since 2026-10-01'
    parses naive, and must not be left to crash later when compared
    against an aware `started_at`."""
    result = _parse_range_bound("2026-10-01", "--since")
    assert result == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert result is not None
    assert result.tzinfo is not None


def test_parse_range_bound_raises_usage_error_for_a_malformed_value():
    with pytest.raises(UsageError, match="'--since' is not a valid ISO-8601"):
        _parse_range_bound("not-a-date", "--since")


def test_parse_range_bound_names_the_flag_it_was_given():
    with pytest.raises(UsageError, match="'--until' is not a valid"):
        _parse_range_bound("not-a-date", "--until")


def test_parse_range_bound_end_of_day_pushes_a_bare_date_to_the_last_microsecond():
    """Found during review: '--until 2026-10-04' without end_of_day parses to
    that day's *midnight*, silently excluding every record later that same
    day — contradicting the CLI's own 'inclusive' claim. end_of_day=True
    (what audit_changes() actually passes for --until) must push a bare date
    to 23:59:59.999999, not leave it at 00:00:00."""
    result = _parse_range_bound("2026-10-04", "--until", end_of_day=True)
    assert result == datetime(2026, 10, 4, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_parse_range_bound_end_of_day_does_not_affect_an_explicit_time():
    """A user who already gave a specific time meant that exact moment —
    end_of_day must not override an explicit, non-midnight time component."""
    result = _parse_range_bound("2026-10-04T08:00:00+00:00", "--until", end_of_day=True)
    assert result == datetime(2026, 10, 4, 8, 0, 0, tzinfo=timezone.utc)


def test_parse_range_bound_without_end_of_day_keeps_midnight():
    """--since has no equivalent bug — 'since the start of this day' is
    already the intuitive, correct reading of a bare date, so end_of_day
    defaults to False and must not be applied unless explicitly requested."""
    result = _parse_range_bound("2026-10-04", "--since")
    assert result == datetime(2026, 10, 4, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# list_manifests_in_range — pure filesystem, mirrors list_latest_per_deployment's
# own test group but with no grouping and three extra filters
# ---------------------------------------------------------------------------


def test_list_manifests_in_range_returns_every_record_not_just_the_latest(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-01T00:00:00+00:00")
    _write_manifest(tmp_path / "records", "app/run2", deployment="app", started_at="2026-10-05T00:00:00+00:00")

    records = list_manifests_in_range(
        tmp_path, sink, deployment=None, since=None, until=None, status=None, diagnostics=Diagnostics()
    )

    assert len(records) == 2  # unlike list_latest_per_deployment, neither is dropped


def test_list_manifests_in_range_sorts_oldest_to_newest(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-05T00:00:00+00:00")
    _write_manifest(tmp_path / "records", "app/run2", deployment="app", started_at="2026-10-01T00:00:00+00:00")

    records = list_manifests_in_range(
        tmp_path, sink, deployment=None, since=None, until=None, status=None, diagnostics=Diagnostics()
    )

    assert [r.started_at for r in records] == ["2026-10-01T00:00:00+00:00", "2026-10-05T00:00:00+00:00"]


def test_list_manifests_in_range_filters_by_since_and_until(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-01T00:00:00+00:00")
    _write_manifest(tmp_path / "records", "app/run2", deployment="app", started_at="2026-10-03T00:00:00+00:00")
    _write_manifest(tmp_path / "records", "app/run3", deployment="app", started_at="2026-10-05T00:00:00+00:00")

    records = list_manifests_in_range(
        tmp_path,
        sink,
        deployment=None,
        since=datetime(2026, 10, 2, tzinfo=timezone.utc),
        until=datetime(2026, 10, 4, tzinfo=timezone.utc),
        status=None,
        diagnostics=Diagnostics(),
    )

    assert [r.started_at for r in records] == ["2026-10-03T00:00:00+00:00"]


def test_list_manifests_in_range_filters_by_status(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(
        tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-01T00:00:00+00:00", status="success"
    )
    _write_manifest(
        tmp_path / "records", "app/run2", deployment="app", started_at="2026-10-02T00:00:00+00:00", status="failed"
    )

    records = list_manifests_in_range(
        tmp_path, sink, deployment=None, since=None, until=None, status="failed", diagnostics=Diagnostics()
    )

    assert len(records) == 1
    assert records[0].status == "failed"


def test_list_manifests_in_range_filters_by_deployment_name(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-01T00:00:00+00:00")
    _write_manifest(tmp_path / "records", "other/run1", deployment="other", started_at="2026-10-01T00:00:00+00:00")

    records = list_manifests_in_range(
        tmp_path, sink, deployment="app", since=None, until=None, status=None, diagnostics=Diagnostics()
    )

    assert [r.deployment for r in records] == ["app"]


def test_list_manifests_in_range_combines_all_filters(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(
        tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-01T00:00:00+00:00", status="failed"
    )
    _write_manifest(
        tmp_path / "records", "app/run2", deployment="app", started_at="2026-10-05T00:00:00+00:00", status="failed"
    )
    _write_manifest(
        tmp_path / "records", "app/run3", deployment="app", started_at="2026-10-05T00:00:00+00:00", status="success"
    )
    _write_manifest(
        tmp_path / "records", "other/run1", deployment="other", started_at="2026-10-05T00:00:00+00:00", status="failed"
    )

    records = list_manifests_in_range(
        tmp_path,
        sink,
        deployment="app",
        since=datetime(2026, 10, 3, tzinfo=timezone.utc),
        until=None,
        status="failed",
        diagnostics=Diagnostics(),
    )

    assert len(records) == 1
    assert records[0].deployment == "app"
    assert records[0].started_at == "2026-10-05T00:00:00+00:00"
    assert records[0].status == "failed"


def test_list_manifests_in_range_skips_a_corrupted_manifest_with_a_warning(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-05T00:00:00+00:00")
    corrupt = tmp_path / "records" / "app" / "run2" / "_manifest.json"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("{not valid json", encoding="utf-8")

    diagnostics = Diagnostics()
    records = list_manifests_in_range(
        tmp_path, sink, deployment=None, since=None, until=None, status=None, diagnostics=diagnostics
    )

    assert len(records) == 1
    assert diagnostics.ok
    assert any(d.code == "audit_read_manifest_unparseable" for d in diagnostics.items)


def test_list_manifests_in_range_named_deployment_with_no_match_is_not_an_error(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-05T00:00:00+00:00")

    diagnostics = Diagnostics()
    records = list_manifests_in_range(
        tmp_path, sink, deployment="never-deployed", since=None, until=None, status=None, diagnostics=diagnostics
    )

    assert records == []
    assert diagnostics.ok
    assert any(d.code == "audit_read_no_records_for_deployment" for d in diagnostics.items)


def test_list_manifests_in_range_no_match_at_all_is_not_an_error(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    (tmp_path / "records").mkdir()

    diagnostics = Diagnostics()
    records = list_manifests_in_range(
        tmp_path, sink, deployment=None, since=None, until=None, status=None, diagnostics=diagnostics
    )

    assert records == []
    assert diagnostics.ok
    assert any(d.code == "audit_read_no_records" for d in diagnostics.items)


def test_list_manifests_in_range_filters_excluding_everything_is_not_a_sink_is_empty_claim(tmp_path: Path):
    """Found during review: the sink genuinely has a record — 'status=failed'
    just excludes it. The message must say the filters matched nothing, not
    claim there's nothing in the sink at all (actively misleading otherwise)."""
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(
        tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-05T00:00:00+00:00", status="success"
    )

    diagnostics = Diagnostics()
    records = list_manifests_in_range(
        tmp_path, sink, deployment=None, since=None, until=None, status="failed", diagnostics=diagnostics
    )

    assert records == []
    assert diagnostics.ok
    assert any(d.code == "audit_read_no_records_for_filters" for d in diagnostics.items)
    assert not any(d.code == "audit_read_no_records" for d in diagnostics.items)


# ---------------------------------------------------------------------------
# audit_changes — the malformed-date check must run before any sink/I-O check
# ---------------------------------------------------------------------------


def test_audit_changes_malformed_since_raises_before_touching_the_context():
    """`_parse_range_bound()` runs before `context` is touched at all — passing
    `None` instead of a real `SolutionContext` and still getting the date
    error (not an `AttributeError` on `context`) proves the ordering."""
    with pytest.raises(UsageError, match="'--since' is not a valid"):
        audit_changes(None, None, since="not-a-date", until=None, status=None)  # type: ignore[arg-type]


def test_audit_changes_malformed_until_raises_before_touching_the_context():
    with pytest.raises(UsageError, match="'--until' is not a valid"):
        audit_changes(None, None, since=None, until="not-a-date", status=None)  # type: ignore[arg-type]


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
@pytest.mark.slow
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
    older.write_text(_manifest_json(deployment="app", started_at="2026-10-01T00:00:00+00:00", status="failed"))
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

    # audit_changes() against the exact same pushed data — same checkout,
    # same three manifests, no second bare repo needed (docs/design/
    # audit-commands.md's Phase 6 plan)
    change_records, change_diagnostics = audit_changes(context, None, since=None, until=None, status=None)
    assert change_diagnostics.ok
    assert len(change_records) == 3  # unlike audit_status(), neither 'app' run is dropped
    assert [r.started_at for r in change_records] == [
        "2026-10-01T00:00:00+00:00",
        "2026-10-03T00:00:00+00:00",
        "2026-10-05T00:00:00+00:00",
    ]  # oldest to newest, not grouped by deployment

    failed_only, _ = audit_changes(context, None, since=None, until=None, status="failed")
    assert [r.deployment for r in failed_only] == ["app"]
    assert failed_only[0].started_at == "2026-10-01T00:00:00+00:00"

    in_range, _ = audit_changes(context, None, since="2026-10-02", until="2026-10-04", status=None)
    assert [r.deployment for r in in_range] == ["other"]

    with pytest.raises(UsageError, match="'--since' is not a valid"):
        audit_changes(context, None, since="not-a-date", until=None, status=None)

    no_match, no_match_diagnostics = audit_changes(context, None, since="2030-01-01", until=None, status=None)
    assert no_match == []
    assert no_match_diagnostics.ok
    # records DO exist in this sink — 'since' just excluded all of them, so the
    # message must say "no match for the filters", not "nothing in this sink"
    assert any(d.code == "audit_read_no_records_for_filters" for d in no_match_diagnostics.items)


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
@pytest.mark.slow
def test_real_audit_status_reads_a_sink_on_a_path_remote(tmp_path: Path):
    """A sink whose remote has a `path` (the pipeline's checkout) is read by
    cloning `url` at the sink's branch: the pipeline's folder is not there on
    this machine, and the remote's `fetch: external` does not stop the read."""
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)

    root = _solution_root(tmp_path, str(bare_repo))
    pushed = open_solution(root).require_valid()
    pusher = pushed.controller.solution
    assert pusher is not None
    sink = AuditGitSinkTargetModel(remote="audit-repo", branch="main", path="records")
    manifest = tmp_path / "run.json"
    manifest.write_text(_manifest_json(deployment="app", started_at="2026-10-05T00:00:00+00:00"))
    assert push_audit_files(root, sink, pusher, {"_manifest.json": manifest}, Path("run1"), "actor").success

    _write(
        root,
        "strata.yaml",
        MANIFEST_TEMPLATE.replace("reference: main", "path: repos/audit").format(remote_url=str(bare_repo)),
    )
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    context = context.require_valid()

    records, diagnostics = audit_status(context, None)

    assert diagnostics.ok, diagnostics.messages()
    assert [record.deployment for record in records] == ["app"]
    assert not (root / "repos" / "audit").exists()


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
@pytest.mark.slow
def test_real_audit_reads_report_a_missing_sink_branch_instead_of_the_default_branch(tmp_path: Path):
    """Records on `main` must not be shown as the `audit` branch's when nothing
    was ever pushed to `audit` (for example `action: commit`, not pushed yet)."""
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)
    root = _solution_root(tmp_path, str(bare_repo))
    context = open_solution(root).require_valid()
    solution = context.controller.solution
    assert solution is not None
    on_main = AuditGitSinkTargetModel(remote="audit-repo", branch="main", path="records")
    manifest = tmp_path / "run.json"
    manifest.write_text(_manifest_json(deployment="app", started_at="2026-10-05T00:00:00+00:00"))
    assert push_audit_files(root, on_main, solution, {"_manifest.json": manifest}, Path("run1"), "actor").success

    _write(root, "configuration.yaml", CONFIGURATION_TEMPLATE.replace("branch: main", "branch: audit"))
    context = open_solution(root).require_valid()

    records, diagnostics = audit_status(context, None)
    changes, change_diagnostics = audit_changes(context, None, since=None, until=None, status=None)

    assert (records, changes) == ([], [])
    assert any(d.code == "audit_read_branch_missing" for d in diagnostics.items)
    assert any(d.code == "audit_read_branch_missing" for d in change_diagnostics.items)


# ---------------------------------------------------------------------------
# Credentials: the sink remote's integration, resolved through a deployment
# ---------------------------------------------------------------------------

TOKEN_INTEGRATION = """apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: audit-git
spec:
  type: git
  capabilities: [sources]
  authentication:
    method: token
    token:
      token: AUDIT_GIT_TOKEN
      username: audit-bot
"""

TOKEN_ENVIRONMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: prd
spec:
  secrets:
    - key: AUDIT_GIT_TOKEN
      store: environment
      value: TEST_AUDIT_GIT_TOKEN
"""

TOKEN_DEPLOYMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: app
spec:
  partial: true
  environments:
    - prd
"""


def _credential_context(tmp_path: Path):
    root = _solution_root(tmp_path, "https://git.example.com/org/audit.git")
    _write(
        root,
        "strata.yaml",
        MANIFEST_TEMPLATE.format(remote_url="https://git.example.com/org/audit.git") + "      integration: audit-git\n",
    )
    _write(root, "integrations/audit-git.yaml", TOKEN_INTEGRATION)
    _write(root, "environments/prd.yaml", TOKEN_ENVIRONMENT)
    _write(root, "deployments/app.yaml", TOKEN_DEPLOYMENT)
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context.require_valid()


def _sink() -> AuditGitSinkTargetModel:
    return AuditGitSinkTargetModel(remote="audit-repo", branch="main", path="records")


def test_read_credentials_resolve_the_integration_through_the_named_deployment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEST_AUDIT_GIT_TOKEN", "ghp_audit_read_token")
    context = _credential_context(tmp_path)

    auth, values, hint = audit_read._read_credentials(context, _sink(), "app")

    assert auth is not None and auth.method == "token"
    assert values == {"AUDIT_GIT_TOKEN": "ghp_audit_read_token"}
    assert hint == ""


@pytest.mark.parametrize("deployment", [None, "not-a-deployment"])
def test_read_credentials_without_a_known_deployment_fall_back_to_ambient_with_a_hint(
    tmp_path: Path, deployment: str | None
):
    context = _credential_context(tmp_path)

    auth, values, hint = audit_read._read_credentials(context, _sink(), deployment)

    assert (auth, values) == (None, None)
    assert "integration 'audit-git'" in hint
    assert "strata audit status <deployment>" in hint


def test_read_credentials_fail_when_the_token_has_no_value(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TEST_AUDIT_GIT_TOKEN", raising=False)
    context = _credential_context(tmp_path)

    with pytest.raises(AuditReadError, match="could not resolve the credentials of integration 'audit-git'"):
        audit_read._read_credentials(context, _sink(), "app")


def test_read_credentials_are_ambient_for_a_remote_without_an_integration(tmp_path: Path):
    context = open_solution(_solution_root(tmp_path, "https://git.example.com/org/audit.git")).require_valid()

    assert audit_read._read_credentials(context, _sink(), "app") == (None, None, "")


def test_audit_status_passes_the_resolved_token_to_the_read(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEST_AUDIT_GIT_TOKEN", "ghp_audit_read_token")
    context = _credential_context(tmp_path)
    seen: list[tuple] = []

    def fake_sync(root, solution, sink, *, auth=None, resolved_values=None):
        seen.append((auth.method if auth else None, resolved_values))
        return None

    monkeypatch.setattr(audit_read, "sync_read_checkout", fake_sync)

    audit_status(context, "app")

    assert seen == [("token", {"AUDIT_GIT_TOKEN": "ghp_audit_read_token"})]


def test_a_failed_ambient_read_names_the_deployment_hint(tmp_path: Path, monkeypatch):
    context = _credential_context(tmp_path)

    def failing_sync(root, solution, sink, *, auth=None, resolved_values=None):
        raise AuditReadError("'git clone https://git.example.com/org/audit.git' failed: Authentication failed")

    monkeypatch.setattr(audit_read, "sync_read_checkout", failing_sync)

    with pytest.raises(AuditReadError, match=r"Authentication failed \(remote 'audit-repo' authenticates through"):
        audit_status(context, None)


# ---------------------------------------------------------------------------
# action (docs/design/deploy-destroy.md) - a destroy must not read as a deploy
# ---------------------------------------------------------------------------


def test_the_latest_record_reports_its_action(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-01T00:00:00+00:00")
    _write_manifest(
        tmp_path / "records", "app/run2", deployment="app", started_at="2026-10-05T00:00:00+00:00", action="destroy"
    )

    records = list_latest_per_deployment(tmp_path, sink, deployment=None, diagnostics=Diagnostics())

    assert [(record.status, record.action) for record in records] == [("success", "destroy")]


def test_a_deploy_record_reports_the_deploy_action(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-01T00:00:00+00:00")

    records = list_latest_per_deployment(tmp_path, sink, deployment=None, diagnostics=Diagnostics())

    assert [record.action for record in records] == ["deploy"]


def test_changes_report_each_records_action_oldest_to_newest(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(
        tmp_path / "records", "app/run2", deployment="app", started_at="2026-10-05T00:00:00+00:00", action="destroy"
    )
    _write_manifest(tmp_path / "records", "app/run1", deployment="app", started_at="2026-10-01T00:00:00+00:00")

    records = list_manifests_in_range(
        tmp_path, sink, deployment=None, since=None, until=None, status=None, diagnostics=Diagnostics()
    )

    assert [record.action for record in records] == ["deploy", "destroy"]


def test_list_latest_per_deployment_carries_the_commit_sha(tmp_path: Path):
    sink = _git_sink().git
    assert sink is not None
    _write_manifest(tmp_path / "records", "app/1", started_at="2026-10-05T14:00:00+00:00", commit_sha="a" * 40)

    records = list_latest_per_deployment(tmp_path, sink, deployment=None, diagnostics=Diagnostics())

    assert [record.commit_sha for record in records] == ["a" * 40]
