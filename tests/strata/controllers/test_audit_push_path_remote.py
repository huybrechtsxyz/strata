#!/usr/bin/env python3
"""An audit sink on a path remote: write, commit and push in the pipeline's own checkout.

docs/design/remote-commit-provenance.md, "Writing to a path remote". Real `git` throughout, against a local bare
repository
standing in for the server, and a clone of it standing in for the checkout a pipeline made.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from strata.controllers import audit_push as audit_push_module
from strata.controllers.audit_push import push_audit_files
from strata.models.audit_model import AuditGitSinkTargetModel
from strata.models.solution_model import SolutionModel

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _status(checkout: Path) -> list[str]:
    """`git status --porcelain`, one entry per line, sorted, leading spaces kept."""
    output = subprocess.run(
        ["git", "status", "--porcelain"], cwd=checkout, check=True, capture_output=True, text=True
    ).stdout
    return sorted(line for line in output.splitlines() if line)


def _server(tmp_path: Path) -> Path:
    """A bare repository with one commit on `main`: `README.md` and `inventory/hosts.yaml`."""
    bare = tmp_path / "server.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", str(bare), str(seed))
    (seed / "README.md").write_text("# inventory\n", encoding="utf-8")
    (seed / "inventory").mkdir()
    (seed / "inventory" / "hosts.yaml").write_text("hosts: []\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "push", "origin", "HEAD:main")
    return bare


def _pipeline_checkout(tmp_path: Path, bare: Path, *, shallow: bool = False) -> Path:
    """What a pipeline's `checkout:` step leaves: a detached `HEAD` at the branch tip, inside the solution."""
    checkout = tmp_path / "solution" / "repos" / "inventory"
    checkout.parent.mkdir(parents=True)
    depth = ["--depth", "1"] if shallow else []
    _git(tmp_path, "clone", *depth, f"file://{bare.as_posix()}", str(checkout))
    _git(checkout, "checkout", "--detach")
    return checkout


def _another_push(tmp_path: Path, bare: Path, relative: str, text: str) -> str:
    """Someone else pushes to `main` after the pipeline checked out."""
    other = tmp_path / f"other-{len(list(tmp_path.glob('other-*')))}"
    _git(tmp_path, "clone", str(bare), str(other))
    (other / relative).parent.mkdir(parents=True, exist_ok=True)
    (other / relative).write_text(text, encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "someone else")
    _git(other, "push", "origin", "HEAD:main")
    return _git(other, "rev-parse", "HEAD")


def _solution() -> SolutionModel:
    return SolutionModel.model_validate(
        {
            "meta": {"name": "platform-config"},
            "spec": {
                "remotes": [
                    {
                        "name": "inventory",
                        "type": "git",
                        "url": "https://git.example.com/inventory.git",
                        "path": "repos/inventory",
                    }
                ]
            },
        }
    )


def _sink(action: str = "push", remote: str = "inventory") -> AuditGitSinkTargetModel:
    return AuditGitSinkTargetModel.model_validate(
        {"remote": remote, "branch": "main", "path": "deploys", "action": action}
    )


def _files(tmp_path: Path, text: str = '{"execution_id": "1"}') -> dict[str, Path]:
    source = tmp_path / "audit-out" / "_manifest.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(text, encoding="utf-8")
    return {"_manifest.json": source}


def _push(tmp_path: Path, action: str = "push", *, run: str = "run1", text: str = '{"execution_id": "1"}'):
    return push_audit_files(
        tmp_path / "solution", _sink(action), _solution(), _files(tmp_path, text), Path(run), "ci-runner"
    )


def _on_server(tmp_path: Path, bare: Path, relative: str) -> str | None:
    verify = tmp_path / "verify"
    if verify.exists():
        _git(verify, "pull", "--quiet")
    else:
        _git(tmp_path, "clone", str(bare), str(verify))
    path = verify / relative
    return path.read_text(encoding="utf-8") if path.exists() else None


def test_push_writes_commits_and_pushes_from_the_pipeline_checkout(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)

    result = _push(tmp_path)

    assert result.success, result.detail
    assert _on_server(tmp_path, bare, "deploys/run1/_manifest.json") == '{"execution_id": "1"}'
    assert _git(checkout, "log", "-1", "--format=%an|%s") == "ci-runner|audit: run1"
    assert not (tmp_path / "solution" / ".strata").exists()  # no clone of its own


def test_only_the_files_written_are_committed(tmp_path: Path):
    """Other changes in the pipeline's checkout stay as they were: modified, staged or untracked."""
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    (checkout / "README.md").write_text("# edited by an earlier step\n", encoding="utf-8")
    (checkout / "inventory" / "hosts.yaml").write_text("hosts: [a]\n", encoding="utf-8")
    _git(checkout, "add", "inventory/hosts.yaml")
    (checkout / "scratch.txt").write_text("x\n", encoding="utf-8")

    assert _push(tmp_path).success

    assert _git(checkout, "show", "--name-only", "--format=", "HEAD").splitlines() == ["deploys/run1/_manifest.json"]
    assert _status(checkout) == [" M README.md", "?? scratch.txt", "M  inventory/hosts.yaml"]
    assert _on_server(tmp_path, bare, "README.md") == "# inventory\n"


def test_commit_stops_before_the_push(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    before = _git(checkout, "rev-parse", "HEAD")

    result = _push(tmp_path, "commit")

    assert result.success, result.detail
    assert _git(checkout, "rev-parse", "HEAD^") == before
    assert _on_server(tmp_path, bare, "deploys/run1/_manifest.json") is None


def test_none_only_writes_the_files(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    before = _git(checkout, "rev-parse", "HEAD")

    result = _push(tmp_path, "none")

    assert result.success, result.detail
    assert (checkout / "deploys" / "run1" / "_manifest.json").read_text(encoding="utf-8") == '{"execution_id": "1"}'
    assert _git(checkout, "rev-parse", "HEAD") == before
    assert _status(checkout) == ["?? deploys/"]


def test_a_branch_that_moved_is_rebased_onto_and_pushed(tmp_path: Path):
    """Another deploy pushed in between: fetch, rebase onto the branch, push again."""
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    (checkout / "README.md").write_text("# edited by an earlier step\n", encoding="utf-8")
    theirs = _another_push(tmp_path, bare, "deploys/run0/_manifest.json", '{"execution_id": "0"}')

    result = _push(tmp_path)

    assert result.success, result.detail
    assert _on_server(tmp_path, bare, "deploys/run0/_manifest.json") == '{"execution_id": "0"}'
    assert _on_server(tmp_path, bare, "deploys/run1/_manifest.json") == '{"execution_id": "1"}'
    assert _git(checkout, "rev-parse", "HEAD^") == theirs
    assert _status(checkout) == [" M README.md"]  # the autostash put it back


def test_a_shallow_pipeline_checkout_is_rebased_too(tmp_path: Path):
    """A pipeline checkout is usually shallow (Azure DevOps `fetchDepth: 1`)."""
    bare = _server(tmp_path)
    _pipeline_checkout(tmp_path, bare, shallow=True)
    _another_push(tmp_path, bare, "deploys/run0/_manifest.json", '{"execution_id": "0"}')

    result = _push(tmp_path)

    assert result.success, result.detail
    assert _on_server(tmp_path, bare, "deploys/run1/_manifest.json") == '{"execution_id": "1"}'


def test_a_rebase_conflict_fails_and_leaves_no_rebase_in_progress(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    _another_push(tmp_path, bare, "deploys/run1/_manifest.json", '{"execution_id": "theirs"}')

    result = _push(tmp_path)

    assert not result.success
    assert "rebase" in result.detail
    assert not (checkout / ".git" / "rebase-merge").exists()
    assert not (checkout / ".git" / "rebase-apply").exists()
    assert _on_server(tmp_path, bare, "deploys/run1/_manifest.json") == '{"execution_id": "theirs"}'


def test_a_push_that_keeps_failing_gives_up(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    _git(checkout, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    result = _push(tmp_path)

    assert not result.success
    assert "push" in result.detail


def test_nothing_to_commit_when_the_files_are_already_there(tmp_path: Path):
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    assert _push(tmp_path).success
    head = _git(checkout, "rev-parse", "HEAD")

    result = _push(tmp_path)

    assert result.success
    assert "nothing to commit" in result.detail
    assert _git(checkout, "rev-parse", "HEAD") == head


def test_the_checkouts_git_config_is_not_changed(tmp_path: Path):
    """The checkout is the pipeline's: the commit identity is passed per command, not written to its config."""
    bare = _server(tmp_path)
    checkout = _pipeline_checkout(tmp_path, bare)
    config = (checkout / ".git" / "config").read_text(encoding="utf-8")

    assert _push(tmp_path).success

    assert (checkout / ".git" / "config").read_text(encoding="utf-8") == config


def test_a_path_that_is_not_a_checkout_fails(tmp_path: Path):
    (tmp_path / "solution" / "repos" / "inventory").mkdir(parents=True)

    result = _push(tmp_path)

    assert not result.success
    assert "not a git checkout" in result.detail


def test_commit_or_none_on_a_reference_remote_is_refused_without_running_git(tmp_path: Path, monkeypatch):
    """Strata's own clone of a `reference` remote is read by nothing else, so anything short of a push is lost."""
    ran: list = []
    monkeypatch.setattr(audit_push_module, "run_command", lambda *a, **k: ran.append(a))
    solution = SolutionModel.model_validate(
        {
            "meta": {"name": "s"},
            "spec": {"remotes": [{"name": "inventory", "type": "git", "url": "https://h/i.git", "reference": "main"}]},
        }
    )
    for action in ("commit", "none"):
        result = push_audit_files(tmp_path, _sink(action), solution, _files(tmp_path), Path("run1"), "ci-runner")

        assert not result.success
        assert f"action '{action}'" in result.detail and "'path'" in result.detail
    assert ran == []
