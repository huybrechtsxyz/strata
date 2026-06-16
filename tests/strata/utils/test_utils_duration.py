"""Unit tests for parse_duration."""

import pytest

from strata.utils.duration import parse_duration


class TestParseDurationValid:
    def test_minutes_only(self):
        assert parse_duration("30m") == 1800

    def test_hours_only(self):
        assert parse_duration("8h") == 28800

    def test_seconds_only(self):
        assert parse_duration("60s") == 60

    def test_hours_and_minutes(self):
        assert parse_duration("2h30m") == 9000

    def test_hours_minutes_seconds(self):
        assert parse_duration("1h5m30s") == 3930

    def test_hours_and_seconds(self):
        assert parse_duration("1h30s") == 3630

    def test_minutes_and_seconds(self):
        assert parse_duration("5m10s") == 310

    def test_zero_seconds(self):
        assert parse_duration("0s") == 0

    def test_large_minutes(self):
        assert parse_duration("120m") == 7200

    def test_leading_whitespace_accepted(self):
        assert parse_duration("  30m") == 1800

    def test_trailing_whitespace_accepted(self):
        assert parse_duration("30m  ") == 1800


class TestParseDurationInvalid:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_duration("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            parse_duration("   ")

    def test_bare_number_raises(self):
        with pytest.raises(ValueError):
            parse_duration("30")

    def test_unknown_suffix_raises(self):
        with pytest.raises(ValueError):
            parse_duration("30d")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            parse_duration("xhym")

    def test_error_message_includes_value(self):
        with pytest.raises(ValueError, match="30d"):
            parse_duration("30d")
