#!/usr/bin/env python3
"""Unit tests for GitIntegration.list_tags()."""

from unittest.mock import MagicMock, patch

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.git import GitIntegration, TagInfo
from strata.models.integration_model import IntegrationModel


def _cfg(name="git") -> IntegrationModel:
    return IntegrationModel(name=name, type="git")


class TestGitListTagsBasic:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_list_tags_returns_empty_when_git_unavailable(self):
        i = GitIntegration(_cfg())
        with patch.object(i, "ensure_available", return_value=(False, "git not available")):
            result = i.list_tags("/tmp/repo")
        assert result == []

    def test_list_tags_returns_empty_when_no_tags(self):
        i = GitIntegration(_cfg())
        i._is_available = True
        i._version = "2.40.0"
        mock_result = MagicMock(returncode=0, stdout="")
        with patch.object(i, "_run_integration", return_value=mock_result):
            result = i.list_tags("/tmp/repo")
        assert result == []

    def test_list_tags_returns_empty_on_command_failure(self):
        i = GitIntegration(_cfg())
        i._is_available = True
        i._version = "2.40.0"
        mock_result = MagicMock(returncode=1, stdout="", stderr="command failed")
        with patch.object(i, "_run_integration", return_value=mock_result):
            result = i.list_tags("/tmp/repo")
        assert result == []


class TestGitListTagsParsing:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_single_tag(self):
        i = GitIntegration(_cfg())
        i._is_available = True
        i._version = "2.40.0"

        tag_output = "v1.2.0|abc1234|2026-06-20T10:30:00+00:00|John Doe|Release v1.2.0"
        mock_result = MagicMock(returncode=0, stdout=tag_output)

        # Mock both the tag list and rev-list calls
        with patch.object(i, "_run_integration") as mock_run:
            # First call returns tags, second call returns full SHA
            mock_run.side_effect = [
                mock_result,  # list_tags output
                MagicMock(returncode=0, stdout="abc1234567890abcdef"),  # rev-list for SHA
            ]
            result = i.list_tags("/tmp/repo")

        assert len(result) == 1
        tag = result[0]
        assert tag.name == "v1.2.0"
        assert tag.short_commit == "abc1234"
        assert tag.tagger == "John Doe"
        assert tag.message == "Release v1.2.0"
        assert tag.is_annotated is True

    def test_parse_multiple_tags(self):
        i = GitIntegration(_cfg())
        i._is_available = True
        i._version = "2.40.0"

        tag_output = """v1.2.0|abc1234|2026-06-20T10:30:00+00:00|John Doe|Release v1.2.0
v1.1.0|def5678|2026-06-10T14:15:00+00:00|Jane Doe|Release v1.1.0
tested|ghi9abc|2026-06-21T08:00:00+00:00||Quality gate"""
        mock_result = MagicMock(returncode=0, stdout=tag_output)

        with patch.object(i, "_run_integration") as mock_run:
            mock_run.side_effect = [
                mock_result,  # list_tags output
                MagicMock(returncode=0, stdout="abc1234567890abcdef"),  # rev-list
                MagicMock(returncode=0, stdout="def5678567890abcdef"),  # rev-list
                MagicMock(returncode=0, stdout="ghi9abc567890abcdef"),  # rev-list
            ]
            result = i.list_tags("/tmp/repo")

        assert len(result) == 3
        assert result[0].name == "v1.2.0"
        assert result[1].name == "v1.1.0"
        assert result[2].name == "tested"

    def test_skip_malformed_lines(self):
        i = GitIntegration(_cfg())
        i._is_available = True
        i._version = "2.40.0"

        tag_output = """v1.2.0|abc1234|2026-06-20T10:30:00+00:00|John Doe|Release
invalid line with no pipes
v1.1.0|def5678|2026-06-10T14:15:00+00:00|Jane Doe|Another"""
        mock_result = MagicMock(returncode=0, stdout=tag_output)

        with patch.object(i, "_run_integration") as mock_run:
            mock_run.side_effect = [
                mock_result,
                MagicMock(returncode=0, stdout="abc1234567890abcdef"),
                MagicMock(returncode=0, stdout="def5678567890abcdef"),
            ]
            result = i.list_tags("/tmp/repo")

        # Should skip the malformed line
        assert len(result) == 2


class TestGitListTagsPattern:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_list_tags_with_pattern(self):
        i = GitIntegration(_cfg())
        i._is_available = True
        i._version = "2.40.0"

        with patch.object(i, "_run_integration") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            i.list_tags("/tmp/repo", pattern="^v[0-9]")

        # Check that pattern was passed to git command
        call_args = mock_run.call_args[0][0]
        assert "^v[0-9]" in call_args

    def test_list_tags_with_sort_order(self):
        i = GitIntegration(_cfg())
        i._is_available = True
        i._version = "2.40.0"

        with patch.object(i, "_run_integration") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            i.list_tags("/tmp/repo", sort="version:refname")

        call_args = mock_run.call_args[0][0]
        assert "version:refname" in call_args


class TestTagInfoProperties:
    def test_age_days_calculation(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        # Create a tag from 5 days ago
        created = now - timedelta(days=5)

        tag = TagInfo(
            name="v1.0.0",
            commit="abc123",
            short_commit="abc123",
            created=created,
            is_annotated=True,
        )

        assert tag.age_days == 5

    def test_age_str_today(self):
        from datetime import datetime, timezone

        tag = TagInfo(
            name="v1.0.0",
            commit="abc123",
            short_commit="abc123",
            created=datetime.now(timezone.utc),
            is_annotated=True,
        )

        assert tag.age_str == "today"

    def test_age_str_none_when_no_created_date(self):
        tag = TagInfo(
            name="v1.0.0",
            commit="abc123",
            short_commit="abc123",
            created=None,
            is_annotated=False,
        )

        assert tag.age_str == "unknown"
