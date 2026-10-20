#!/usr/bin/env python3
"""Direct tests for `strata.utils.path_safety`.

This module had no dedicated test file despite being the repo's path-traversal
guard — every helper was only exercised indirectly, through whichever model
happened to call it. Found during the asset-management code review.
"""

from pathlib import Path

import pytest

from strata.utils.path_safety import (
    resolve_within,
    validate_file_ref_no_traversal,
    validate_no_path_traversal,
    validate_no_url_credentials,
    validate_relative_path,
)

# ---------------------------------------------------------------------------
# validate_no_path_traversal()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("..", id="bare"),
        pytest.param("../etc", id="leading"),
        pytest.param("a/../b", id="middle"),
        pytest.param("a/..", id="trailing"),
        pytest.param("a\\..\\b", id="backslash-separated"),
    ],
)
def test_rejects_parent_directory_segments(value: str):
    with pytest.raises(ValueError, match="parent directory"):
        validate_no_path_traversal(value)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("/etc/passwd", id="posix-root"),
        pytest.param("\\\\server\\share", id="unc"),
        pytest.param("C:/Windows", id="drive-letter"),
    ],
)
def test_rejects_absolute_paths(value: str):
    with pytest.raises(ValueError, match="relative|absolute"):
        validate_no_path_traversal(value)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("archive..json", id="dots-inside-filename"),
        pytest.param("v1..2/config", id="dots-inside-segment"),
        pytest.param("..hidden", id="leading-dots-in-name"),
        pytest.param("a/b/c.yaml", id="ordinary-nested"),
        pytest.param("feature/foo", id="git-ref-shape"),
    ],
)
def test_allows_dots_that_are_not_a_parent_segment(value: str):
    """`..` is matched per segment, not as a substring — a filename that merely
    contains two dots is an ordinary name, not traversal. (Before this was
    segment-aware, `archive..json` was rejected outright.)"""
    validate_no_path_traversal(value)


# ---------------------------------------------------------------------------
# validate_relative_path()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a\\b\\c", "a/b/c", id="backslashes-normalized"),
        pytest.param("a/b/", "a/b", id="trailing-slash-stripped"),
        pytest.param("a/b", "a/b", id="already-clean"),
    ],
)
def test_relative_path_normalizes(value: str, expected: str):
    assert validate_relative_path(value) == expected


def test_relative_path_rejects_a_leading_slash_rather_than_stripping_it():
    """The absolute check runs on the raw value first, so a leading '/' is an
    error — only a *trailing* one is stripped."""
    with pytest.raises(ValueError, match="relative|absolute"):
        validate_relative_path("/a/b/")


def test_relative_path_still_rejects_traversal():
    with pytest.raises(ValueError, match="parent directory"):
        validate_relative_path("a/../b")


# ---------------------------------------------------------------------------
# validate_file_ref_no_traversal()
# ---------------------------------------------------------------------------


def test_file_ref_checks_only_the_part_after_a_repo_prefix():
    validate_file_ref_no_traversal("@infra/config/app.yaml")
    with pytest.raises(ValueError, match="parent directory"):
        validate_file_ref_no_traversal("@infra/../../etc/passwd")


def test_file_ref_without_a_repo_prefix_is_checked_as_is():
    validate_file_ref_no_traversal("config/app.yaml")
    with pytest.raises(ValueError, match="parent directory"):
        validate_file_ref_no_traversal("../etc/passwd")


# ---------------------------------------------------------------------------
# resolve_within() — the runtime complement to the string validators
# ---------------------------------------------------------------------------


def test_resolve_within_returns_a_contained_path(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    assert resolve_within(tmp_path, tmp_path / "sub") == (tmp_path / "sub").resolve()


def test_resolve_within_accepts_a_path_that_does_not_exist_yet(tmp_path: Path):
    """Callers check containment before `exists()`, so a not-yet-created path
    must resolve rather than raise."""
    assert resolve_within(tmp_path, tmp_path / "missing" / "file.json")


def test_resolve_within_rejects_an_escaping_path(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(ValueError, match="resolves outside"):
        resolve_within(root, root / ".." / "secret.txt")


def test_resolve_within_collapses_traversal_before_comparing(tmp_path: Path):
    """A purely lexical check would be fooled by a path that escapes and comes
    back — and by one that merely looks like it escapes but doesn't."""
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    assert resolve_within(root, root / "sub" / ".." / "ok.txt")
    with pytest.raises(ValueError, match="resolves outside"):
        resolve_within(root, root / "sub" / ".." / ".." / "escaped.txt")


def test_resolve_within_rejects_an_absolute_path_elsewhere(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="resolves outside"):
        resolve_within(root, other / "file.txt")


# ---------------------------------------------------------------------------
# validate_no_url_credentials()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://host/org/repo.git",
        "https://user@host/org/repo.git",
        "https://myorg@dev.azure.com/myorg/project/_git/repo",
        "ssh://git@host:2222/org/repo.git",
        "ssh://" + "a" * 40 + "@host/org/repo.git",  # only http(s) usernames are checked
        "git@github.com:org/repo.git",
        "../relative/local/path",
        "oci://registry.example.com/charts",
        "https://[::1/repo.git",  # not parseable, so there is no userinfo to find
    ],
)
def test_url_credentials_allowed_without_a_password(value):
    validate_no_url_credentials(value, "url")


@pytest.mark.parametrize(
    "token",
    [
        "ghp_AbCd1234EfGh5678IjKl",
        "github_pat_11ABCDEFG0abcdefghijkl",
        "glpat-AbCdEfGhIjKlMnOp",
        "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6",  # a 52-character Azure DevOps style token
    ],
)
def test_a_token_used_as_the_http_username_is_rejected_and_never_echoed(token):
    with pytest.raises(ValueError, match="must not embed a password or token") as excinfo:
        validate_no_url_credentials(f"https://{token}@host/org/repo.git", "url")
    assert token not in str(excinfo.value)


@pytest.mark.parametrize("value", ["https://u:pw@host/r.git", "http://:pw@host/r.git", "ssh://git:pw@host/r.git"])
def test_url_with_a_password_is_rejected_and_never_echoed(value):
    with pytest.raises(ValueError, match="url must not embed a password") as excinfo:
        validate_no_url_credentials(value, "url")
    assert "pw" not in str(excinfo.value).replace("password", "")
