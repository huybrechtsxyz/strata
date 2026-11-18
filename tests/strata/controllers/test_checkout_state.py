#!/usr/bin/env python3
"""Tests for `checkout_state()` (docs/work/remote-commit-provenance.md, Phase 1)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from strata.controllers.checkout_state import CheckoutState, checkout_state

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _repository(path: Path) -> str:
    """A repository at `path` with `infra/main.tf`, `docs/README.md` and an ignored `.terraform/`, all committed."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    (path / "infra").mkdir()
    (path / "infra" / "main.tf").write_text("# v1\n", encoding="utf-8")
    (path / "docs").mkdir()
    (path / "docs" / "README.md").write_text("# docs\n", encoding="utf-8")
    (path / ".gitignore").write_text(".terraform/\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "first")
    return _git(path, "rev-parse", "HEAD")


def test_a_clean_checkout_gives_its_commit_and_is_not_dirty(tmp_path: Path):
    head = _repository(tmp_path / "repo")

    assert checkout_state(tmp_path / "repo", ["infra"]) == CheckoutState(commit=head, dirty=False)


def test_without_paths_the_dirty_check_is_skipped(tmp_path: Path):
    head = _repository(tmp_path / "repo")
    (tmp_path / "repo" / "infra" / "main.tf").write_text("# changed\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo") == CheckoutState(commit=head, dirty=None)


def test_a_modified_file_inside_the_paths_is_dirty(tmp_path: Path):
    _repository(tmp_path / "repo")
    (tmp_path / "repo" / "infra" / "main.tf").write_text("# changed\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo", ["infra"]).dirty is True


def test_a_modified_file_outside_the_paths_is_not_dirty(tmp_path: Path):
    """D4: a changed README elsewhere does not make a Terraform build dirty."""
    _repository(tmp_path / "repo")
    (tmp_path / "repo" / "docs" / "README.md").write_text("# changed\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo", ["infra"]).dirty is False


def test_an_untracked_file_inside_the_paths_is_dirty(tmp_path: Path):
    """The build copies untracked files too, so they are part of what was taken."""
    _repository(tmp_path / "repo")
    (tmp_path / "repo" / "infra" / "extra.tf").write_text("# new\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo", ["infra"]).dirty is True


def test_an_ignored_file_is_not_dirty(tmp_path: Path):
    """D11: build leftovers such as `.terraform/` would otherwise make every developer build dirty."""
    _repository(tmp_path / "repo")
    (tmp_path / "repo" / "infra" / ".terraform").mkdir()
    (tmp_path / "repo" / "infra" / ".terraform" / "state").write_text("x\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo", ["infra"]).dirty is False


def test_empty_paths_check_the_whole_checkout(tmp_path: Path):
    _repository(tmp_path / "repo")
    (tmp_path / "repo" / "docs" / "README.md").write_text("# changed\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo", []).dirty is True


def test_an_excluded_folder_does_not_make_the_checkout_dirty(tmp_path: Path):
    """The solution root's own build output is not part of what the build took (D13)."""
    _repository(tmp_path / "repo")
    (tmp_path / "repo" / "build").mkdir()
    (tmp_path / "repo" / "build" / "out.json").write_text("{}\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo", [], exclude=["build"]).dirty is False
    assert checkout_state(tmp_path / "repo", []).dirty is True


def test_a_glob_path_matches_as_git_matches_it(tmp_path: Path):
    """An asset's `source_path` may be a glob; it is passed to git as a pathspec."""
    _repository(tmp_path / "repo")
    (tmp_path / "repo" / "infra" / "main.tf").write_text("# changed\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo", ["infra/*.tf"]).dirty is True
    assert checkout_state(tmp_path / "repo", ["docs/*.md"]).dirty is False


def test_a_folder_that_is_not_a_git_checkout_has_no_commit(tmp_path: Path):
    (tmp_path / "plain").mkdir()

    state = checkout_state(tmp_path / "plain", ["."])

    assert (state.commit, state.dirty) == (None, None)
    assert state.problem is not None and "is not a git checkout" in state.problem


def test_a_plain_folder_inside_a_repository_does_not_report_the_parents_commit(tmp_path: Path):
    """Without its own `.git`, `git rev-parse` would walk up and report the enclosing repository's commit."""
    _repository(tmp_path / "repo")

    state = checkout_state(tmp_path / "repo" / "infra", ["."])

    assert (state.commit, state.dirty) == (None, None)
    assert state.problem is not None and "is not a git checkout" in state.problem


def test_enclosing_reads_the_repository_a_subfolder_is_in(tmp_path: Path):
    """The solution root may be a folder inside its repository; only its own files are checked."""
    head = _repository(tmp_path / "repo")
    (tmp_path / "repo" / "docs" / "README.md").write_text("# changed\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo" / "infra", ["."], enclosing=True) == CheckoutState(head, False)
    (tmp_path / "repo" / "infra" / "main.tf").write_text("# changed\n", encoding="utf-8")
    assert checkout_state(tmp_path / "repo" / "infra", ["."], enclosing=True).dirty is True


def test_enclosing_outside_any_repository_has_no_commit(tmp_path: Path):
    (tmp_path / "plain").mkdir()

    state = checkout_state(tmp_path / "plain", ["."], enclosing=True)

    assert (state.commit, state.dirty) == (None, None)
    assert state.problem is not None and "git rev-parse HEAD" in state.problem


def test_literal_and_glob_pathspecs(tmp_path: Path):
    """`:(glob)` keeps `*` inside one segment, as `pathlib.glob()` does; `:(literal)` matches nothing else."""
    _repository(tmp_path / "repo")
    (tmp_path / "repo" / "infra" / "main.tf").write_text("# changed\n", encoding="utf-8")

    assert checkout_state(tmp_path / "repo", [":(glob)*.tf"]).dirty is False
    assert checkout_state(tmp_path / "repo", [":(glob)**/*.tf"]).dirty is True
    assert checkout_state(tmp_path / "repo", [":(literal)infra/*.tf"]).dirty is False
    assert checkout_state(tmp_path / "repo", [":(literal)infra"]).dirty is True


def test_a_repository_without_a_commit_reports_why(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    _git(tmp_path / "empty", "init", "-b", "main")

    state = checkout_state(tmp_path / "empty", ["."])

    assert (state.commit, state.dirty) == (None, None)
    assert state.problem is not None and "git rev-parse HEAD" in state.problem


def test_a_worktree_with_a_git_file_is_a_checkout(tmp_path: Path):
    """A worktree (or a submodule) has a `.git` file, not a folder: still a checkout of its own."""
    head = _repository(tmp_path / "repo")
    _git(tmp_path / "repo", "worktree", "add", "--detach", str(tmp_path / "wt"))

    assert (tmp_path / "wt" / ".git").is_file()
    assert checkout_state(tmp_path / "wt", ["infra"]) == CheckoutState(commit=head, dirty=False)


def test_the_dirty_check_leaves_the_index_alone(tmp_path: Path):
    """`--no-optional-locks`: reading someone else's checkout must not rewrite its index."""
    _repository(tmp_path / "repo")
    index = tmp_path / "repo" / ".git" / "index"
    before = index.read_bytes()
    (tmp_path / "repo" / "infra" / "main.tf").touch()

    checkout_state(tmp_path / "repo", ["infra"])

    assert index.read_bytes() == before
