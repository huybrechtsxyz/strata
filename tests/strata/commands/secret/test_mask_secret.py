"""Unit tests for the mask_secret() function."""

from strata.commands.secret.mask_secret_command import mask_secret


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
        assert result == "pass" + "X" * 7  # 11 chars - 4 shown = 7 masked

    def test_mask_custom_char_dash(self):
        """Custom character dash."""
        result = mask_secret("token-abcdef", show=3, char="-")
        assert result == "tok" + "-" * 9


class TestMaskSecretShortValues:
    """Test behavior with values shorter than or equal to show parameter."""

    def test_mask_short_value_show_4_length_2(self):
        """Value shorter than show is fully masked."""
        result = mask_secret("ab", show=4)
        # len(value) = 2 <= show = 4, so fully masked
        assert result == "**"
        assert len(result) == 2

    def test_mask_short_value_show_4_length_4(self):
        """Value equal to show is fully masked (safety margin)."""
        result = mask_secret("abcd", show=4)
        # len(value) = 4 <= show = 4, so fully masked
        assert result == "****"

    def test_mask_short_value_show_4_length_5(self):
        """Value of length 5 with show=4 shows first 4."""
        result = mask_secret("abcde", show=4)
        # len(value) = 5 > show = 4, so show first 4
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
        # 15 chars - 2 shown = 13 masked (spaces are masked too)
        assert result == "my" + "*" * 13

    def test_mask_with_special_chars(self):
        """Special characters in visible portion are shown."""
        result = mask_secret("api-key-secret", show=7)
        # 14 chars - 7 shown = 7 masked with default '*' char
        assert result == "api-key" + "*" * 7

    def test_mask_unicode_characters(self):
        """Unicode characters are handled."""
        result = mask_secret("café_secret", show=4)
        # The first 4 characters should be visible, rest masked
        assert len(result) == len("café_secret")

    def test_mask_newlines(self):
        """Newlines are masked like other characters."""
        result = mask_secret("line1\nline2", show=5)
        # 11 chars - 5 shown = 6 masked (newline gets masked)
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
        value = "secret123"
        result = mask_secret(value, show=8)
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
        visible = result[:8]
        assert visible == value[:8]


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
