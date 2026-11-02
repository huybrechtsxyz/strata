#!/usr/bin/env python3
"""Tests for safe rendering of the command line."""

from strata.logging.redaction import REDACTED
from strata.utils.argv import command_line, redact_argv


def test_ordinary_arguments_are_untouched():
    """Redaction must not mangle a normal invocation."""
    assert redact_argv(["strata", "validate", "config"]) == ["strata", "validate", "config"]


def test_separate_token_value_is_masked():
    """`--token abc` — the value is the next token."""
    assert redact_argv(["strata", "--token", "abc"]) == ["strata", "--token", REDACTED]


def test_inline_value_is_masked():
    """`--token=abc` — the value is inside the same token."""
    assert redact_argv(["strata", "--token=abc"]) == ["strata", f"--token={REDACTED}"]


def test_value_flag_is_masked():
    """`secret put KEY --value <plaintext>` is the case v1 had to fix."""
    assert redact_argv(["strata", "secret", "put", "KEY", "--value", "hunter2"]) == [
        "strata",
        "secret",
        "put",
        "KEY",
        "--value",
        REDACTED,
    ]


def test_sensitivity_reuses_the_logging_rule():
    """One list of what counts as secret, not one per call site."""
    assert redact_argv(["strata", "--password", "p"])[-1] == REDACTED
    assert redact_argv(["strata", "--api-key", "k"])[-1] == REDACTED
    assert redact_argv(["strata", "--client-secret", "s"])[-1] == REDACTED


def test_only_the_value_immediately_after_a_flag_is_masked():
    """A later positional argument is not collateral damage."""
    assert redact_argv(["strata", "--token", "abc", "validate"]) == [
        "strata",
        "--token",
        REDACTED,
        "validate",
    ]


def test_a_positional_that_looks_like_a_flag_name_is_kept():
    """Only options trigger masking, not bare words."""
    assert redact_argv(["strata", "token", "list"]) == ["strata", "token", "list"]


def test_input_is_not_mutated():
    """Callers reuse argv after rendering it."""
    argv = ["strata", "--token", "abc"]
    redact_argv(argv)
    assert argv == ["strata", "--token", "abc"]


def test_command_line_reports_the_typed_program_name():
    """Users retype 'strata', not the resolved interpreter path."""
    assert command_line(["/usr/lib/python/strata", "validate"]) == "strata validate"


def test_command_line_redacts():
    """The whole point: echoing the invocation must be safe."""
    assert REDACTED in command_line(["strata", "--token", "abc"])


def test_command_line_handles_empty_argv():
    """Defensive: an empty argv must not raise."""
    assert command_line([]) == ""
