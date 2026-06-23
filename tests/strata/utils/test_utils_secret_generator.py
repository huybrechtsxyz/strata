"""Unit tests for strata.utils.secret_generator (generate_secret + mask_secret)."""

import base64
import re
import string
import time
import uuid

import pytest

from strata.utils.secret_generator import generate_secret, mask_secret

# ---------------------------------------------------------------------------
# generate_secret
# ---------------------------------------------------------------------------


class TestGenerateSecretUrlsafe:
    """Test urlsafe format generation."""

    def test_urlsafe_default_length(self):
        """Default length=32 bytes produces a URL-safe base64 string."""
        result = generate_secret("urlsafe", 32)
        assert isinstance(result, str)
        assert len(result) > 0
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
        has_upper = any(c in string.ascii_uppercase for c in result)
        has_lower = any(c in string.ascii_lowercase for c in result)
        has_digit = any(c in string.digits for c in result)
        has_symbol = any(c in "!@#$%^&*()-_=+" for c in result)
        assert has_upper and has_lower and has_digit and has_symbol

    def test_password_length_8(self):
        """Length 8 produces password with mix of character classes."""
        result = generate_secret("password", 8)
        assert len(result) == 8
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
        with pytest.raises(ValueError, match="password format requires length >= 4"):
            generate_secret("password", 3)

    def test_password_length_1_raises_error(self):
        """Length 1 raises ValueError."""
        with pytest.raises(ValueError, match="password format requires length >= 4"):
            generate_secret("password", 1)

    def test_password_no_shell_special_chars(self):
        """Password does not contain shell-special or ambiguous characters."""
        result = generate_secret("password", 64)
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
        decoded = base64.b64decode(result)
        assert len(decoded) == 32

    def test_base64_valid_encoding(self):
        """Output is valid base64 (standard alphabet)."""
        result = generate_secret("base64", 16)
        decoded = base64.b64decode(result)
        assert len(decoded) == 16

    def test_base64_length_1(self):
        """Length 1 byte produces 4-char base64."""
        result = generate_secret("base64", 1)
        assert len(result) == 4

    def test_base64_randomness(self):
        """Multiple calls produce different values."""
        values = [generate_secret("base64", 32) for _ in range(5)]
        assert len(set(values)) == 5


class TestGenerateSecretUUID4:
    """Test UUID v4 format generation."""

    def test_uuid4_format(self):
        """Output is a valid UUID v4."""
        result = generate_secret("uuid4", 32)
        parsed = uuid.UUID(result)
        assert parsed.version == 4

    def test_uuid4_ignores_length(self):
        """Length parameter is ignored for uuid4."""
        result1 = generate_secret("uuid4", 10)
        result2 = generate_secret("uuid4", 100)
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
        assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", result)


class TestGenerateSecretUUID7:
    """Test UUID v7 format generation."""

    def test_uuid7_format(self):
        """Output is a valid UUID v7."""
        result = generate_secret("uuid7", 32)
        parsed = uuid.UUID(result)
        assert parsed.version == 7

    def test_uuid7_ignores_length(self):
        """Length parameter is ignored for uuid7."""
        result1 = generate_secret("uuid7", 10)
        result2 = generate_secret("uuid7", 100)
        assert len(result1) == len(result2)
        assert uuid.UUID(result1).version == 7
        assert uuid.UUID(result2).version == 7

    def test_uuid7_time_ordered(self):
        """UUID v7 generates time-ordered values."""
        uuids = []
        for _ in range(5):
            uuids.append(uuid.UUID(generate_secret("uuid7", 32)))
            time.sleep(0.001)
        for i in range(1, len(uuids)):
            assert uuids[i] > uuids[i - 1]

    def test_uuid7_hyphen_format(self):
        """UUID is in standard hyphenated format."""
        result = generate_secret("uuid7", 32)
        assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", result)

    def test_uuid7_randomness(self):
        """Multiple calls produce different UUIDs."""
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


# ---------------------------------------------------------------------------
# mask_secret
# ---------------------------------------------------------------------------


class TestMaskSecretBasic:
    """Test basic masking functionality."""

    def test_mask_default_show_4(self):
        """Default show=4 keeps first 4 chars visible."""
        result = mask_secret("mysecretpassword")
        assert result == "myse" + "*" * 12
        assert len(result) == len("mysecretpassword")

    def test_mask_preserves_length(self):
        """Output length equals input length."""
        value = "verylongsecrettoken123456"
        result = mask_secret(value, show=4)
        assert len(result) == len(value)

    def test_mask_custom_show_8(self):
        """Show 8 chars keeps first 8 visible."""
        result = mask_secret("databasepassword123", show=8)
        assert result == "database" + "*" * 11
        assert len(result) == 19

    def test_mask_show_0(self):
        """Show 0 masks entire value."""
        result = mask_secret("secret", show=0)
        assert result == "******"

    def test_mask_custom_char_hash(self):
        """Custom mask character is used."""
        result = mask_secret("mysecret", show=2, char="#")
        assert result == "my" + "#" * 6
        assert "#" in result
        assert "*" not in result

    def test_mask_custom_char_x(self):
        """Custom character X."""
        result = mask_secret("password123", show=4, char="X")
        assert result == "pass" + "X" * 7

    def test_mask_custom_char_dash(self):
        """Custom character dash."""
        result = mask_secret("token-abcdef", show=3, char="-")
        assert result == "tok" + "-" * 9


class TestMaskSecretShortValues:
    """Test behavior with values shorter than or equal to show parameter."""

    def test_mask_short_value_show_4_length_2(self):
        """Value shorter than show is fully masked."""
        result = mask_secret("ab", show=4)
        assert result == "**"
        assert len(result) == 2

    def test_mask_short_value_show_4_length_4(self):
        """Value equal to show is fully masked (safety margin)."""
        result = mask_secret("abcd", show=4)
        assert result == "****"

    def test_mask_short_value_show_4_length_5(self):
        """Value of length 5 with show=4 shows first 4."""
        result = mask_secret("abcde", show=4)
        assert result == "abcd*"

    def test_mask_empty_string(self):
        """Empty string masked is empty."""
        result = mask_secret("", show=4)
        assert result == ""

    def test_mask_single_char_show_4(self):
        """Single character with show=4 is fully masked."""
        result = mask_secret("X", show=4)
        assert result == "*"

    def test_mask_single_char_show_0(self):
        """Single character with show=0 is masked."""
        result = mask_secret("X", show=0)
        assert result == "*"

    def test_mask_show_larger_than_value(self):
        """Show parameter larger than value fully masks."""
        result = mask_secret("secret", show=100)
        assert result == "**" * 3


class TestMaskSecretEdgeCases:
    """Test edge cases and special scenarios."""

    def test_mask_with_spaces(self):
        """Spaces are masked like other characters."""
        result = mask_secret("my secret token", show=2)
        assert result == "my" + "*" * 13

    def test_mask_with_special_chars(self):
        """Special characters in visible portion are shown."""
        result = mask_secret("api-key-secret", show=7)
        assert result == "api-key" + "*" * 7

    def test_mask_unicode_characters(self):
        """Unicode characters are handled."""
        result = mask_secret("café_secret", show=4)
        assert len(result) == len("café_secret")

    def test_mask_newlines(self):
        """Newlines are masked like other characters."""
        result = mask_secret("line1\nline2", show=5)
        assert len(result) == 11
        assert result == "line1" + "*" * 6

    def test_mask_very_long_value(self):
        """Very long values are masked correctly."""
        long_value = "x" * 10000
        result = mask_secret(long_value, show=10)
        assert len(result) == 10000
        assert result == "x" * 10 + "*" * 9990

    def test_mask_show_equals_length_minus_1(self):
        """Show one less than length shows all but last char."""
        result = mask_secret("secret123", show=8)
        assert result == "secret12" + "*"

    def test_mask_preserves_value_semantics(self):
        """Masking does not lose information about length."""
        values = ["a", "ab", "abc", "abcd", "abcde"]
        results = [mask_secret(v, show=4) for v in values]
        assert [len(r) for r in results] == [len(v) for v in values]


class TestMaskSecretWithCharParameter:
    """Test the char parameter extensively."""

    def test_mask_char_space(self):
        """Space as mask character."""
        result = mask_secret("secret", show=2, char=" ")
        assert result == "se    "

    def test_mask_char_digit(self):
        """Digit as mask character."""
        result = mask_secret("mysecret", show=2, char="0")
        assert result == "my" + "0" * 6

    def test_mask_char_lowercase(self):
        """Lowercase letter as mask character."""
        result = mask_secret("password", show=4, char="x")
        assert result == "pass" + "x" * 4

    def test_mask_char_uppercase(self):
        """Uppercase letter as mask character."""
        result = mask_secret("PASSWORD", show=4, char="X")
        assert result == "PASS" + "X" * 4

    def test_mask_char_symbol(self):
        """Various symbols as mask character."""
        for char in ["#", "@", "$", "%", "^", "&"]:
            result = mask_secret("secret", show=2, char=char)
            assert all(c == char for c in result[2:])


class TestMaskSecretReturnProperties:
    """Test properties of the returned string."""

    def test_return_type_is_string(self):
        """Return value is always a string."""
        result = mask_secret("secret", show=4)
        assert isinstance(result, str)

    def test_masked_chars_are_only_mask_char(self):
        """All masked positions contain exactly the mask character."""
        result = mask_secret("verylongsecret", show=5, char="#")
        visible = result[:5]
        masked = result[5:]
        assert visible == "veryl"
        assert all(c == "#" for c in masked)

    def test_visible_chars_match_input(self):
        """Visible portion matches input exactly."""
        value = "databasepassword"
        result = mask_secret(value, show=8, char="*")
        assert result[:8] == value[:8]


class TestMaskSecretConsistency:
    """Test consistency of masking across different calls."""

    def test_same_input_produces_same_output(self):
        """Masking is deterministic."""
        value = "secret_token_12345"
        result1 = mask_secret(value, show=5, char="*")
        result2 = mask_secret(value, show=5, char="*")
        assert result1 == result2

    def test_different_show_produces_different_output(self):
        """Different show values produce different outputs."""
        value = "verylongtoken"
        result1 = mask_secret(value, show=4)
        result2 = mask_secret(value, show=8)
        assert result1 != result2

    def test_different_char_produces_different_output(self):
        """Different mask characters produce different outputs."""
        value = "secret"
        result1 = mask_secret(value, show=2, char="*")
        result2 = mask_secret(value, show=2, char="#")
        assert result1 != result2
        assert result1 == "se****"
        assert result2 == "se####"
