"""Integration tests for the ``secret`` command group via Click CLI."""

import json
import re
import uuid

from click.testing import CliRunner

from strata.commands.cli_secret import secret_group


class TestGenerateSecretCommandBasic:
    """Test the generate command with basic options."""

    def test_generate_command_default_format(self):
        """generate command with no options uses urlsafe format."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate"])
        assert result.exit_code == 0
        value = result.output.strip()
        # urlsafe base64 alphabet
        assert re.match(r"^[A-Za-z0-9_-]*$", value)

    def test_generate_command_explicit_urlsafe(self):
        """generate --format urlsafe."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "urlsafe"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert re.match(r"^[A-Za-z0-9_-]*$", value)

    def test_generate_command_hex_format(self):
        """generate --format hex."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "hex"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert re.match(r"^[0-9a-f]*$", value)
        assert len(value) == 64  # 32 bytes = 64 hex chars

    def test_generate_command_alphanumeric_format(self):
        """generate --format alphanumeric."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "alphanumeric"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert re.match(r"^[A-Za-z0-9]*$", value)
        assert len(value) == 32  # Default length

    def test_generate_command_password_format(self):
        """generate --format password."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "password"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert len(value) == 32

    def test_generate_command_numeric_format(self):
        """generate --format numeric."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "numeric"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert re.match(r"^\d*$", value)

    def test_generate_command_base64_format(self):
        """generate --format base64."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "base64"])
        assert result.exit_code == 0
        value = result.output.strip()
        # Valid base64 should decode without error
        import base64

        decoded = base64.b64decode(value)
        assert len(decoded) == 32

    def test_generate_command_uuid4_format(self):
        """generate --format uuid4."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "uuid4"])
        assert result.exit_code == 0
        value = result.output.strip()
        # Should be parseable as UUID
        parsed = uuid.UUID(value)
        assert parsed.version == 4

    def test_generate_command_uuid7_format(self):
        """generate --format uuid7."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "uuid7"])
        assert result.exit_code == 0
        value = result.output.strip()
        # Should be parseable as UUID
        parsed = uuid.UUID(value)
        assert parsed.version == 7


class TestGenerateSecretCommandLength:
    """Test the --length option."""

    def test_generate_with_length_4(self):
        """--length 4."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "alphanumeric", "--length", "4"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert len(value) == 4

    def test_generate_with_length_64(self):
        """--length 64."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "alphanumeric", "--length", "64"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert len(value) == 64

    def test_generate_with_length_1(self):
        """--length 1 (minimum)."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "numeric", "--length", "1"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert len(value) == 1

    def test_generate_with_length_for_hex(self):
        """--length affects hex output length."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "hex", "--length", "16"])
        assert result.exit_code == 0
        value = result.output.strip()
        # 16 bytes = 32 hex chars
        assert len(value) == 32


class TestGenerateSecretCommandPasswordValidation:
    """Test password format validation."""

    def test_password_length_3_fails(self):
        """password --length 3 exits with code 1."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "password", "--length", "3"])
        assert result.exit_code == 2  # Click UsageError maps to exit 2
        assert "password format requires length >= 4" in result.output

    def test_password_length_2_fails(self):
        """password --length 2 exits with error."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "password", "--length", "2"])
        assert result.exit_code == 2
        assert "password format requires length >= 4" in result.output

    def test_password_length_1_fails(self):
        """password --length 1 exits with error."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "password", "--length", "1"])
        assert result.exit_code == 2
        assert "password format requires length >= 4" in result.output

    def test_password_length_4_succeeds(self):
        """password --length 4 succeeds."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "password", "--length", "4"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert len(value) == 4

    def test_password_length_32_succeeds(self):
        """password --length 32 succeeds."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "password", "--length", "32"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert len(value) == 32


class TestGenerateSecretCommandJsonOutput:
    """Test JSON output format."""

    def test_generate_output_json_urlsafe(self):
        """--output json returns valid JSON with format and length."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "urlsafe", "--length", "16", "--output", "json"])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["success"] is True
        assert envelope["command"] == "secret_generate"
        data = envelope["data"]
        assert "secret" in data
        assert "format" in data
        assert data["format"] == "urlsafe"
        assert "length" in data
        assert data["length"] == 16
        assert isinstance(data["secret"], str)

    def test_generate_output_json_hex(self):
        """--output json for hex format."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "hex", "--length", "8", "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["format"] == "hex"
        assert data["length"] == 8

    def test_generate_output_json_password(self):
        """--output json for password format."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "password", "--length", "16", "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["format"] == "password"
        assert data["length"] == 16

    def test_generate_output_json_uuid4_no_length(self):
        """--output json for uuid4 (length ignored)."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "uuid4", "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert "secret" in data
        assert data["format"] == "uuid4"
        # UUID formats should not have length in JSON
        assert "length" not in data

    def test_generate_output_json_uuid7_no_length(self):
        """--output json for uuid7 (length ignored)."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "uuid7", "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert "secret" in data
        assert data["format"] == "uuid7"
        # UUID formats should not have length in JSON
        assert "length" not in data


class TestGenerateSecretCommandTextOutput:
    """Test text output format (default)."""

    def test_generate_output_text_bare_value(self):
        """Default output is bare value (no JSON wrapper)."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "alphanumeric"])
        assert result.exit_code == 0
        value = result.output.strip()
        # Should not be JSON
        assert "{" not in value
        assert "}" not in value
        # Should be plain alphanumeric
        assert re.match(r"^[A-Za-z0-9]*$", value)

    def test_generate_output_text_explicit(self):
        """--output text returns bare value."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--output", "text", "--format", "numeric"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert re.match(r"^\d*$", value)


class TestGenerateSecretCommandCaseInsensitivity:
    """Test that format parameter is case-insensitive."""

    def test_generate_format_uppercase(self):
        """--format URLSAFE (uppercase)."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "URLSAFE"])
        assert result.exit_code == 0

    def test_generate_format_mixed_case(self):
        """--format UrlSafe (mixed case)."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "UrlSafe"])
        assert result.exit_code == 0

    def test_generate_format_hex_uppercase(self):
        """--format HEX."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "HEX"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert re.match(r"^[0-9a-f]*$", value)


class TestGenerateSecretCommandRandomness:
    """Test that multiple invocations produce different values."""

    def test_multiple_invocations_different_values(self):
        """Multiple calls produce different secrets."""
        runner = CliRunner()
        values = []
        for _ in range(5):
            result = runner.invoke(secret_group, ["generate"])
            assert result.exit_code == 0
            values.append(result.output.strip())
        # All values should be different
        assert len(set(values)) == 5


class TestMaskSecretCommandBasic:
    """Test the mask command with basic options."""

    def test_mask_command_default_options(self):
        """mask <value> with default show=4."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "mysecrettoken"])
        assert result.exit_code == 0
        value = result.output.strip()
        # 13 chars - 4 shown = 9 masked
        assert value == "myse" + "*" * 9

    def test_mask_command_show_2(self):
        """mask --show 2 <value>."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "databasepassword", "--show", "2"])
        assert result.exit_code == 0
        value = result.output.strip()
        # 16 chars - 2 shown = 14 masked
        assert value == "da" + "*" * 14

    def test_mask_command_show_8(self):
        """mask --show 8 <value>."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "databasepassword", "--show", "8"])
        assert result.exit_code == 0
        value = result.output.strip()
        # 16 chars - 8 shown = 8 masked
        assert value == "database" + "*" * 8

    def test_mask_command_show_0(self):
        """mask --show 0 fully masks."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "secret", "--show", "0"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert value == "******"

    def test_mask_command_custom_char_hash(self):
        """mask --char # <value>."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "mysecret", "--char", "#"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert value == "myse" + "#" * 4

    def test_mask_command_custom_char_x(self):
        """mask --char X <value>."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "password", "--char", "X"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert value == "pass" + "X" * 4

    def test_mask_command_custom_char_dash(self):
        """mask --char - <value>."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "token123", "--char", "-"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert value == "toke" + "-" * 4

    def test_mask_command_show_and_char(self):
        """mask --show N --char C <value>."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "verylongsecret", "--show", "6", "--char", "#"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert value == "verylo" + "#" * 8


class TestMaskSecretCommandShortValues:
    """Test mask with short values."""

    def test_mask_short_value_fully_masked(self):
        """Value length <= show is fully masked."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "ab"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert value == "**"

    def test_mask_empty_string(self):
        """Empty string is handled."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", ""])
        assert result.exit_code == 0
        value = result.output.strip()
        assert value == ""

    def test_mask_single_char(self):
        """Single character is masked."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "X"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert value == "*"


class TestMaskSecretCommandJsonOutput:
    """Test JSON output for mask command."""

    def test_mask_output_json(self):
        """--output json returns structured JSON."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "mysecret", "--output", "json"])
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["success"] is True
        assert envelope["command"] == "secret_mask"
        data = envelope["data"]
        assert "masked" in data
        assert "show" in data
        assert "char" in data
        assert data["show"] == 4
        assert data["char"] == "*"
        assert data["masked"] == "myse****"

    def test_mask_output_json_custom_options(self):
        """--output json with custom show and char."""
        runner = CliRunner()
        result = runner.invoke(
            secret_group, ["mask", "databasepassword", "--output", "json", "--show", "6", "--char", "#"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["show"] == 6
        assert data["char"] == "#"
        # 16 chars - 6 shown = 10 masked
        assert data["masked"] == "databa" + "#" * 10

    def test_mask_output_text_explicit(self):
        """--output text returns bare masked value."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "mysecret", "--output", "text"])
        assert result.exit_code == 0
        value = result.output.strip()
        # No JSON
        assert "{" not in value
        assert value == "myse****"


class TestMaskSecretCommandCharValidation:
    """Test character parameter validation."""

    def test_mask_char_must_be_single(self):
        """--char must be exactly one character."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "secret", "--char", "ab"])
        assert result.exit_code == 2
        assert "exactly one character" in result.output

    def test_mask_char_empty_fails(self):
        """Empty --char is invalid."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "secret", "--char", ""])
        assert result.exit_code == 2

    def test_mask_char_space_valid(self):
        """Space is a valid single character."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "mysecret", "--char", " "])
        assert result.exit_code == 0
        value = result.output.strip()
        # Leading spaces would be stripped by strip(), so check more carefully
        result_no_strip = runner.invoke(secret_group, ["mask", "mysecret", "--char", " "])
        assert result_no_strip.exit_code == 0


class TestMaskSecretCommandArgument:
    """Test argument handling."""

    def test_mask_missing_value_argument(self):
        """mask without value argument fails."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask"])
        assert result.exit_code == 2

    def test_mask_value_with_spaces(self):
        """Value with spaces is handled."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "my secret token"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert len(value) == len("my secret token")
        assert "my s" in value

    def test_mask_value_with_special_chars(self):
        """Value with special characters."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "api-key-12345"])
        assert result.exit_code == 0
        value = result.output.strip()
        assert len(value) == len("api-key-12345")


class TestGenerateAndMaskIntegration:
    """Test integration between generate and mask commands."""

    def test_generate_and_mask_roundtrip(self):
        """Generate a secret and mask it."""
        runner = CliRunner()
        # Generate a secret
        gen_result = runner.invoke(secret_group, ["generate", "--format", "alphanumeric", "--length", "16"])
        assert gen_result.exit_code == 0
        secret = gen_result.output.strip()

        # Mask it
        mask_result = runner.invoke(secret_group, ["mask", secret, "--show", "4"])
        assert mask_result.exit_code == 0
        masked = mask_result.output.strip()

        # Verify masking
        assert masked == secret[:4] + "*" * (len(secret) - 4)

    def test_uuid4_generation_and_mask(self):
        """Generate UUID4 and mask it."""
        runner = CliRunner()
        gen_result = runner.invoke(secret_group, ["generate", "--format", "uuid4"])
        assert gen_result.exit_code == 0
        uuid_value = gen_result.output.strip()

        mask_result = runner.invoke(secret_group, ["mask", uuid_value, "--show", "8"])
        assert mask_result.exit_code == 0

    def test_password_generation_and_mask(self):
        """Generate password and mask it."""
        runner = CliRunner()
        gen_result = runner.invoke(secret_group, ["generate", "--format", "password", "--length", "20"])
        assert gen_result.exit_code == 0
        password = gen_result.output.strip()

        # Use -- to stop Click from treating a password that starts with '-' as an option flag.
        mask_result = runner.invoke(secret_group, ["mask", "--show", "5", "--", password])
        assert mask_result.exit_code == 0
        masked = mask_result.output.strip()
        assert len(masked) == len(password)


class TestGenerateSecretCommandEdgeCases:
    """Test edge cases and error conditions."""

    def test_generate_invalid_format(self):
        """Invalid format raises error."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--format", "invalid"])
        assert result.exit_code == 2

    def test_generate_length_0_fails(self):
        """--length 0 is invalid."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--length", "0"])
        assert result.exit_code == 2

    def test_generate_negative_length_fails(self):
        """Negative length is invalid."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--length", "-1"])
        assert result.exit_code == 2

    def test_generate_non_numeric_length_fails(self):
        """Non-numeric length fails."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--length", "abc"])
        assert result.exit_code == 2


class TestSecretCommandGroupHelp:
    """Test help functionality."""

    def test_secret_group_help(self):
        """strata secret --help works."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["--help"])
        assert result.exit_code == 0
        assert "Generate and manage secret values" in result.output

    def test_generate_help(self):
        """strata secret generate --help works."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["generate", "--help"])
        assert result.exit_code == 0
        assert "cryptographically secure secret" in result.output.lower()
        assert "--format" in result.output
        assert "--length" in result.output

    def test_mask_help(self):
        """strata secret mask --help works."""
        runner = CliRunner()
        result = runner.invoke(secret_group, ["mask", "--help"])
        assert result.exit_code == 0
        assert "mask" in result.output.lower()
        assert "--show" in result.output
        assert "--char" in result.output
