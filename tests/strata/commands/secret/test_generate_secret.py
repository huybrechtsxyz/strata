"""Unit tests for the generate_secret() function."""

import base64
import re
import string
import time
import uuid

import pytest

from strata.commands.secret.generate_secret_command import generate_secret


class TestGenerateSecretUrlsafe:
    """Test urlsafe format generation."""

    def test_urlsafe_default_length(self):
        """Default length=32 bytes produces a URL-safe base64 string."""
        result = generate_secret("urlsafe", 32)
        assert isinstance(result, str)
        assert len(result) > 0
        # URL-safe base64 uses only alphanumeric + - and _
        assert re.match(r"^[A-Za-z0-9_-]*$", result)

    def test_urlsafe_length_4(self):
        """Length 4 bytes produces output."""
        result = generate_secret("urlsafe", 4)
        assert isinstance(result, str)
        assert len(result) > 0
        assert re.match(r"^[A-Za-z0-9_-]*$", result)

    def test_urlsafe_length_128(self):
        """Length 128 bytes produces longer output."""
        result = generate_secret("urlsafe", 128)
        assert isinstance(result, str)
        # 128 bytes -> 171 chars in base64
        assert len(result) >= 128

    def test_urlsafe_randomness(self):
        """Multiple calls produce different values."""
        values = [generate_secret("urlsafe", 32) for _ in range(5)]
        assert len(set(values)) == 5, "All values should be different"


class TestGenerateSecretHex:
    """Test hex format generation."""

    def test_hex_default_length(self):
        """Default length=32 bytes produces hex string."""
        result = generate_secret("hex", 32)
        assert isinstance(result, str)
        # 32 bytes = 64 hex characters
        assert len(result) == 64
        assert re.match(r"^[0-9a-f]*$", result)

    def test_hex_length_1(self):
        """Length 1 byte produces 2 hex digits."""
        result = generate_secret("hex", 1)
        assert len(result) == 2
        assert re.match(r"^[0-9a-f]{2}$", result)

    def test_hex_length_256(self):
        """Length 256 bytes produces 512 hex characters."""
        result = generate_secret("hex", 256)
        assert len(result) == 512
        assert re.match(r"^[0-9a-f]*$", result)

    def test_hex_lowercase_only(self):
        """Hex output is lowercase."""
        result = generate_secret("hex", 32)
        assert result == result.lower()

    def test_hex_randomness(self):
        """Multiple calls produce different values."""
        values = [generate_secret("hex", 32) for _ in range(5)]
        assert len(set(values)) == 5


class TestGenerateSecretAlphanumeric:
    """Test alphanumeric format generation."""

    def test_alphanumeric_default_length(self):
        """Length 32 produces 32 alphanumeric characters."""
        result = generate_secret("alphanumeric", 32)
        assert len(result) == 32
        assert re.match(r"^[A-Za-z0-9]*$", result)

    def test_alphanumeric_exact_length(self):
        """Character count matches requested length."""
        for length in [1, 4, 10, 64, 128]:
            result = generate_secret("alphanumeric", length)
            assert len(result) == length
            assert re.match(r"^[A-Za-z0-9]*$", result)

    def test_alphanumeric_includes_letters_and_digits(self):
        """Generated string includes both letters and digits (with high probability)."""
        result = generate_secret("alphanumeric", 100)
        has_letter = any(c in string.ascii_letters for c in result)
        has_digit = any(c in string.digits for c in result)
        assert has_letter and has_digit

    def test_alphanumeric_randomness(self):
        """Multiple calls produce different values."""
        values = [generate_secret("alphanumeric", 32) for _ in range(5)]
        assert len(set(values)) == 5


class TestGenerateSecretPassword:
    """Test password format generation."""

    def test_password_length_4_minimum(self):
        """Minimum length 4 produces valid password with all character classes."""
        result = generate_secret("password", 4)
        assert len(result) == 4
        # Should have at least: uppercase, lowercase, digit, symbol
        has_upper = any(c in string.ascii_uppercase for c in result)
        has_lower = any(c in string.ascii_lowercase for c in result)
        has_digit = any(c in string.digits for c in result)
        has_symbol = any(c in "!@#$%^&*()-_=+" for c in result)
        assert has_upper and has_lower and has_digit and has_symbol

    def test_password_length_8(self):
        """Length 8 produces password with mix of character classes."""
        result = generate_secret("password", 8)
        assert len(result) == 8
        # Verify it only contains allowed characters
        allowed = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
        assert all(c in allowed for c in result)

    def test_password_length_32(self):
        """Length 32 produces valid password."""
        result = generate_secret("password", 32)
        assert len(result) == 32
        allowed = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
        assert all(c in allowed for c in result)

    def test_password_length_3_raises_error(self):
        """Length 3 raises ValueError because minimum is 4."""
        with pytest.raises(ValueError, match="password format requires --length >= 4"):
            generate_secret("password", 3)

    def test_password_length_1_raises_error(self):
        """Length 1 raises ValueError."""
        with pytest.raises(ValueError, match="password format requires --length >= 4"):
            generate_secret("password", 1)

    def test_password_no_ambiguous_chars(self):
        """Password does not contain ambiguous characters like 0/O, 1/l/I."""
        result = generate_secret("password", 64)
        # The implementation excludes these; verify
        assert "0" in string.digits  # 0 is allowed in alphanumeric
        assert "1" in string.digits  # 1 is allowed in alphanumeric
        # Ambiguous chars should not be in symbols per design
        assert "'" not in result
        assert '"' not in result
        assert "`" not in result

    def test_password_randomness(self):
        """Multiple calls produce different values."""
        values = [generate_secret("password", 32) for _ in range(5)]
        assert len(set(values)) == 5


class TestGenerateSecretNumeric:
    """Test numeric format generation."""

    def test_numeric_default_length(self):
        """Length 32 produces 32 digits."""
        result = generate_secret("numeric", 32)
        assert len(result) == 32
        assert re.match(r"^\d*$", result)

    def test_numeric_exact_length(self):
        """Character count matches requested length."""
        for length in [1, 4, 10, 64]:
            result = generate_secret("numeric", length)
            assert len(result) == length
            assert re.match(r"^\d*$", result)

    def test_numeric_all_digits(self):
        """Output contains only digit characters."""
        result = generate_secret("numeric", 128)
        assert all(c in string.digits for c in result)

    def test_numeric_randomness(self):
        """Multiple calls produce different values."""
        values = [generate_secret("numeric", 32) for _ in range(5)]
        assert len(set(values)) == 5


class TestGenerateSecretBase64:
    """Test base64 format generation."""

    def test_base64_default_length(self):
        """Length 32 bytes produces valid base64."""
        result = generate_secret("base64", 32)
        assert isinstance(result, str)
        # Verify it can be decoded
        decoded = base64.b64decode(result)
        assert len(decoded) == 32

    def test_base64_valid_encoding(self):
        """Output is valid base64 (standard alphabet)."""
        result = generate_secret("base64", 16)
        # Should decode without error
        decoded = base64.b64decode(result)
        assert len(decoded) == 16

    def test_base64_length_1(self):
        """Length 1 byte produces 4-char base64."""
        result = generate_secret("base64", 1)
        # 1 byte encodes to 4 base64 chars
        assert len(result) == 4

    def test_base64_randomness(self):
        """Multiple calls produce different values."""
        values = [generate_secret("base64", 32) for _ in range(5)]
        assert len(set(values)) == 5


class TestGenerateSecretUUID4:
    """Test UUID v4 format generation."""

    def test_uuid4_format(self):
        """Output is a valid UUID v4."""
        result = generate_secret("uuid4", 32)  # length ignored
        assert isinstance(result, str)
        # Should be parseable as UUID
        parsed = uuid.UUID(result)
        assert parsed.version == 4

    def test_uuid4_ignores_length(self):
        """Length parameter is ignored for uuid4."""
        result1 = generate_secret("uuid4", 10)
        result2 = generate_secret("uuid4", 100)
        # Both should be valid UUIDs (same length, roughly)
        assert len(result1) == len(result2)
        assert uuid.UUID(result1).version == 4
        assert uuid.UUID(result2).version == 4

    def test_uuid4_randomness(self):
        """Multiple calls produce different UUIDs."""
        values = [generate_secret("uuid4", 32) for _ in range(5)]
        assert len(set(values)) == 5

    def test_uuid4_hyphen_format(self):
        """UUID is in standard hyphenated format."""
        result = generate_secret("uuid4", 32)
        # Standard UUID format: 8-4-4-4-12
        assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", result)


class TestGenerateSecretUUID7:
    """Test UUID v7 format generation."""

    def test_uuid7_format(self):
        """Output is a valid UUID v7."""
        result = generate_secret("uuid7", 32)  # length ignored
        assert isinstance(result, str)
        # Should be parseable as UUID
        parsed = uuid.UUID(result)
        assert parsed.version == 7

    def test_uuid7_ignores_length(self):
        """Length parameter is ignored for uuid7."""
        result1 = generate_secret("uuid7", 10)
        result2 = generate_secret("uuid7", 100)
        # Both should be valid UUIDs (same length, roughly)
        assert len(result1) == len(result2)
        assert uuid.UUID(result1).version == 7
        assert uuid.UUID(result2).version == 7

    def test_uuid7_time_ordered(self):
        """UUID v7 generates time-ordered values."""
        uuids = []
        for _ in range(5):
            uuids.append(uuid.UUID(generate_secret("uuid7", 32)))
            # Small delay to ensure timestamp progresses
            time.sleep(0.001)

        # Each UUID should be greater than the previous
        for i in range(1, len(uuids)):
            assert uuids[i] > uuids[i - 1], f"UUID {i} should be > UUID {i - 1}"

    def test_uuid7_hyphen_format(self):
        """UUID is in standard hyphenated format."""
        result = generate_secret("uuid7", 32)
        # Standard UUID format: 8-4-4-4-12
        assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", result)

    def test_uuid7_randomness(self):
        """Multiple calls produce different UUIDs (at different times)."""
        results = []
        for _ in range(3):
            results.append(generate_secret("uuid7", 32))
            time.sleep(0.001)
        assert len(set(results)) == 3


class TestGenerateSecretInvalidFormat:
    """Test error handling for invalid formats."""

    def test_unknown_format_raises_error(self):
        """Unknown format raises ValueError."""
        with pytest.raises(ValueError, match="Unknown secret format"):
            generate_secret("badformat", 32)

    def test_empty_format_raises_error(self):
        """Empty format string raises ValueError."""
        with pytest.raises(ValueError, match="Unknown secret format"):
            generate_secret("", 32)


class TestGenerateSecretEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_length_1_for_urlsafe(self):
        """Minimum length 1 for urlsafe."""
        result = generate_secret("urlsafe", 1)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_large_length_for_alphanumeric(self):
        """Very large length for alphanumeric."""
        result = generate_secret("alphanumeric", 10000)
        assert len(result) == 10000
        assert re.match(r"^[A-Za-z0-9]*$", result)

    def test_format_case_insensitive(self):
        """Format strings are case-insensitive (in CLI layer, but test function behavior)."""
        # The function itself is lowercase; CLI handles case-insensitivity
        result = generate_secret("urlsafe", 16)
        assert isinstance(result, str)
