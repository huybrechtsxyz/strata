#!/usr/bin/env python3
"""Tests for `BaseGitOpsIntegration`/`ArgoCDIntegration`/`FluxIntegration`
(docs/design/gitops-integration.md, Implementation Plan Phase 3).

Three tiers: class-contract checks, `prepare()`'s sidecar-writing logic
(mocked), and one true end-to-end test driving `prepare()` -> `plan()` ->
`deploy()` -> `output()` -> `destroy()` against a real local bare git
repository, confirming the whole chain actually works end to end.
"""

import json
import subprocess
from pathlib import Path

import pytest

from strata.integrations.errors import IntegrationError
from strata.integrations.gitops import _SIDECAR_FILENAME, ArgoCDIntegration, FluxIntegration
from strata.integrations.registry import get
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.models.provisioning_model import OutputModel, ProvisionerGitOpsModel, ProvisionerModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel


def _provisioner(template: str = "templates/forge-values.yaml.j2", gitops: bool = True) -> ProvisionerModel:
    return ProvisionerModel(
        name="forge_sync",
        tool="argocd",
        gitops=ProvisionerGitOpsModel(remote="gitops-config", output_file="apps/forge/values.yaml") if gitops else None,
        output=OutputModel(template=template) if template else None,
    )


def _graph(provisioner: ProvisionerModel | None = None) -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(providers=["p"], provisioners=[provisioner or _provisioner()]),
    )
    return ResolvedWorkspaceGraph(workspace=workspace)


def _remote(url: str) -> SolutionRemoteModel:
    return SolutionRemoteModel(name="gitops-config", type=RemoteType.GIT, url=url, reference="main")


# ---------------------------------------------------------------------------
# Class contract
# ---------------------------------------------------------------------------


def test_class_declares_its_contract():
    assert ArgoCDIntegration.TYPE == "argocd"
    assert FluxIntegration.TYPE == "flux"
    assert ArgoCDIntegration.CAPABILITIES == {"infrastructure"}
    assert ArgoCDIntegration.TRANSPORTS == {"cli"}
    assert ArgoCDIntegration.COMMAND == "git"


def test_registered_in_the_registry():
    assert isinstance(get("argocd"), ArgoCDIntegration)
    assert isinstance(get("flux"), FluxIntegration)


@pytest.mark.parametrize(
    "raw,expected",
    [("git version 2.42.0", "2.42.0"), ("git version 2.42.0.windows.1", "2.42.0"), ("garbage", "garbage")],
)
def test_parse_version(raw, expected):
    assert ArgoCDIntegration().parse_version(raw) == expected


# ---------------------------------------------------------------------------
# default_output()/prepare() validation
# ---------------------------------------------------------------------------


def test_default_output_raises_without_template():
    with pytest.raises(IntegrationError, match="requires 'output.template'"):
        ArgoCDIntegration().default_output(ValueResolution(deployment="d"), _provisioner(), _graph())


def test_prepare_raises_without_output_template(tmp_path: Path):
    provisioner = _provisioner(template="")
    with pytest.raises(IntegrationError, match="requires 'output.template'"):
        ArgoCDIntegration().prepare(
            tmp_path, resolved=ValueResolution(deployment="d"), provisioner=provisioner, graph=_graph(provisioner)
        )


def test_prepare_raises_without_gitops_config(tmp_path: Path):
    provisioner = _provisioner(gitops=False)
    template_path = tmp_path / "template.yaml.j2"
    template_path.write_text("replicas: {{ provisioner.name }}\n")
    with pytest.raises(IntegrationError, match="has no 'gitops' configuration"):
        ArgoCDIntegration().prepare(
            tmp_path,
            resolved=ValueResolution(deployment="d"),
            provisioner=provisioner,
            graph=_graph(provisioner),
            template_path=template_path,
            remotes={"gitops-config": _remote("https://example.com/x.git")},
            root=tmp_path,
        )


def test_prepare_raises_for_unknown_remote(tmp_path: Path):
    provisioner = _provisioner()
    template_path = tmp_path / "template.yaml.j2"
    template_path.write_text("replicas: {{ provisioner.name }}\n")
    with pytest.raises(IntegrationError, match="not declared in this solution's spec.remotes"):
        ArgoCDIntegration().prepare(
            tmp_path,
            resolved=ValueResolution(deployment="d"),
            provisioner=provisioner,
            graph=_graph(provisioner),
            template_path=template_path,
            remotes={},
            root=tmp_path,
        )


def test_prepare_dry_run_mode_skips_sidecar(tmp_path: Path):
    """`remotes`/`root` unset (build_controller.py's --dry-run branch) -> only
    template validation runs, nothing is persisted."""
    provisioner = _provisioner()
    template_path = tmp_path / "template.yaml.j2"
    template_path.write_text("replicas: {{ provisioner.name }}\n")
    ArgoCDIntegration().prepare(
        tmp_path,
        resolved=ValueResolution(deployment="d"),
        provisioner=provisioner,
        graph=_graph(provisioner),
        template_path=template_path,
    )
    assert not (tmp_path / _SIDECAR_FILENAME).exists()


def test_prepare_writes_sidecar_for_real_build(tmp_path: Path):
    provisioner = _provisioner()
    template_path = tmp_path / "template.yaml.j2"
    template_path.write_text("replicas: {{ provisioner.name }}\n")
    remote = _remote("https://example.com/org/gitops-config.git")

    ArgoCDIntegration().prepare(
        tmp_path,
        resolved=ValueResolution(deployment="d"),
        provisioner=provisioner,
        graph=_graph(provisioner),
        template_path=template_path,
        remotes={"gitops-config": remote},
        root=tmp_path,
    )

    sidecar = json.loads((tmp_path / _SIDECAR_FILENAME).read_text())
    assert sidecar["output_file"] == "apps/forge/values.yaml"
    assert sidecar["rendered_filename"] == "template.yaml"
    assert sidecar["remote"]["name"] == "gitops-config"
    assert sidecar["root"] == str(tmp_path)


# ---------------------------------------------------------------------------
# plan/deploy/destroy/output — no sidecar present
# ---------------------------------------------------------------------------


def test_plan_fails_cleanly_without_a_sidecar(tmp_path: Path):
    result = ArgoCDIntegration().plan(tmp_path)
    assert not result.is_successful
    assert "no GitOps sidecar" in result.stderr


def test_deploy_fails_cleanly_without_a_sidecar(tmp_path: Path):
    result = ArgoCDIntegration().deploy(tmp_path)
    assert not result.is_successful
    assert "no GitOps sidecar" in result.stderr


def test_destroy_fails_cleanly_without_a_sidecar(tmp_path: Path):
    result = ArgoCDIntegration().destroy(tmp_path)
    assert not result.is_successful
    assert "no GitOps sidecar" in result.stderr


def test_output_fails_cleanly_without_a_sidecar(tmp_path: Path):
    result = ArgoCDIntegration().output(tmp_path)
    assert not result.is_successful
    assert "no GitOps sidecar" in result.stderr


# ---------------------------------------------------------------------------
# auth/resolved_values forwarding (docs/design/gitops-integration.md
# Implementation Plan Phase 5) — plan()/deploy()/destroy()/output() must
# forward whatever they're given straight through to
# ensure_checkout()/push_file()/remove_file(), with zero new credential
# logic of their own.
# ---------------------------------------------------------------------------


def _prepared(tmp_path: Path) -> Path:
    """A real sidecar on disk, via the same `prepare()` path every other
    test in this file already exercises."""
    provisioner = _provisioner()
    template_path = tmp_path / "template.yaml.j2"
    template_path.write_text("replicas: {{ provisioner.name }}\n")
    remote = _remote("https://example.com/org/gitops-config.git")
    ArgoCDIntegration().prepare(
        tmp_path,
        resolved=ValueResolution(deployment="d"),
        provisioner=provisioner,
        graph=_graph(provisioner),
        template_path=template_path,
        remotes={"gitops-config": remote},
        root=tmp_path,
    )
    (tmp_path / "template.yaml").write_text("replicas: 3\n")
    return tmp_path


def test_plan_forwards_auth_and_resolved_values_to_ensure_checkout(tmp_path: Path, monkeypatch):
    from strata.integrations import gitops as gitops_module
    from strata.integrations.git_push import PushResult
    from strata.models.auth_models import AuthenticationModel, CLIAuthenticationModel

    path = _prepared(tmp_path)
    captured: dict[str, object] = {}

    def _fake_ensure_checkout(root, remote, *, env=None, auth=None, resolved_values=None):
        captured["auth"] = auth
        captured["resolved_values"] = resolved_values
        return PushResult(True)

    monkeypatch.setattr(gitops_module, "ensure_checkout", _fake_ensure_checkout)
    auth = AuthenticationModel(method="cli", cli=CLIAuthenticationModel())

    result = ArgoCDIntegration().plan(path, env={}, auth=auth, resolved_values={"k": "v"})

    assert result.is_successful, result.stderr
    assert captured["auth"] is auth
    assert captured["resolved_values"] == {"k": "v"}


def test_deploy_forwards_auth_and_resolved_values_to_push_file(tmp_path: Path, monkeypatch):
    from strata.integrations import gitops as gitops_module
    from strata.integrations.git_push import PushResult
    from strata.models.auth_models import AuthenticationModel, CLIAuthenticationModel

    path = _prepared(tmp_path)
    captured: dict[str, object] = {}

    def _fake_push_file(
        root, remote, rendered_file, output_file, message, *, env=None, auth=None, resolved_values=None
    ):
        captured["auth"] = auth
        captured["resolved_values"] = resolved_values
        return PushResult(True)

    monkeypatch.setattr(gitops_module, "push_file", _fake_push_file)
    auth = AuthenticationModel(method="cli", cli=CLIAuthenticationModel())

    result = ArgoCDIntegration().deploy(path, env={}, auth=auth, resolved_values={"k": "v"})

    assert result.is_successful, result.stderr
    assert captured["auth"] is auth
    assert captured["resolved_values"] == {"k": "v"}


def test_destroy_forwards_auth_and_resolved_values_to_remove_file(tmp_path: Path, monkeypatch):
    from strata.integrations import gitops as gitops_module
    from strata.integrations.git_push import PushResult
    from strata.models.auth_models import AuthenticationModel, CLIAuthenticationModel

    path = _prepared(tmp_path)
    captured: dict[str, object] = {}

    def _fake_remove_file(root, remote, output_file, message, *, env=None, auth=None, resolved_values=None):
        captured["auth"] = auth
        captured["resolved_values"] = resolved_values
        return PushResult(True)

    monkeypatch.setattr(gitops_module, "remove_file", _fake_remove_file)
    auth = AuthenticationModel(method="cli", cli=CLIAuthenticationModel())

    result = ArgoCDIntegration().destroy(path, env={}, auth=auth, resolved_values={"k": "v"})

    assert result.is_successful, result.stderr
    assert captured["auth"] is auth
    assert captured["resolved_values"] == {"k": "v"}


def test_output_forwards_auth_and_resolved_values_to_ensure_checkout(tmp_path: Path, monkeypatch):
    from strata.integrations import gitops as gitops_module
    from strata.integrations.git_push import PushResult
    from strata.models.auth_models import AuthenticationModel, CLIAuthenticationModel
    from strata.utils.transport import CommandResult

    path = _prepared(tmp_path)
    captured: dict[str, object] = {}

    def _fake_ensure_checkout(root, remote, *, env=None, auth=None, resolved_values=None):
        captured["auth"] = auth
        captured["resolved_values"] = resolved_values
        return PushResult(True)

    def _fake_rev_parse(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=0, stdout="a" * 40, stderr="")

    monkeypatch.setattr(gitops_module, "ensure_checkout", _fake_ensure_checkout)
    monkeypatch.setattr(gitops_module, "run_command", _fake_rev_parse)
    auth = AuthenticationModel(method="cli", cli=CLIAuthenticationModel())

    result = ArgoCDIntegration().output(path, env={}, auth=auth, resolved_values={"k": "v"})

    assert result.is_successful, result.stderr
    assert captured["auth"] is auth
    assert captured["resolved_values"] == {"k": "v"}


# ---------------------------------------------------------------------------
# IntegrationError from credential resolution must never escape uncaught
# (found on follow-up review, 2026-10-02) — plan()/deploy()/destroy()/
# output() must convert it to a clean failed CommandResult, matching every
# other failure mode these methods already use, rather than letting a plain
# `Exception` (not a `StrataError`) crash `deploy_run()` with a raw
# traceback.
# ---------------------------------------------------------------------------


def _misconfigured_auth():
    from strata.models.auth_models import AuthenticationModel, SSHKeyAuthenticationModel

    # References a key that never resolves — ensure_checkout()/push_file()/
    # remove_file() all route through prepare_git_credentials(), which
    # raises IntegrationError for exactly this.
    return AuthenticationModel(method="ssh_key", ssh_key=SSHKeyAuthenticationModel(private_key="missing-ref"))


def test_plan_converts_integration_error_to_failed_result_not_a_crash(tmp_path: Path):
    path = _prepared(tmp_path)
    result = ArgoCDIntegration().plan(path, env={}, auth=_misconfigured_auth(), resolved_values={})
    assert not result.is_successful
    assert "did not resolve to a value" in result.stderr


def test_deploy_converts_integration_error_to_failed_result_not_a_crash(tmp_path: Path):
    path = _prepared(tmp_path)
    result = ArgoCDIntegration().deploy(path, env={}, auth=_misconfigured_auth(), resolved_values={})
    assert not result.is_successful
    assert "did not resolve to a value" in result.stderr


def test_destroy_converts_integration_error_to_failed_result_not_a_crash(tmp_path: Path):
    path = _prepared(tmp_path)
    result = ArgoCDIntegration().destroy(path, env={}, auth=_misconfigured_auth(), resolved_values={})
    assert not result.is_successful
    assert "did not resolve to a value" in result.stderr


def test_output_converts_integration_error_to_failed_result_not_a_crash(tmp_path: Path):
    path = _prepared(tmp_path)
    result = ArgoCDIntegration().output(path, env={}, auth=_misconfigured_auth(), resolved_values={})
    assert not result.is_successful
    assert "did not resolve to a value" in result.stderr


# ---------------------------------------------------------------------------
# Real end-to-end — prepare() -> plan() -> deploy() -> output() -> destroy()
# ---------------------------------------------------------------------------


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=False)
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_real_end_to_end_cycle(tmp_path: Path):
    bare_repo = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare_repo)], check=True, capture_output=True)

    root = tmp_path / "solution"
    root.mkdir()
    build_path = root / "build" / "forge_sync"
    build_path.mkdir(parents=True)

    remote = _remote(str(bare_repo))
    provisioner = _provisioner()
    template_path = root / "templates" / "forge-values.yaml.j2"
    template_path.parent.mkdir(parents=True)
    template_path.write_text("replicas: {{ provisioner.name }}\n")

    integration = ArgoCDIntegration()

    # build run: prepare() writes the sidecar (template rendering itself is
    # deploy_controller.py's job via render_output_template() — simulated
    # here by writing the expected rendered file directly, same file the
    # base class's own render_output_template() would produce).
    integration.prepare(
        build_path,
        resolved=ValueResolution(deployment="d"),
        provisioner=provisioner,
        graph=_graph(provisioner),
        template_path=template_path,
        remotes={"gitops-config": remote},
        root=root,
    )
    (build_path / "forge-values.yaml").write_text("replicas: forge_sync\n")

    # deploy run: plan() previews (repo is empty — a "new file" diff, exit 1,
    # normalised to a successful CommandResult), deploy() pushes, output()
    # reports commit_sha/remote_path.
    plan_result = integration.plan(build_path)
    assert plan_result.is_successful, plan_result.stderr

    deploy_result = integration.deploy(build_path)
    assert deploy_result.is_successful, deploy_result.stderr

    verify_dir = tmp_path / "verify"
    subprocess.run(["git", "clone", str(bare_repo), str(verify_dir)], check=True, capture_output=True)
    pushed_file = verify_dir / "apps" / "forge" / "values.yaml"
    assert pushed_file.exists()
    assert pushed_file.read_text() == "replicas: forge_sync\n"

    output_result = integration.output(build_path)
    assert output_result.is_successful, output_result.stderr
    payload = json.loads(output_result.stdout)
    assert payload["remote_path"]["value"] == "apps/forge/values.yaml"
    assert len(payload["commit_sha"]["value"]) == 40  # a real full git SHA

    # A second plan() now sees no differences (exit 0) and still succeeds.
    second_plan = integration.plan(build_path)
    assert second_plan.is_successful, second_plan.stderr

    # destroy() removes the file and pushes the removal.
    destroy_result = integration.destroy(build_path)
    assert destroy_result.is_successful, destroy_result.stderr
    subprocess.run(["git", "-C", str(verify_dir), "pull"], check=True, capture_output=True)
    assert not pushed_file.exists()
