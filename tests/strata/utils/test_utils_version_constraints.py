#!/usr/bin/env python3
"""Tests for `utils/version_constraints.py` (docs/work/integration-readiness.md D3)."""

import pytest

from strata.utils.version_constraints import parse_version_constraint, version_satisfies


@pytest.mark.parametrize(
    ("constraint", "inside", "outside"),
    [
        (">= 1.7", "1.9.2", "1.6.9"),
        ("~= 1.9", "1.12.0", "2.0.0"),
        ("~> 1.9", "1.12.0", "2.0.0"),
        ("~> 1.9.0", "1.9.5", "1.10.0"),
        (">= 1.7, < 2.0", "1.7.0", "2.0.0"),
    ],
)
def test_constraints_match_like_terraform(constraint, inside, outside):
    parsed = parse_version_constraint(constraint)
    assert parsed.contains(inside) and not parsed.contains(outside)


@pytest.mark.parametrize("constraint", ["latest", "1.7+", "~> 1"])
def test_an_invalid_constraint_is_a_value_error_with_examples(constraint):
    with pytest.raises(ValueError, match="is not a version constraint"):
        parse_version_constraint(constraint)


@pytest.mark.parametrize(
    ("installed", "constraint", "ok"),
    [
        ("1.12.0", "~> 1.9", True),
        ("2.0.0", "~> 1.9", False),
        ("1.10.0-beta1", ">= 1.7", True),
        ("v3.14.0", ">= 3.0", True),
        ("not-a-version", ">= 0", False),
    ],
)
def test_version_satisfies(installed, constraint, ok):
    assert version_satisfies(installed, constraint) is ok
