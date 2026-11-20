#!/usr/bin/env python3
"""A GitOps step on a path remote: written in the pipeline's checkout (docs/design/remote-commit-provenance.md, "Writing
to a path remote").

Real `git` against a bare repository standing in for the server, and a detached clone standing in for the pipeline's
`checkout:` step, as `test_audit_push_path_remote.py` does for an audit sink.
"""

import json
import subprocess
from pathlib import Path

import pytest

from strata.integrations import gitops as gitops_module
from strata.integrations.gitops import _SIDECAR_FILENAME, ArgoCDIntegration
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.models.provisioning_model import OutputModel, ProvisionerGitOpsModel, ProvisionerModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel
from strata.utils.transport import CommandResult

pytestmark = pytest.mark.slow

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
_OUTPUT_FILE = "apps/forge/values.yaml"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _status(checkout: Path) -> list[str]:
    output = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted(line for line in output.splitlines() if line)


def _server(tmp_path: Path) -> Path:
    """A bare repository with one commit on `main`."""
    bare = tmp_path / "server.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", str(bare), str(seed))
    (seed / "README.md").write_text("# gitops\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "push", "origin", "HEAD:main")
    return bare


def _pipeline_checkout(tmp_path: Path, bare: Path) -> Path:
    """What a pipeline's `checkout:` step leaves: a detached `HEAD` at the branch tip, inside the solution."""
    checkout = tmp_path / "solution" / "repos" / "gitops-config"
    checkout.parent.mkdir(parents=True)
    _git(tmp_path, "clone", f"file://{bare.as_posix()}", str(checkout))
    _git(checkout, "checkout", "--detach")
    return checkout


def _on_server(tmp_path: Path, bare: Path, relative: str) -> str | None:
    verify = tmp_path / "verify"
    if verify.exists():
        _git(verify, "pull", "--quiet")
    else:
        _git(tmp_path, "clone", str(bare), str(verify))
    path = verify / relative
    return path.read_text(encoding="utf-8") if path.exists() else None


def _prepared(tmp_path: Path, *, action: str = "push", branch: str | None = "main") -> Path:
    """`build run`'s part: the sidecar and the rendered file in the step's build folder."""
    root = tmp_path / "solution"
    build_path = root / "build" / "forge_sync"
    build_path.mkdir(parents=True)
    template_path = root / "templates" / "forge-values.yaml.j2"
    template_path.parent.mkdir(parents=True)
    template_path.write_text("replicas: {{ provisioner.name }}\n", encoding="utf-8")
    provisioner = ProvisionerModel(
        name="forge_sync",
        tool="argocd",
        gitops=ProvisionerGitOpsModel(remote="gitops-config", output_file=_OUTPUT_FILE, branch=branch, action=action),
        output=OutputModel(template="templates/forge-values.yaml.j2"),
    )
    remote = SolutionRemoteModel(
        name="gitops-config", type=RemoteType.GIT, url="https://git.example.com/gitops.git", path="repos/gitops-config"
    )
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"), spec=WorkspaceSpecModel(providers=["p"], provisioners=[provisioner])
    )
    ArgoCDIntegration().prepare(
        build_path,
        resolved=ValueResolution(deployment="d"),
        provisioner=provisioner,
        graph=ResolvedWorkspaceGraph(workspace=workspace),
        template_path=template_path,
        remotes={"gitops-config": remote},
        root=root,
    )
    (build_path / "forge-values.yaml").write_text("replicas: 3\n", encoding="utf-8")
    return build_path


def test_prepare_records_branch_and_action_in_the_sidecar(tmp_path: Path):
    build_path = _prepared(tmp_path, action="commit", branch=None)

    sidecar = json.loads((build_path / _SIDECAR_FILENAME).read_text(encoding="utf-8"))

    assert sidecar["action"] == "commit"
    assert sidecar["branch"] is None
    assert sidecar["remote"]["path"] == "repos/gitops-config"


def test_the_whole_cycle_works_in_the_pipeline_checkout(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    build_path = _prepared(tmp_path)
    integration = ArgoCDIntegration()

    planned = integration.plan(build_path)
    assert planned.is_successful, planned.stderr

    deployed = integration.deploy(build_path)
    assert deployed.is_successful, deployed.stderr
    assert _on_server(tmp_path, bare, _OUTPUT_FILE) == "replicas: 3\n"
    assert (
        _git(checkout, "log", "-1", "--format=%s") == f"strata: update {_OUTPUT_FILE} (provisioner step 'forge_sync')"
    )
    assert not (tmp_path / "solution" / ".strata").exists()  # no clone of its own

    output = integration.output(build_path)
    assert output.is_successful, output.stderr
    assert json.loads(output.stdout)["commit_sha"]["value"] == _git(checkout, "rev-parse", "HEAD")

    (build_path / "forge-values.yaml").write_text("replicas: 4\n", encoding="utf-8")
    assert "+replicas: 4" in integration.plan(build_path).stdout  # diffed against the checkout's file
    previewed = integration.plan_destroy(build_path)
    assert previewed.stdout == f"would remove {_OUTPUT_FILE} from gitops-config"

    destroyed = integration.destroy(build_path)
    assert destroyed.is_successful, destroyed.stderr
    assert _on_server(tmp_path, bare, _OUTPUT_FILE) is None
    assert _status(checkout) == []


def test_commit_stops_before_the_push(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)

    deployed = ArgoCDIntegration().deploy(_prepared(tmp_path, action="commit", branch=None))

    assert deployed.is_successful, deployed.stderr
    assert "action: commit" in deployed.stdout
    assert _git(checkout, "show", f"HEAD:{_OUTPUT_FILE}") == "replicas: 3"
    assert _on_server(tmp_path, bare, _OUTPUT_FILE) is None


def test_none_writes_the_file_and_leaves_it_uncommitted(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    head = _git(checkout, "rev-parse", "HEAD")

    deployed = ArgoCDIntegration().deploy(_prepared(tmp_path, action="none", branch=None))

    assert deployed.is_successful, deployed.stderr
    assert "action: none" in deployed.stdout
    assert _status(checkout) == [f"?? {_OUTPUT_FILE}"]
    assert _git(checkout, "rev-parse", "HEAD") == head


def test_a_branch_that_moved_is_rebased_onto_and_pushed(tmp_path: Path):
    bare = _server(tmp_path)
    _pipeline_checkout(tmp_path, bare)
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(bare), str(other))
    (other / "apps").mkdir()
    (other / "apps" / "other.yaml").write_text("other: true\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "someone else")
    _git(other, "push", "origin", "HEAD:main")

    deployed = ArgoCDIntegration().deploy(_prepared(tmp_path))

    assert deployed.is_successful, deployed.stderr
    assert _on_server(tmp_path, bare, _OUTPUT_FILE) == "replicas: 3\n"
    assert _on_server(tmp_path, bare, "apps/other.yaml") == "other: true\n"


def test_other_changes_in_the_checkout_are_left_alone(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    (checkout / "README.md").write_text("# edited by the pipeline\n", encoding="utf-8")
    (checkout / "scratch.txt").write_text("untracked\n", encoding="utf-8")

    deployed = ArgoCDIntegration().deploy(_prepared(tmp_path))

    assert deployed.is_successful, deployed.stderr
    assert _status(checkout) == [" M README.md", "?? scratch.txt"]
    assert _on_server(tmp_path, bare, "README.md") == "# gitops\n"
    assert _git(checkout, "show", "--name-only", "--format=", "HEAD") == _OUTPUT_FILE


def test_a_folder_that_is_not_a_checkout_is_refused_before_writing(tmp_path: Path):
    folder = tmp_path / "solution" / "repos" / "gitops-config"
    folder.mkdir(parents=True)

    deployed = ArgoCDIntegration().deploy(_prepared(tmp_path))

    assert not deployed.is_successful
    assert "not a git checkout" in deployed.stderr
    assert not (folder / _OUTPUT_FILE).exists()


def test_a_push_without_a_branch_fails_naming_the_field(tmp_path: Path):
    """`validate` reports `gitops_branch_required` first; this is the run-time guard."""
    bare = _server(tmp_path)
    _pipeline_checkout(tmp_path, bare)

    deployed = ArgoCDIntegration().deploy(_prepared(tmp_path, branch=None))

    assert not deployed.is_successful
    assert "gitops.branch" in deployed.stderr


def test_destroying_a_file_that_was_never_committed_has_nothing_to_remove(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    head = _git(checkout, "rev-parse", "HEAD")
    integration = ArgoCDIntegration()
    build_path = _prepared(tmp_path)

    assert integration.plan_destroy(build_path).stdout.endswith("nothing to remove")
    destroyed = integration.destroy(build_path)

    assert destroyed.is_successful, destroyed.stderr
    assert "nothing to remove" in destroyed.stdout
    assert _git(checkout, "rev-parse", "HEAD") == head


def test_a_destroy_whose_tracking_check_fails_removes_nothing(tmp_path: Path, monkeypatch):
    """A failing `git ls-files` (an index lock, say) must not read as "untracked": the file would be deleted with
    nothing committed, and the destroy reported done."""
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    integration = ArgoCDIntegration()
    build_path = _prepared(tmp_path)
    assert integration.deploy(build_path).is_successful
    real_run = gitops_module.run_command

    def _ls_files_fails(args, **kwargs):
        if "ls-files" in args:
            return CommandResult(returncode=128, stdout="", stderr="index.lock exists")
        return real_run(args, **kwargs)

    monkeypatch.setattr(gitops_module, "run_command", _ls_files_fails)

    destroyed = integration.destroy(build_path)

    assert not destroyed.is_successful
    assert "ls-files" in destroyed.stderr and "index.lock" in destroyed.stderr
    assert (checkout / _OUTPUT_FILE).is_file()
    assert _on_server(tmp_path, bare, _OUTPUT_FILE) == "replicas: 3\n"


def test_the_checkout_is_found_under_the_deploys_root_not_the_builds(tmp_path: Path):
    """The build recorded `tmp_path/solution` as its root. The deploy runs from another folder, where the pipeline
    made its checkout: `deploy_run()` sets `solution_root`, and the step writes there."""
    bare = _server(tmp_path)
    build_path = _prepared(tmp_path)
    deploy_root = tmp_path / "deploy-agent" / "solution"
    checkout = deploy_root / "repos" / "gitops-config"
    checkout.parent.mkdir(parents=True)
    _git(tmp_path, "clone", f"file://{bare.as_posix()}", str(checkout))
    _git(checkout, "checkout", "--detach")

    without_root = ArgoCDIntegration().deploy(build_path)
    assert not without_root.is_successful
    assert "does not exist" in without_root.stderr  # the build's folder has no checkout

    integration = ArgoCDIntegration()
    integration.solution_root = deploy_root
    deployed = integration.deploy(build_path)

    assert deployed.is_successful, deployed.stderr
    assert _on_server(tmp_path, bare, _OUTPUT_FILE) == "replicas: 3\n"
    assert json.loads(integration.output(build_path).stdout)["commit_sha"]["value"] == _git(
        checkout, "rev-parse", "HEAD"
    )
