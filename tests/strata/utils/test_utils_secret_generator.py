#!/usr/bin/env python3
"""Tests for `generate_secret`/`mask_secret` (ported from v1)."""

import uuid

import pytest

from strata.utils.secret_generator import FORMATS, generate_secret, mask_secret

# ---------------------------------------------------------------------------
# generate_secret
# ---------------------------------------------------------------------------


def test_urlsafe_is_nonempty_and_url_safe():
    value = generate_secret("urlsafe", 16)
    assert value
    assert all(c.isalnum() or c in "-_" for c in value)


def test_hex_is_lowercase_hex_of_the_right_byte_length():
    value = generate_secret("hex", 8)
    assert len(value) == 16  # 8 bytes -> 16 hex chars
    int(value, 16)  # does not raise


def test_alphanumeric_is_exact_length_and_charset():
    value = generate_secret("alphanumeric", 20)
    assert len(value) == 20
    assert value.isalnum()


def test_numeric_is_exact_length_and_digits_only():
    value = generate_secret("numeric", 6)
    assert len(value) == 6
    assert value.isdigit()


def test_base64_decodes_to_the_requested_byte_length():
    import base64

    value = generate_secret("base64", 12)
    assert len(base64.b64decode(value)) == 12


def test_password_is_exact_length_and_mixes_every_required_class():
    value = generate_secret("password", 12)
    assert len(value) == 12
    assert any(c.isupper() for c in value)
    assert any(c.islower() for c in value)
    assert any(c.isdigit() for c in value)
    assert any(c in "!@#$%^&*()-_=+" for c in value)


def test_password_rejects_length_below_four():
    with pytest.raises(ValueError, match="length >= 4"):
        generate_secret("password", 3)


def test_uuid4_round_trips_through_uuid_parsing():
    value = generate_secret("uuid4", 32)  # length ignored
    parsed = uuid.UUID(value)
    assert parsed.version == 4


def test_uuid7_round_trips_and_has_version_7():
    value = generate_secret("uuid7")
    parsed = uuid.UUID(value)
    assert parsed.version == 7


def test_uuid7_is_time_ordered():
    """The embedded 48-bit millisecond timestamp never goes backwards
    between two successive calls (random tie-break bits can make the full
    UUID string non-monotonic within the same millisecond, so compare the
    timestamp field itself rather than the string)."""
    first_ts = uuid.UUID(generate_secret("uuid7")).int >> 80
    second_ts = uuid.UUID(generate_secret("uuid7")).int >> 80
    assert second_ts >= first_ts


def test_unknown_format_raises_value_error():
    with pytest.raises(ValueError, match="Unknown secret format"):
        generate_secret("rot13", 16)


def test_formats_constant_matches_every_branch_generate_secret_handles():
    for fmt in FORMATS:
        assert generate_secret(fmt, 16)  # never raises, never empty


# ---------------------------------------------------------------------------
# mask_secret
# ---------------------------------------------------------------------------


def test_mask_keeps_the_leading_characters_visible():
    assert mask_secret("hunter2-supersecret", show=4) == "hunt***************"


def test_mask_default_show_is_four():
    assert mask_secret("abcdefgh") == "abcd****"


def test_mask_uses_the_custom_char():
    assert mask_secret("abcdefgh", show=2, char="#") == "ab######"


def test_mask_masks_entirely_when_value_is_not_longer_than_show():
    assert mask_secret("abc", show=4) == "***"
    assert mask_secret("abcd", show=4) == "****"


def test_mask_preserves_the_original_length():
    value = "a-fairly-long-secret-value"
    assert len(mask_secret(value, show=3)) == len(value)
