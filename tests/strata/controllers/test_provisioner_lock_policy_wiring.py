#!/usr/bin/env python3
"""`provisioner_lock_policy` in `build_run()` and `strata policy` (docs/design/provisioner-lock-policy.md, phase 5)."""

from pathlib import Path

import pytest

from strata.controllers.build_controller import build_run
from strata.controllers.policy_controller import check_policies, list_policies
from strata.controllers.policy_results import read_policy_results
from strata.controllers.solution_context import open_solution
from strata.utils.transport import CommandResult

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"
_AZURERM = (
    'terraform {\n  required_providers {\n    azurerm = {\n      source  = "hashicorp/azurerm"\n'
    '      version = "~> 4.0"\n    }\n  }\n}\n'
)
_LOCK = 'provider "registry.terraform.io/hashicorp/azurerm" {\n  version = "4.12.0"\n}\n'


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path, *, policy: str = "  provisioner_lock_policy: {}\n", locked: bool = False) -> Path:
    """One Terraform provisioner whose source declares azurerm, with or without a lock file."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    (root / "infra").mkdir()
    (root / "infra" / "main.tf").write_text(_AZURERM, encoding="utf-8")
    if locked:
        (root / "infra" / ".terraform.lock.hcl").write_text(_LOCK, encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "resource.yaml",
        "resource",
        "r1",
        "  provider_type: local\n  resource_type: server\n  category: compute\n  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n" + policy,
    )
    _write(root, "environment.yaml", "environment", "prd", "  variables: []\n")
    _write(root, "deployment.yaml", "deployment", "app", "  workspace: main\n  environments:\n    - prd\n")
    return root


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


@pytest.fixture(autouse=True)
def terraform(monkeypatch):
    def _run(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=0, stdout="", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _run)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")


def _codes(diagnostics) -> list[str | None]:
    return [d.code for d in diagnostics.errors + diagnostics.warnings]


def _recorded(build_path: Path):
    return {r.policy_name: r for r in read_policy_results(build_path) or []}.get("provisioner_lock_policy")


def test_build_run_fails_on_a_missing_lock_file_and_records_it(tmp_path):
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(_solution(tmp_path)), "app", build_path)

    assert not diagnostics.ok
    assert _codes(diagnostics) == ["provisioner_lock_missing"]
    recorded = _recorded(build_path)
    assert (recorded.policy_type, recorded.phase, recorded.enforcement, recorded.passed) == (
        "provisioner_lock",
        "build",
        "deny",
        False,
    )
    assert "tf_main" in recorded.violations[0]


def test_build_run_continues_on_a_warn_breach(tmp_path):
    build_path = tmp_path / "build"
    root = _solution(tmp_path, policy="  provisioner_lock_policy:\n    enforcement: warn\n")

    diagnostics = build_run(_context(root), "app", build_path)

    assert diagnostics.ok
    assert _codes(diagnostics) == ["provisioner_lock_missing"]
    recorded = _recorded(build_path)
    assert (recorded.passed, len(recorded.violations)) == (True, 1)


def test_build_run_passes_with_a_complete_lock_file(tmp_path):
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(_solution(tmp_path, locked=True)), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    assert (_recorded(build_path).passed, _recorded(build_path).violations) == (True, [])


def test_without_the_policy_build_run_only_notes_the_missing_lock_file(tmp_path):
    build_path = tmp_path / "build"

    diagnostics = build_run(_context(_solution(tmp_path, policy="")), "app", build_path)

    assert diagnostics.ok
    assert [d.code for d in diagnostics.infos] == ["sbom_provider_unlocked"]
    assert _recorded(build_path) is None


def test_a_dry_run_does_not_judge_the_lock_file(tmp_path):
    diagnostics = build_run(_context(_solution(tmp_path)), "app", tmp_path / "build", dry_run=True)
    assert "provisioner_lock_missing" not in _codes(diagnostics)


def test_build_run_uses_the_ring_of_the_deployments_version_document(tmp_path):
    root = _solution(tmp_path, policy="  provisioner_lock_policy:\n    rings: [prd]\n")
    _write(root, "version.yaml", "version", "dev", "  promotion:\n    ring: dev\n    order: 1\n")
    _write(
        root, "deployment.yaml", "deployment", "app", "  workspace: main\n  version: dev\n  environments:\n    - prd\n"
    )

    diagnostics = build_run(_context(root), "app", tmp_path / "build")

    assert diagnostics.ok, diagnostics.messages()
    assert "provisioner_lock_policy_not_applied" in [d.code for d in diagnostics.infos]


def test_policy_check_judges_the_build_path_and_records_the_result(tmp_path):
    build_path = tmp_path / "build"
    root = _solution(tmp_path, policy="  provisioner_lock_policy:\n    enforcement: warn\n")
    build_run(_context(root), "app", build_path)
    (build_path / "policy_results.json").unlink()

    diagnostics, checked = check_policies(_context(root), "app", build_path, policies=["provisioner_lock_policy"])

    assert checked == ["provisioner_lock_policy"]
    assert _codes(diagnostics) == ["provisioner_lock_missing"]
    assert _recorded(build_path).passed is True


def test_policy_list_shows_the_policy_as_offline(tmp_path):
    [info] = list_policies(_context(_solution(tmp_path)), "app", tmp_path / "missing")
    assert (info.name, info.phase, info.enforcement, info.backing, info.checkable, info.findings) == (
        "provisioner_lock_policy",
        "build",
        "deny",
        "strata (offline)",
        True,
        False,
    )
