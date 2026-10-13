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
