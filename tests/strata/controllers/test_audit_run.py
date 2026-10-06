#!/usr/bin/env python3
"""Tests for `audit_run.finalize_and_distribute_deploy_audit()` (docs/design/
audit-trail.md's "Write once locally, then distribute").

Phase 5 of that doc's Layer 2 Implementation Plan — the final phase. Real
solution fixtures (same shape as `test_deploy_controller.py`'s own), a real
local bare git repository for the `git` sink dispatch tests, and mocked
`terraform`/`run_command` so no real binaries are needed.
"""

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strata.controllers import audit_run as audit_run_module
from strata.controllers.audit_push import PushResult
from strata.controllers.audit_run import finalize_and_distribute_deploy_audit
from strata.controllers.build_controller import build_run
from strata.controllers.solution_context import open_solution
from strata.integrations.capabilities import AuditSinkIntegration
from strata.integrations.errors import IntegrationError
from strata.models.integration_model import Capability
from strata.utils import layout
from strata.utils.diagnostics import Diagnostics
from strata.utils.transport import CommandResult

MANIFEST_TEMPLATE = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
{remotes}
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solution_root(tmp_path: Path, *, remote_url: str | None = None) -> Path:
    root = tmp_path / "sln"
    remotes = "  {}"
    if remote_url is not None:
        remotes = (
            f"  remotes:\n    - name: config-repo\n      type: git\n      url: {remote_url}\n      reference: main"
        )
    _write(root, "strata.yaml", MANIFEST_TEMPLATE.format(remotes=remotes))
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n      scope: infra\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


def _add_configuration(root: Path, audit_spec_yaml: str = "") -> None:
    _write(
        root,
        "configuration.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: configuration\nmeta:\n  name: cfg\nspec:\n" + audit_spec_yaml,
    )


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context.require_valid()


@pytest.fixture
def _terraform_stub(monkeypatch):
    """Stub `terraform` only — `shutil.which` is a shared module attribute
    (patching it wholesale, as `test_deploy_controller.py`'s own fixture
    does, would also break this file's *real* `git` subprocess calls for
    the audit push tests, since both modules import the same `shutil`)."""

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import shutil as real_shutil

    import strata.integrations.base as base_module

    real_which = real_shutil.which

    def _fake_which(cmd: str) -> str | None:
        return f"/usr/bin/{cmd}" if cmd == "terraform" else real_which(cmd)

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", _fake_which)


def _run_deploy_and_build(root: Path, tmp_path: Path) -> Path:
    build_path = tmp_path / "build"
    build_run(_context(root), "app", build_path)
    return build_path


def _finalize(root: Path, build_path: Path, *, run_ok: bool = True, dry_run: bool = False, **change_kwargs):
    context = _context(root)
    started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    run_diagnostics = Diagnostics()
    if not run_ok:
        run_diagnostics.error("a step failed")
    return finalize_and_distribute_deploy_audit(
        context,
        "app",
        build_path,
        execution_id="11111111-1111-1111-1111-111111111111",
        started_at=started_at,
        run_diagnostics=run_diagnostics,
        dry_run=dry_run,
        **change_kwargs,
    )


# ---------------------------------------------------------------------------
# Zero-config default
# ---------------------------------------------------------------------------


def test_zero_config_writes_manifest_and_metrics_locally(tmp_path: Path, _terraform_stub):
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    diagnostics = _finalize(root, build_path)

    assert diagnostics.ok
    audit_dir = layout.audit_dir(root)
    manifest_files = list(audit_dir.rglob("_manifest.json"))
    metrics_files = list(audit_dir.rglob("_metrics.json"))
    assert len(manifest_files) == 1
    assert len(metrics_files) == 1
    assert manifest_files[0].parent.parent == layout.audit_dir(root) / "main" / "app"


def test_zero_config_manifest_reflects_run_outcome(tmp_path: Path, _terraform_stub):
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(root, build_path, run_ok=False)

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    assert '"status": "failed"' in manifest_path.read_text()


def test_dry_run_writes_nothing(tmp_path: Path, _terraform_stub):
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    diagnostics = _finalize(root, build_path, dry_run=True)

    assert diagnostics.ok
    assert not layout.audit_dir(root).exists()


def test_missing_resolved_yaml_warns_and_skips(tmp_path: Path, _terraform_stub):
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)
    (build_path / "resolved.yaml").unlink()

    diagnostics = _finalize(root, build_path)

    assert diagnostics.ok  # a warning, not an error
    assert any("resolved.yaml" in w.message for w in diagnostics.warnings)
    assert not layout.audit_dir(root).exists()


def test_manifest_includes_sbom_reference_when_present(tmp_path: Path, _terraform_stub):
    """`build run` writes a real `sbom.json` unconditionally (gap #18 Phase 1)
    — the manifest's `sbom` reference should pick it up, re-hashed fresh."""
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)
    assert (build_path / "sbom.json").is_file()  # confirms the fixture actually exercises this path

    _finalize(root, build_path)

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    manifest_text = manifest_path.read_text()
    assert '"path": "sbom.json"' in manifest_text
    assert '"format": "cyclonedx-1.6"' in manifest_text
    assert '"sha256": "sha256:' in manifest_text


# ---------------------------------------------------------------------------
# artifacts.repositories/.images/.charts (docs/work/version-lifecycle.md
# Phase 6 — the audit gap: effective, pin-resolved values, not the SBOM's
# own index-sourced (never pin-overlaid) components).
# ---------------------------------------------------------------------------


def _solution_with_workload(
    tmp_path: Path, *, version_doc: str = "", reference_version_from_deployment: bool = True
) -> Path:
    """Like `_solution_root()`, but the workspace also reaches a chart-based
    (helm) module and an image-based (compose) module through one
    namespace, plus a `fetch: strata` remote the chart-based module's
    source names — enough surface to exercise all three new BOM fields at
    once."""
    root = tmp_path / "sln"
    _write(
        root,
        "strata.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec:\n"
        "  remotes:\n    - name: infra-remote\n      type: git\n"
        "      url: https://example.com/org/infra.git\n      reference: main\n",
    )
    _write(root, "infra/main.tf", "# root module\n")
    _write(root, "charts/authentik/Chart.yaml", "name: authentik\n")
    _write(root, "services/redis/docker-compose.yml", "# stand-in\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  properties:\n    type: local\n    region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  properties:\n    provider_type: local\n    resource_type: server\n    category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "module-authentik.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: authentik\nspec:\n"
        "  source:\n    remote: infra-remote\n    chart_name: authentik\n    chart_version: 2024.1.0\n"
        "  type: helm\n  default_labels:\n    app: authentik\n  services:\n    - name: server\n",
    )
    _write(
        root,
        "module-redis.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: module\nmeta:\n  name: redis\nspec:\n"
        "  source:\n    source_path: services/redis\n  type: compose\n"
        "  default_labels:\n    app: redis\n  services:\n    - name: redis\n      image: redis:7\n",
    )
    _write(
        root,
        "namespace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: namespace\nmeta:\n  name: apps\nspec:\n"
        "  default_labels:\n    app: apps\n"
        "  modules:\n    - name: auth\n      module: authentik\n    - name: redis\n      module: redis\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  namespaces:\n    - apps\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      targets:\n        - r1\n      scope: infra\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    version_line = "  version: prd\n" if (version_doc and reference_version_from_deployment) else ""
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        f"  workspace: main\n{version_line}  environments:\n    - prd\n",
    )
    if version_doc:
        _write(root, "version.yaml", version_doc)
    return root


_WORKLOAD_VERSION_DOC = (
    "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n"
    "  pins:\n"
    "    remotes:\n      infra-remote: v2.0.0\n"
    "    charts:\n      authentik: 2024.2.0\n"
    "    images:\n      redis: redis:7.2\n"
)


def test_manifest_records_effective_repositories_images_and_charts(tmp_path: Path, _terraform_stub):
    root = _solution_with_workload(tmp_path, version_doc=_WORKLOAD_VERSION_DOC)
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(root, build_path)

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    import json as _json

    manifest = _json.loads(manifest_path.read_text())
    artifacts = manifest["artifacts"]
    assert artifacts["repositories"]["infra-remote"]["ref"] == "v2.0.0"
    assert {"name": "redis", "image": "redis:7.2"} in artifacts["images"]
    assert {"name": "authentik", "chart": "authentik", "version": "2024.2.0"} in artifacts["charts"]


def test_manifest_without_any_pin_records_the_declared_values(tmp_path: Path, _terraform_stub):
    """Control case: with no `kind: version` document at all, the manifest
    still records every reachable target's own declared value — "the
    effective value for every actually-deployed target, not only pinned
    ones" (this doc's own Phase 6 requirement)."""
    root = _solution_with_workload(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(root, build_path)

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    import json as _json

    manifest = _json.loads(manifest_path.read_text())
    artifacts = manifest["artifacts"]
    assert artifacts["repositories"]["infra-remote"]["ref"] == "main"
    assert {"name": "redis", "image": "redis:7"} in artifacts["images"]
    assert {"name": "authentik", "chart": "authentik", "version": "2024.1.0"} in artifacts["charts"]


def test_manifest_pin_records_the_pinned_effective_values_not_the_on_disk_ones(tmp_path: Path, _terraform_stub):
    """`--pin`'s own "Done when" extended to the audit record: the manifest
    reflects what was *actually* deployed (the pin), not `spec.version` as
    hand-written on disk (absent here entirely)."""
    root = _solution_with_workload(tmp_path, version_doc=_WORKLOAD_VERSION_DOC, reference_version_from_deployment=False)
    assert "version" not in (root / "deployment.yaml").read_text(encoding="utf-8")
    build_path = _run_deploy_and_build(root, tmp_path)

    context = _context(root)
    started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    finalize_and_distribute_deploy_audit(
        context,
        "app",
        build_path,
        execution_id="11111111-1111-1111-1111-111111111111",
        started_at=started_at,
        run_diagnostics=Diagnostics(),
        pin="prd",
    )

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    import json as _json

    manifest = _json.loads(manifest_path.read_text())
    artifacts = manifest["artifacts"]
    assert artifacts["repositories"]["infra-remote"]["ref"] == "v2.0.0"
    assert {"name": "redis", "image": "redis:7.2"} in artifacts["images"]
    assert {"name": "authentik", "chart": "authentik", "version": "2024.2.0"} in artifacts["charts"]


def test_manifest_artifacts_bom_absent_when_workspace_reaches_nothing(tmp_path: Path, _terraform_stub):
    """The base `_solution_root()` fixture (no namespaces/modules/remotes
    reachable) must not regress — repositories/images/charts stay unset,
    exactly as before this phase, not empty lists/dicts."""
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(root, build_path)

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    manifest_text = manifest_path.read_text()
    assert '"repositories"' not in manifest_text
    assert '"images"' not in manifest_text
    assert '"charts"' not in manifest_text


# ---------------------------------------------------------------------------
# change_reference (docs/design/audit-trail.md's ChangeReferenceModel CLI wiring)
# ---------------------------------------------------------------------------


def test_change_reference_absent_by_default(tmp_path: Path, _terraform_stub):
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(root, build_path)

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    assert "change_reference" not in manifest_path.read_text()


def test_change_reference_populated_when_system_id_reason_all_given(tmp_path: Path, _terraform_stub):
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(
        root,
        build_path,
        change_system="jira",
        change_id="OPS-1234",
        change_reason="planned maintenance",
        change_classification="normal",
    )

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    manifest_text = manifest_path.read_text()
    assert '"system": "jira"' in manifest_text
    assert '"id": "OPS-1234"' in manifest_text
    assert '"reason": "planned maintenance"' in manifest_text
    assert '"classification": "normal"' in manifest_text


def test_change_reference_supplied_by_and_at_are_auto_derived_not_accepted_as_input(tmp_path: Path, _terraform_stub):
    """`supplied_by`/`supplied_at` are never CLI-supplied — always the run's
    own actor/clock, same pattern `deployed_by`/`started_at` already use.
    The actual value is environment-dependent (`resolve_actor()`'s own
    chain) — only presence is asserted, matching how `deployed_by` itself
    is never asserted against a literal elsewhere in this file."""
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(root, build_path, change_system="jira", change_id="OPS-1234", change_reason="planned maintenance")

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    manifest_text = manifest_path.read_text()
    assert '"supplied_by": "' in manifest_text
    assert '"supplied_at": "' in manifest_text


def test_change_reference_approved_by_and_at_are_populated_when_given(tmp_path: Path, _terraform_stub):
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(
        root,
        build_path,
        change_system="jira",
        change_id="OPS-1234",
        change_reason="planned maintenance",
        change_approved_by="jsmith",
        change_approved_at="2026-10-04T09:00:00+00:00",
    )

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    manifest_text = manifest_path.read_text()
    assert '"approved_by": "jsmith"' in manifest_text
    assert '"approved_at": "2026-10-04T09:00:00+00:00"' in manifest_text


def test_change_reference_approval_fields_absent_by_default(tmp_path: Path, _terraform_stub):
    """Approval is opt-in — a change_reference with no approval info supplied
    writes no approved_by/approved_at at all (exclude_none on write)."""
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(root, build_path, change_system="jira", change_id="OPS-1234", change_reason="planned maintenance")

    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    manifest_text = manifest_path.read_text()
    assert "approved_by" not in manifest_text
    assert "approved_at" not in manifest_text


def test_manifest_omits_sbom_reference_without_error_when_absent(tmp_path: Path, _terraform_stub):
    """Unlike `resolved.yaml`, a missing SBOM is not an error — SBOM
    generation is itself optional."""
    root = _solution_root(tmp_path)
    build_path = _run_deploy_and_build(root, tmp_path)
    (build_path / "sbom.json").unlink()

    diagnostics = _finalize(root, build_path)

    assert diagnostics.ok
    assert not diagnostics.warnings
    manifest_path = next(layout.audit_dir(root).rglob("_manifest.json"))
    assert '"sbom"' not in manifest_path.read_text()


# ---------------------------------------------------------------------------
# event_overrides gate
# ---------------------------------------------------------------------------


def test_event_override_disables_measured_dispatch_but_not_local_write(tmp_path: Path, _terraform_stub, monkeypatch):
    root = _solution_root(tmp_path, remote_url="https://example.invalid/does-not-matter.git")
    _add_configuration(
        root,
        "  audit:\n    event_overrides:\n      deployment.measured: false\n"
        "    sinks:\n      - name: config-repo\n        git:\n          remote: config-repo\n"
        "          branch: main\n          path: audit\n        events: [deployment.completed]\n",
    )
    build_path = _run_deploy_and_build(root, tmp_path)

    captured_files = {}

    def _fake_push(root_arg, sink, solution, files, relative_path, actor):
        captured_files.update(files)
        return PushResult(True)

    monkeypatch.setattr(audit_run_module, "push_audit_files", _fake_push)

    _finalize(root, build_path)

    # Metrics file is still written locally...
    assert len(list(layout.audit_dir(root).rglob("_metrics.json"))) == 1
    # ...but not included in what gets pushed, since the sink's own filter
    # only names deployment.completed anyway (measured already gated off too).
    assert "_metrics.json" not in captured_files
    assert "_manifest.json" in captured_files


# ---------------------------------------------------------------------------
# git sink dispatch — real local bare repository
# ---------------------------------------------------------------------------


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=False)
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_configured_git_sink_pushes_to_real_bare_repo(tmp_path: Path, _terraform_stub):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)

    root = _solution_root(tmp_path, remote_url=str(bare_repo))
    _add_configuration(
        root,
        "  audit:\n    sinks:\n      - name: config-repo\n        git:\n          remote: config-repo\n"
        "          branch: main\n          path: audit\n        events: [deployment.completed, deployment.measured]\n",
    )
    build_path = _run_deploy_and_build(root, tmp_path)

    diagnostics = _finalize(root, build_path)

    assert not diagnostics.warnings, diagnostics.messages()
    assert diagnostics.ok, diagnostics.messages()
    verify_dir = tmp_path / "verify"
    subprocess.run(["git", "clone", str(bare_repo), str(verify_dir)], check=True, capture_output=True)
    pushed = list((verify_dir / "audit").rglob("_manifest.json"))
    assert len(pushed) == 1
    assert list((verify_dir / "audit").rglob("_metrics.json"))


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_push_failure_warns_when_not_required(tmp_path: Path, _terraform_stub, monkeypatch):
    root = _solution_root(tmp_path, remote_url="https://example.invalid/does-not-exist.git")
    _add_configuration(
        root,
        "  audit:\n    sinks:\n      - name: config-repo\n        required: false\n        git:\n"
        "          remote: config-repo\n          branch: main\n          path: audit\n"
        "        events: [deployment.completed]\n",
    )
    build_path = _run_deploy_and_build(root, tmp_path)

    monkeypatch.setattr(audit_run_module, "push_audit_files", lambda *a, **k: PushResult(False, "network down"))

    diagnostics = _finalize(root, build_path)

    assert diagnostics.ok  # warning only, command still succeeds
    assert any("network down" in w.message for w in diagnostics.warnings)


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_push_failure_fails_command_when_required(tmp_path: Path, _terraform_stub, monkeypatch):
    root = _solution_root(tmp_path, remote_url="https://example.invalid/does-not-exist.git")
    _add_configuration(
        root,
        "  audit:\n    sinks:\n      - name: config-repo\n        required: true\n        git:\n"
        "          remote: config-repo\n          branch: main\n          path: audit\n"
        "        events: [deployment.completed]\n",
    )
    build_path = _run_deploy_and_build(root, tmp_path)

    monkeypatch.setattr(audit_run_module, "push_audit_files", lambda *a, **k: PushResult(False, "network down"))

    diagnostics = _finalize(root, build_path)

    assert not diagnostics.ok
    assert any("network down" in e.message for e in diagnostics.errors)


# ---------------------------------------------------------------------------
# integration sink — not dispatched, explicit info finding
# ---------------------------------------------------------------------------


def _add_webhook_integration(root: Path, *, name: str = "audit-hook", integration_type: str = "webhook") -> None:
    _write(
        root,
        f"config/integrations/{name}.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: {name}\nspec:\n"
        f"  type: {integration_type}\n  capabilities: [audit]\n  required: false\n  enabled: true\n"
        "  endpoints:\n    address: https://siem.example.com/collect\n",
    )


class _FakeSink(AuditSinkIntegration):
    """Records what it was asked to send; never touches the network."""

    TYPE = "webhook"
    CAPABILITIES = frozenset({Capability.AUDIT})
    TRANSPORTS = frozenset({"http"})

    sent: list[dict] = []
    fail_with: str | None = None

    def send(self, event: dict) -> None:
        if type(self).fail_with is not None:
            raise IntegrationError(type(self).fail_with)
        type(self).sent.append(event)


@pytest.fixture
def _fake_sink(monkeypatch):
    _FakeSink.sent = []
    _FakeSink.fail_with = None
    monkeypatch.setattr(audit_run_module.registry, "get", lambda t, c=None: _FakeSink(c))
    return _FakeSink


def test_integration_sink_dispatches_rendered_event(tmp_path: Path, _terraform_stub, _fake_sink):
    root = _solution_root(tmp_path)
    _add_webhook_integration(root)
    _add_configuration(
        root,
        "  audit:\n    sinks:\n      - name: prod-siem\n        integration: audit-hook\n"
        "        events: [deployment.completed]\n",
    )
    build_path = _run_deploy_and_build(root, tmp_path)

    diagnostics = _finalize(root, build_path)

    assert diagnostics.ok
    assert len(_fake_sink.sent) == 1
    event = _fake_sink.sent[0]
    assert event["type"] == "xyz.huybrechts.strata.deployment.completed"
    assert event["specversion"] == "1.0"
    assert event["data"]["manifest"]["path"].endswith("_manifest.json")
    assert event["data"]["manifest"]["sha256"].startswith("sha256:")


def test_integration_sink_respects_event_filter(tmp_path: Path, _terraform_stub, _fake_sink):
    """No `events` filter means every admitted type — both events dispatch."""
    root = _solution_root(tmp_path)
    _add_webhook_integration(root)
    _add_configuration(root, "  audit:\n    sinks:\n      - name: prod-siem\n        integration: audit-hook\n")
    build_path = _run_deploy_and_build(root, tmp_path)

    _finalize(root, build_path)

    sent_types = sorted(e["type"] for e in _fake_sink.sent)
    assert sent_types == [
        "xyz.huybrechts.strata.deployment.completed",
        "xyz.huybrechts.strata.deployment.measured",
    ]


def test_integration_sink_send_failure_warns_when_not_required(tmp_path: Path, _terraform_stub, _fake_sink):
    root = _solution_root(tmp_path)
    _add_webhook_integration(root)
    _add_configuration(
        root,
        "  audit:\n    sinks:\n      - name: prod-siem\n        integration: audit-hook\n"
        "        events: [deployment.completed]\n",
    )
    build_path = _run_deploy_and_build(root, tmp_path)
    _fake_sink.fail_with = "endpoint refused the connection"

    diagnostics = _finalize(root, build_path)

    assert diagnostics.ok  # a warning never fails the run
    assert any("endpoint refused" in w.message for w in diagnostics.warnings)


def test_integration_sink_send_failure_fails_run_when_required(tmp_path: Path, _terraform_stub, _fake_sink):
    root = _solution_root(tmp_path)
    _add_webhook_integration(root)
    _add_configuration(
        root,
        "  audit:\n    sinks:\n      - name: prod-siem\n        integration: audit-hook\n"
        "        required: true\n        events: [deployment.completed]\n",
    )
    build_path = _run_deploy_and_build(root, tmp_path)
    _fake_sink.fail_with = "endpoint refused the connection"

    diagnostics = _finalize(root, build_path)

    assert not diagnostics.ok
    assert any("endpoint refused" in e.message for e in diagnostics.errors)


def test_integration_sink_without_audit_capability_is_reported(tmp_path: Path, _terraform_stub, monkeypatch):
    """A terraform Integration wired to an audit sink is a config error, not a crash."""
    root = _solution_root(tmp_path)
    _write(
        root,
        "config/integrations/tf.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: tf-tool\nspec:\n"
        "  type: terraform\n  capabilities: [infrastructure]\n  enabled: true\n",
    )
    _add_configuration(
        root,
        "  audit:\n    sinks:\n      - name: prod-siem\n        integration: tf-tool\n"
        "        events: [deployment.completed]\n",
    )
    build_path = _run_deploy_and_build(root, tmp_path)

    diagnostics = _finalize(root, build_path)

    assert diagnostics.ok  # not required -> warning
    assert any("does not provide" in w.message and "audit" in w.message for w in diagnostics.warnings)


def test_disabled_integration_document_dispatches_nothing(tmp_path: Path, _terraform_stub, _fake_sink):
    """`spec.enabled: false` on the Integration is a deliberate off-switch, not a failure."""
    root = _solution_root(tmp_path)
    _write(
        root,
        "config/integrations/audit-hook.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: integration\nmeta:\n  name: audit-hook\nspec:\n"
        "  type: webhook\n  capabilities: [audit]\n  enabled: false\n"
        "  endpoints:\n    address: https://siem.example.com/collect\n",
    )
    _add_configuration(
        root,
        "  audit:\n    sinks:\n      - name: prod-siem\n        integration: audit-hook\n"
        "        events: [deployment.completed]\n",
    )
    build_path = _run_deploy_and_build(root, tmp_path)

    diagnostics = _finalize(root, build_path)

    assert diagnostics.ok
    assert not diagnostics.warnings
    assert _fake_sink.sent == []
