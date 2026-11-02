#!/usr/bin/env python3
"""Tests for failure classification and its exit-code rendering."""

import pytest

from strata.commands.exit_codes import (
    EXIT_CODE_BY_ERROR,
    EXIT_FAILURE,
    EXIT_SUCCESS,
    EXIT_USAGE,
    EXIT_VALIDATION,
    exit_code_for,
)
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import StrataError, SystemError, UsageError, ValidationError


def _all_error_types(base: type = StrataError) -> list[type]:
    """Every StrataError subclass, including the base."""
    found = [base]
    for subclass in base.__subclasses__():
        found.extend(_all_error_types(subclass))
    return found


# ---------------------------------------------------------------------------
# The codes themselves
# ---------------------------------------------------------------------------


def test_exit_codes_match_the_v1_contract():
    """Existing pipelines branch on these numbers; they cannot move."""
    assert (EXIT_SUCCESS, EXIT_FAILURE, EXIT_USAGE, EXIT_VALIDATION) == (0, 1, 2, 3)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (UsageError("wrong place"), EXIT_USAGE),
        (ValidationError(Diagnostics()), EXIT_VALIDATION),
        (SystemError("disk gone"), EXIT_FAILURE),
        (StrataError("something"), EXIT_FAILURE),
    ],
)
def test_each_failure_maps_to_its_code(error, expected):
    """Classification comes from the error type, not from probing objects."""
    assert exit_code_for(error) == expected


def test_usage_and_validation_are_distinguishable():
    """'Wrong directory' and 'invalid config' need different responses."""
    assert exit_code_for(UsageError("x")) != exit_code_for(ValidationError(Diagnostics()))


# ---------------------------------------------------------------------------
# The mapping cannot go stale
# ---------------------------------------------------------------------------


def test_every_error_type_is_mapped():
    """Adding a failure without an exit code would silently become a crash code.

    This is the guard that justifies keeping the code out of the exception:
    the table is separate, so it needs a test proving it stays exhaustive.
    """
    unmapped = [e.__name__ for e in _all_error_types() if e not in EXIT_CODE_BY_ERROR]
    assert not unmapped, f"Unmapped failure types: {unmapped}. Add them to EXIT_CODE_BY_ERROR."


def test_a_subclass_inherits_its_parents_code():
    """A future specialisation must not fall through to a generic failure."""

    class NotInsideSolutionError(UsageError):
        """A more specific usage failure."""

    assert exit_code_for(NotInsideSolutionError("x")) == EXIT_USAGE


# ---------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------


def test_errors_carry_no_exit_code():
    """Exit codes are one front-end's concern; serve/mcp would want HTTP status."""
    assert not hasattr(UsageError("x"), "exit_code")


def test_validation_error_carries_its_findings():
    """The caller renders findings before exiting, so they travel with the error."""
    diagnostics = Diagnostics()
    diagnostics.error("broken", source="a.yaml")
    assert ValidationError(diagnostics).diagnostics.messages() == ["a.yaml: broken"]
