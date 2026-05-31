"""Tests for strata.utils.system — path resolution, normalisation, UUID."""

from pathlib import Path

import pytest

from strata.utils.system import (
    generate_uuid,
    normalize_path,
    resolve_path,
    resolve_work_path,
)

# ---------------------------------------------------------------------------
# resolve_path — @repo-name cross-repo references
# ---------------------------------------------------------------------------


class TestResolvePathAtRef:
    """resolve_path with @repo_name/... cross-repo references."""

    def test_at_ref_resolved_correctly(self, tmp_path):
        """@repo-name/rel/path is joined onto the repo root from repo_map."""
        repo_root = str(tmp_path / "my-repo")
        repo_map = {"my-repo": repo_root}

        result = resolve_path(
            base_path=str(tmp_path),
            target_path="@my-repo/sub/file.yaml",
            repo_map=repo_map,
        )
        assert result == Path(repo_root) / "sub/file.yaml"

    def test_at_ref_repo_only_no_subpath(self, tmp_path):
        """@repo-name with no trailing path resolves to the repo root."""
        repo_root = str(tmp_path / "my-repo")
        repo_map = {"my-repo": repo_root}

        result = resolve_path(
            base_path=str(tmp_path),
            target_path="@my-repo",
            repo_map=repo_map,
        )
        assert result == Path(repo_root)

    def test_at_ref_unknown_repo_raises(self, tmp_path):
        """Unknown @repo-name raises ValueError."""
        repo_map = {"known-repo": str(tmp_path)}

        with pytest.raises(ValueError, match="Unknown repo reference"):
            resolve_path(
                base_path=str(tmp_path),
                target_path="@unknown-repo/path.yaml",
                repo_map=repo_map,
            )

    def test_at_ref_no_repo_map_raises(self, tmp_path):
        """@repo-name with repo_map=None raises ValueError."""
        with pytest.raises(ValueError, match="Unknown repo reference"):
            resolve_path(
                base_path=str(tmp_path),
                target_path="@some-repo/file.yaml",
                repo_map=None,
            )


# ---------------------------------------------------------------------------
# resolve_path — plain paths (no @)
# ---------------------------------------------------------------------------


class TestResolvePathPlain:
    """resolve_path with ordinary relative and absolute paths."""

    def test_plain_relative_path_joined_onto_base(self, tmp_path):
        """A relative target_path is joined onto base_path."""
        result = resolve_path(
            base_path=str(tmp_path),
            target_path="sub/dir/file.yaml",
        )
        assert result == tmp_path / "sub" / "dir" / "file.yaml"

    def test_absolute_target_path_used_directly(self, tmp_path):
        """An absolute target_path is returned as-is (base ignored)."""
        absolute = str(tmp_path / "absolute" / "path.yaml")
        result = resolve_path(
            base_path="/some/other/base",
            target_path=absolute,
        )
        assert result == Path(absolute)

    def test_no_target_returns_base(self, tmp_path):
        """When target_path is None, base_path is returned as a Path."""
        result = resolve_path(base_path=str(tmp_path))
        assert result == tmp_path

    def test_empty_base_uses_cwd(self):
        """Empty base_path falls back to CWD."""
        import os

        result = resolve_path(base_path="", target_path="relative/path")
        expected = Path(os.getcwd()) / "relative" / "path"
        assert result == expected

    def test_sub_paths_joined(self, tmp_path):
        """Sub-paths are appended after base/target resolution."""
        # target_path is 2nd positional; sub_paths follow positionally
        result = resolve_path(str(tmp_path), "level1", "level2", "file.txt")
        assert result == tmp_path / "level1" / "level2" / "file.txt"

    def test_absolute_sub_path_raises(self, tmp_path):
        """Absolute path in sub_paths raises ValueError."""
        # Use a real absolute path so the test works on both Windows and Linux
        abs_subpath = str(tmp_path / "abs_subpath")
        with pytest.raises(ValueError, match="Absolute path not allowed"):
            resolve_path(str(tmp_path), "rel", abs_subpath)


# ---------------------------------------------------------------------------
# resolve_path — Windows backslash guard
# ---------------------------------------------------------------------------


class TestResolvePathBackslashGuard:
    """resolve_path raises on Windows-style backslash paths when not on Windows."""

    @pytest.mark.skipif(
        __import__("os").name == "nt",
        reason="Backslash is a valid separator on Windows",
    )
    def test_backslash_only_path_raises_on_linux(self, tmp_path):
        """target_path with '\\' and no '/' raises ValueError on non-Windows."""
        with pytest.raises(ValueError, match="Windows backslash separators"):
            resolve_path(str(tmp_path), "config\\workspace.yaml")

    @pytest.mark.skipif(
        __import__("os").name == "nt",
        reason="Backslash is a valid separator on Windows",
    )
    def test_nested_backslash_path_raises_on_linux(self, tmp_path):
        """Nested Windows path like 'sub\\dir\\file.yaml' raises on non-Windows."""
        with pytest.raises(ValueError, match="forward slashes"):
            resolve_path(str(tmp_path), "sub\\dir\\file.yaml")

    @pytest.mark.skipif(
        __import__("os").name == "nt",
        reason="Backslash is a valid separator on Windows",
    )
    def test_mixed_slash_path_not_raised(self, tmp_path):
        """A path with both '/' and '\\' is not flagged (e.g. escaped chars)."""
        # Should not raise — forward slash present means it's not a pure Windows path
        result = resolve_path(str(tmp_path), "sub/dir\\file.yaml")
        assert result is not None

    def test_forward_slash_path_always_ok(self, tmp_path):
        """Paths with only forward slashes are always accepted."""
        result = resolve_path(str(tmp_path), "config/workspace.yaml")
        assert result == tmp_path / "config" / "workspace.yaml"


# ---------------------------------------------------------------------------
# normalize_path
# ---------------------------------------------------------------------------


class TestNormalizePath:
    """normalize_path strips characters invalid in file paths."""

    def test_valid_path_unchanged(self):
        """A simple valid path passes through unchanged."""
        assert normalize_path("some_dir") == "some_dir"

    def test_removes_invalid_characters(self):
        """Characters like < > : " | ? * are replaced with _."""
        result = normalize_path("a<b>c:d")
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result

    def test_strips_leading_and_trailing_dots_and_spaces(self):
        """Leading and trailing dots/spaces are stripped."""
        result = normalize_path("  .filename.  ")
        assert not result.startswith((" ", "."))
        assert not result.endswith((" ", "."))

    def test_empty_string(self):
        """Empty string produces empty string."""
        assert normalize_path("") == ""


# ---------------------------------------------------------------------------
# generate_uuid
# ---------------------------------------------------------------------------


class TestGenerateUuid:
    """generate_uuid returns a well-formed UUID string."""

    def test_returns_string(self):
        """generate_uuid returns a str."""
        result = generate_uuid()
        assert isinstance(result, str)

    def test_returns_valid_uuid_format(self):
        """generate_uuid returns a string parseable as UUID."""
        import uuid

        result = generate_uuid()
        # Should not raise
        parsed = uuid.UUID(result)
        assert str(parsed) == result

    def test_unique_on_each_call(self):
        """Successive calls produce different UUIDs."""
        ids = {generate_uuid() for _ in range(10)}
        assert len(ids) == 10


# ---------------------------------------------------------------------------
# resolve_work_path
# ---------------------------------------------------------------------------


class TestResolveWorkPath:
    """resolve_work_path explicit-path shortcut."""

    def test_explicit_path_returned_resolved(self, tmp_path):
        """An explicit path is resolved to absolute and returned."""
        result = resolve_work_path(explicit=str(tmp_path))
        assert result == tmp_path.resolve()

    def test_none_returns_a_path(self):
        """None falls back to CWD walk / CWD and always returns a Path."""
        result = resolve_work_path(explicit=None)
        assert isinstance(result, Path)
        assert result.is_absolute()
