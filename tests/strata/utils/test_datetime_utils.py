"""Tests for strata.utils.datetime_utils."""

import pytest

try:
    import click

    from strata.utils.datetime_utils import (
        coerce_to_utc,
        format_display_timestamp,
        now_utc,
        parse_iso_timestamp,
        to_wire_timestamp,
    )

    IMPL_MISSING = False
except ImportError:
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="datetime_utils not available")


# ===========================================================================
# now_utc
# ===========================================================================


class TestNowUtc:
    def test_returns_aware_datetime(self):
        dt = now_utc()
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0  # UTC

    def test_returns_current_time(self):
        import time

        before = now_utc()
        time.sleep(0.001)
        after = now_utc()
        assert after >= before


# ===========================================================================
# to_wire_timestamp
# ===========================================================================


class TestToWireTimestamp:
    def test_produces_plus_zero_zero_suffix(self):
        from datetime import datetime, timezone

        dt = datetime(2026, 7, 20, 14, 30, 0, tzinfo=timezone.utc)
        result = to_wire_timestamp(dt)
        assert result == "2026-07-20T14:30:00+00:00"

    def test_aware_non_utc_converted(self):
        from datetime import datetime, timedelta, timezone

        tz_plus2 = timezone(timedelta(hours=2))
        dt = datetime(2026, 7, 20, 16, 30, 0, tzinfo=tz_plus2)  # 14:30 UTC
        result = to_wire_timestamp(dt)
        assert "+00:00" in result
        assert "14:30" in result

    def test_microseconds_preserved(self):
        from datetime import datetime, timezone

        dt = datetime(2026, 7, 20, 14, 30, 0, 123456, tzinfo=timezone.utc)
        result = to_wire_timestamp(dt)
        assert "123456" in result


# ===========================================================================
# format_display_timestamp
# ===========================================================================


class TestFormatDisplayTimestamp:
    def test_produces_human_readable_format(self):
        from datetime import datetime, timezone

        dt = datetime(2026, 7, 20, 14, 30, 0, tzinfo=timezone.utc)
        result = format_display_timestamp(dt)
        assert result == "2026-07-20 14:30:00 UTC"

    def test_aware_non_utc_converted_for_display(self):
        from datetime import datetime, timedelta, timezone

        tz_plus5 = timezone(timedelta(hours=5))
        dt = datetime(2026, 7, 20, 19, 30, 0, tzinfo=tz_plus5)  # 14:30 UTC
        result = format_display_timestamp(dt)
        assert result == "2026-07-20 14:30:00 UTC"

    def test_no_microseconds_in_display(self):
        from datetime import datetime, timezone

        dt = datetime(2026, 7, 20, 14, 30, 0, 999999, tzinfo=timezone.utc)
        result = format_display_timestamp(dt)
        # strftime doesn't include microseconds
        assert "999999" not in result
        assert "UTC" in result


# ===========================================================================
# parse_iso_timestamp
# ===========================================================================


class TestParseIsoTimestamp:
    def test_parses_full_iso_with_offset(self):
        dt = parse_iso_timestamp("2026-07-20T14:30:00+00:00")
        assert dt.tzinfo is not None
        assert dt.year == 2026
        assert dt.hour == 14

    def test_parses_z_suffix(self):
        dt = parse_iso_timestamp("2026-07-20T14:30:00Z")
        assert dt.hour == 14

    def test_z_suffix_normalised_to_utc(self):
        dt = parse_iso_timestamp("2026-07-20T14:30:00Z")
        assert dt.utcoffset().total_seconds() == 0

    def test_parses_date_only_shorthand(self):
        dt = parse_iso_timestamp("2026-07-20")
        assert dt.year == 2026
        assert dt.month == 7
        assert dt.day == 20
        assert dt.hour == 0
        assert dt.minute == 0

    def test_non_utc_offset_converted_to_utc(self):
        dt = parse_iso_timestamp("2026-07-20T16:30:00+02:00")
        assert dt.hour == 14  # 16:30+02:00 → 14:30 UTC

    def test_naive_datetime_raises(self):
        with pytest.raises(click.BadParameter, match="no timezone"):
            parse_iso_timestamp("2026-07-20T14:30:00")

    def test_invalid_string_raises(self):
        with pytest.raises(click.BadParameter, match="not a valid"):
            parse_iso_timestamp("not-a-date")

    def test_empty_string_raises(self):
        with pytest.raises((click.BadParameter, ValueError)):
            parse_iso_timestamp("")

    def test_result_is_always_utc(self):
        from datetime import timezone

        dt = parse_iso_timestamp("2026-07-20T10:00:00+05:30")
        assert dt.tzinfo == timezone.utc


# ===========================================================================
# coerce_to_utc
# ===========================================================================


class TestCoerceToUtc:
    def test_none_returns_none(self):
        assert coerce_to_utc(None) is None

    def test_naive_datetime_tagged_as_utc(self):
        from datetime import datetime, timezone

        naive = datetime(2026, 7, 20, 14, 30, 0)
        result = coerce_to_utc(naive)
        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.hour == 14  # value preserved

    def test_aware_datetime_converted_to_utc(self):
        from datetime import datetime, timedelta, timezone

        tz_plus3 = timezone(timedelta(hours=3))
        aware = datetime(2026, 7, 20, 17, 30, 0, tzinfo=tz_plus3)
        result = coerce_to_utc(aware)
        assert result is not None
        assert result.utcoffset().total_seconds() == 0
        assert result.hour == 14  # 17:30+03:00 → 14:30 UTC

    def test_already_utc_unchanged(self):
        dt = now_utc()
        result = coerce_to_utc(dt)
        assert result == dt
