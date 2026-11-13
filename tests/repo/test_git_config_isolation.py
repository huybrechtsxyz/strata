#!/usr/bin/env python3
"""The git configuration tests run under (tests/conftest.py, tests/gitconfig).

A developer's global or system git settings never reach a test: a hook from `core.hooksPath` ran on every commit
strata made in a test, and a credential helper could prompt. Only `tests/gitconfig` is read.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git_config(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "config", *args], cwd=cwd, capture_output=True, text=True, check=False)


def test_git_reads_the_tests_own_global_config(tmp_path: Path):
    listed = _git_config(tmp_path, "--list", "--show-origin")

    # `file:<path>`, quoted with escaped backslashes when the path needs it.
    origins = {line.split("\t", 1)[0].removeprefix("file:").strip('"') for line in listed.stdout.splitlines()}
    assert {Path(origin.replace("\\\\", "\\")) for origin in origins} == {Path(__file__).parents[1] / "gitconfig"}


def test_no_hooks_path_or_credential_helper_is_configured(tmp_path: Path):
    assert _git_config(tmp_path, "--get", "core.hooksPath").stdout == ""
    assert _git_config(tmp_path, "--get-all", "credential.helper").stdout == ""
