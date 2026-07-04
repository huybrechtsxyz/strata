#!/usr/bin/env python3
"""Unit tests for repo status tag discovery."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from strata.commands.repo.status_repo_solution_command import StatusRepoSolutionCommand
from strata.integrations.git import TagInfo


class TestRepoStatusTagDiscovery:
    def test_discover_tags_no_tags(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        mock_git.list_tags.return_value = []

        result = cmd._discover_tags(mock_git, "/tmp/repo")

        assert result is None

    def test_discover_tags_release_tag_only(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()

        tag = TagInfo(
            name="v1.2.0",
            commit="abc1234567890abcdef",
            short_commit="abc1234",
            tagger="John Doe",
            created=datetime(2026, 6, 20, 10, 30, 0, tzinfo=timezone.utc),
            message="Release v1.2.0",
            is_annotated=True,
        )
        mock_git.list_tags.return_value = [tag]

        result = cmd._discover_tags(mock_git, "/tmp/repo")

        assert result is not None
        assert "latest_release" in result
        assert result["latest_release"]["name"] == "v1.2.0"
        assert result["latest_release"]["commit"] == "abc1234"

    def test_discover_tags_quality_tag_only(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()

        tag = TagInfo(
            name="tested",
            commit="def5678567890abcdef",
            short_commit="def5678",
            tagger=None,
            created=datetime(2026, 6, 21, 8, 0, 0, tzinfo=timezone.utc),
            message="Quality gate",
            is_annotated=False,
        )
        mock_git.list_tags.return_value = [tag]

        result = cmd._discover_tags(mock_git, "/tmp/repo")

        assert result is not None
        assert "latest_quality" in result
        assert result["latest_quality"]["name"] == "tested"

    def test_discover_tags_both_release_and_quality(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()

        release_tag = TagInfo(
            name="v1.2.0",
            commit="abc1234567890abcdef",
            short_commit="abc1234",
            tagger="John Doe",
            created=datetime(2026, 6, 20, 10, 30, 0, tzinfo=timezone.utc),
            message="Release v1.2.0",
            is_annotated=True,
        )
        quality_tag = TagInfo(
            name="tested",
            commit="def5678567890abcdef",
            short_commit="def5678",
            tagger=None,
            created=datetime(2026, 6, 21, 8, 0, 0, tzinfo=timezone.utc),
            message="Quality gate",
            is_annotated=False,
        )
        mock_git.list_tags.return_value = [quality_tag, release_tag]

        result = cmd._discover_tags(mock_git, "/tmp/repo")

        assert result is not None
        assert "latest_release" in result
        assert "latest_quality" in result
        assert result["latest_release"]["name"] == "v1.2.0"
        assert result["latest_quality"]["name"] == "tested"

    def test_discover_tags_prefers_first_matching(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()

        # Multiple release tags, should pick the first (latest)
        tag1 = TagInfo(
            name="v1.3.0",
            commit="xyz1234567890abcdef",
            short_commit="xyz1234",
            tagger="Jane Doe",
            created=datetime(2026, 6, 21, 14, 0, 0, tzinfo=timezone.utc),
            message="Release v1.3.0",
            is_annotated=True,
        )
        tag2 = TagInfo(
            name="v1.2.0",
            commit="abc1234567890abcdef",
            short_commit="abc1234",
            tagger="John Doe",
            created=datetime(2026, 6, 20, 10, 30, 0, tzinfo=timezone.utc),
            message="Release v1.2.0",
            is_annotated=True,
        )
        mock_git.list_tags.return_value = [tag1, tag2]  # Ordered newest first

        result = cmd._discover_tags(mock_git, "/tmp/repo")

        assert result is not None
        assert result["latest_release"]["name"] == "v1.3.0"

    def test_discover_tags_handles_exception(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()
        mock_git.list_tags.side_effect = Exception("Git command failed")

        result = cmd._discover_tags(mock_git, "/tmp/repo")

        assert result is None

    def test_discover_tags_iso_format_created_date(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()

        created = datetime(2026, 6, 20, 10, 30, 45, tzinfo=timezone.utc)
        tag = TagInfo(
            name="v1.2.0",
            commit="abc1234567890abcdef",
            short_commit="abc1234",
            tagger="John Doe",
            created=created,
            message="Release v1.2.0",
            is_annotated=True,
        )
        mock_git.list_tags.return_value = [tag]

        result = cmd._discover_tags(mock_git, "/tmp/repo")

        assert result is not None
        assert "latest_release" in result
        # ISO format should be preserved
        assert result["latest_release"]["created"] == created.isoformat()

    def test_looks_like_semver(self):
        # True cases
        assert StatusRepoSolutionCommand._looks_like_semver("v1.2.0") is True
        assert StatusRepoSolutionCommand._looks_like_semver("v0.0.1") is True
        assert StatusRepoSolutionCommand._looks_like_semver("v10.20.300") is True

        # False cases
        assert StatusRepoSolutionCommand._looks_like_semver("v1.2") is False
        assert StatusRepoSolutionCommand._looks_like_semver("main") is False
        assert StatusRepoSolutionCommand._looks_like_semver("release-2026-01-01") is False
        assert StatusRepoSolutionCommand._looks_like_semver("tested") is False

    def test_discover_tags_quality_patterns(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()

        tag1 = TagInfo(
            name="tested",
            commit="a1b2c3d4567890abcdef",
            short_commit="a1b2c3d",
            tagger=None,
            created=datetime(2026, 6, 21, 8, 0, 0, tzinfo=timezone.utc),
            message="Quality gate",
            is_annotated=False,
        )
        tag2 = TagInfo(
            name="rc-1",
            commit="e5f6g7h8567890abcdef",
            short_commit="e5f6g7h",
            tagger=None,
            created=datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc),
            message="Release candidate",
            is_annotated=False,
        )
        mock_git.list_tags.return_value = [tag1, tag2]  # tested comes first

        result = cmd._discover_tags(mock_git, "/tmp/repo")

        assert result is not None
        # Should pick the first matching quality tag (tested)
        assert result["latest_quality"]["name"] == "tested"

    def test_discover_tags_age_calculation(self):
        cmd = StatusRepoSolutionCommand()
        mock_git = MagicMock()

        # Create a tag from 5 days ago
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        created = now - timedelta(days=5)

        tag = TagInfo(
            name="v1.2.0",
            commit="abc1234567890abcdef",
            short_commit="abc1234",
            tagger="John Doe",
            created=created,
            message="Release v1.2.0",
            is_annotated=True,
        )
        mock_git.list_tags.return_value = [tag]

        result = cmd._discover_tags(mock_git, "/tmp/repo")

        assert result is not None
        assert result["latest_release"]["age_days"] == 5
        assert "days ago" in result["latest_release"]["age_str"]
