#!/usr/bin/env python3
"""A `path` remote's `requires`: the versions of a repository the solution accepts.

docs/design/remotes.md, "Which versions a solution accepts". A pipeline names both the platform's tag and the
Terraform repository's tag; `requires` catches the two drifting apart, at validate, before a build takes a file.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from strata.controllers import checkout_state
from strata.controllers.checkout_state import checkout_ref
from strata.controllers.solution_context import open_solution
from strata.utils.diagnostics import Diagnostic
from strata.utils.transport import CommandResult

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_API = "apiVersion: strata.huybrechts.xyz/v2\n"
_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
_RANGE = ">=1.4.0 <2.0.0"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _solution(tmp_path: Path, *, requires: str = _RANGE, path: str = "../infra") -> tuple[Path, Path]:
    """A solution whose `infra` remote is a checkout at `path` with one commit on `main`."""
    root = tmp_path / "platform"
    root.mkdir()
    (root / "strata.yaml").write_text(
        f"{_API}kind: solution\nmeta:\n  name: platform\nspec:\n  remotes:\n"
        f"    - name: infra\n      type: git\n      url: https://git.example.com/infra.git\n"
        f'      path: {path}\n      requires: "{requires}"\n',
        encoding="utf-8",
    )
    checkout = (root / path).resolve()
    checkout.mkdir(parents=True, exist_ok=True)
    (checkout / "main.tf").write_text("# v1\n", encoding="utf-8")
    _git(checkout, "init", "-b", "main")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-m", "first")
    return root, checkout


def _at_tags(checkout: Path, *tags: str) -> None:
    for tag in tags:
        _git(checkout, "tag", "-a", tag, "-m", tag)
    _git(checkout, "checkout", "--detach", tags[0])


def _findings(root: Path) -> list[Diagnostic]:
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    context.resolve()
    return [d for d in context.diagnostics.items if d.code and d.code.startswith("remote_requires")]


@pytest.mark.parametrize("tag", ["v1.4.0", "1.9.9", "v1.5.0-rc1"], ids=["lower-bound", "no-v-prefix", "prerelease"])
def test_a_checkout_on_a_tag_in_the_range_passes(tmp_path: Path, tag: str):
    root, checkout = _solution(tmp_path)
    _at_tags(checkout, tag)

    assert _findings(root) == []


@pytest.mark.parametrize("tag", ["v1.3.9", "v2.0.0", "v2.0.0-rc1"], ids=["below", "upper-bound", "next-major-rc"])
def test_a_checkout_on_a_tag_outside_the_range_fails(tmp_path: Path, tag: str):
    root, checkout = _solution(tmp_path)
    _at_tags(checkout, tag)

    [finding] = _findings(root)

    assert finding.severity.value == "error"
    assert finding.code == "remote_requires_unmet"
    assert f"is on tag '{tag}', outside requires '{_RANGE}'" in finding.message
    assert finding.location == "spec.remotes.0.requires"
    assert finding.source == str(root / "strata.yaml")


def test_build_run_refuses_a_checkout_outside_the_range(tmp_path: Path):
    """`require_valid()`, which `build run` and `deploy` start with, fails before a file is taken."""
    root, checkout = _solution(tmp_path)
    _at_tags(checkout, "v2.1.0")

    context = open_solution(root)
    context.resolve()

    assert not context.ok


def test_the_highest_version_tag_on_the_commit_decides(tmp_path: Path):
    """`v1.4.0-rc1` and `v1.4.0` on one commit: the release decides, not the candidate git sorts above it."""
    root, checkout = _solution(tmp_path, requires=">=1.4.0")
    _at_tags(checkout, "v1.4.0-rc1", "v1.4.0", "latest")

    assert _findings(root) == []


def test_a_tag_that_is_not_a_version_fails(tmp_path: Path):
    root, checkout = _solution(tmp_path)
    _at_tags(checkout, "release-candidate")

    [finding] = _findings(root)

    assert finding.code == "remote_requires_unmet"
    assert "'release-candidate', which is not a release version" in finding.message


def test_a_commit_with_one_tag_in_the_range_passes(tmp_path: Path):
    """The same tree re-released under a new major: the pipeline may have named either tag."""
    root, checkout = _solution(tmp_path)
    _at_tags(checkout, "v2.0.0", "v1.9.0")

    assert _findings(root) == []


def test_a_commit_with_every_release_tag_outside_the_range_names_them_all(tmp_path: Path):
    root, checkout = _solution(tmp_path)
    _at_tags(checkout, "v2.1.0", "v2.0.0")

    [finding] = _findings(root)

    assert "is on tags 'v2.1.0', 'v2.0.0', outside requires" in finding.message


def test_a_build_number_tag_is_not_a_release(tmp_path: Path):
    """`20241001` is a PEP 440 version, but it must not outrank `v1.4.0` on the same commit."""
    root, checkout = _solution(tmp_path)
    _at_tags(checkout, "v1.4.0", "20241001")

    assert _findings(root) == []
    assert checkout_ref(checkout) == "v1.4.0"


def test_git_failing_to_read_the_tags_is_a_warning_not_a_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """ "Dubious ownership" in a CI container must not read as "no tag" and let any version through."""
    root, checkout = _solution(tmp_path)
    _at_tags(checkout, "v3.0.0")
    real = checkout_state.run_command

    def refusing(args, **kwargs):
        if args[:2] == ["git", "tag"]:
            return CommandResult(returncode=128, stdout="", stderr="fatal: detected dubious ownership")
        return real(args, **kwargs)

    monkeypatch.setattr(checkout_state, "run_command", refusing)

    [finding] = _findings(root)

    assert finding.severity.value == "warning"
    assert finding.code == "remote_requires_unchecked"
    assert "dubious ownership" in finding.message


def test_a_checkout_on_a_branch_is_an_info_not_a_failure(tmp_path: Path):
    """A sandbox following `dev`: reported, but `validate --strict` still passes."""
    root, checkout = _solution(tmp_path)
    _git(checkout, "checkout", "-b", "dev")

    [finding] = _findings(root)

    assert finding.severity.value == "info"
    assert finding.code == "remote_requires_unchecked"
    assert "is on branch 'dev', not a version tag" in finding.message


def test_a_checkout_on_a_commit_no_tag_names_is_an_info(tmp_path: Path):
    root, checkout = _solution(tmp_path)
    _git(checkout, "checkout", "--detach", "HEAD")

    [finding] = _findings(root)

    assert finding.code == "remote_requires_unchecked"
    assert "is on a commit no tag names" in finding.message


def test_a_checkout_that_is_not_there_is_not_checked(tmp_path: Path):
    """A developer may validate without every checkout; the build reports a missing one where it needs it."""
    root, checkout = _solution(tmp_path)
    checkout.rename(checkout.with_name("elsewhere"))  # not rmtree: git's object files are read-only on Windows

    assert _findings(root) == []


def test_a_checkout_inside_the_solution_root_is_checked_too(tmp_path: Path):
    root, checkout = _solution(tmp_path, path="repos/infra")
    _at_tags(checkout, "v2.0.0")

    assert [d.code for d in _findings(root)] == ["remote_requires_unmet"]
