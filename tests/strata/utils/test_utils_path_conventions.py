#!/usr/bin/env python3
"""Tests for path_conventions (docs/design/path-conventions.md)."""

from strata.utils.path_conventions import (
    compile_pattern,
    in_scope,
    match_directory,
    match_filename,
    matches_segment_pattern,
    split_directory_and_filename,
)

# ---------------------------------------------------------------------------
# in_scope()
# ---------------------------------------------------------------------------


def test_path_under_scope_prefix_is_in_scope():
    assert in_scope("customers/c0062/tenant.yaml", "customers/**") is True


def test_path_outside_scope_prefix_is_not_in_scope():
    assert in_scope("providers/westeurope.yaml", "customers/**") is False


def test_multi_segment_scope_prefix_is_respected():
    assert in_scope("deploy/hubs/z00/s01/c0224/dev/deployment.yaml", "deploy/hubs/**") is True
    assert in_scope("deploy/control/dev/deployment.yaml", "deploy/hubs/**") is False


def test_scope_prefix_is_not_a_substring_match_of_a_different_directory():
    """'deploy/hub/**' must not match 'deploy/hubs/...' — startswith on the
    real prefix, not a loose substring check."""
    assert in_scope("deploy/hubs/z00/s01/deployment.yaml", "deploy/hub/**") is False


# ---------------------------------------------------------------------------
# split_directory_and_filename()
# ---------------------------------------------------------------------------


def test_split_separates_directory_from_filename():
    assert split_directory_and_filename("customers/c0062/tenant.yaml") == ("customers/c0062", "tenant.yaml")


def test_split_with_no_slash_has_no_directory_component():
    """A document filed directly at the solution root — no real scope
    (always '<dir>/**') can ever match this, but the split still has a
    defined, non-crashing answer."""
    assert split_directory_and_filename("configuration.yaml") == ("", "configuration.yaml")


# ---------------------------------------------------------------------------
# match_directory()
# ---------------------------------------------------------------------------


def test_directory_pattern_matches_and_extracts_the_segment():
    match = match_directory("customers/c0062/tenant.yaml", "customers/{code}")
    assert match is not None
    assert match.group("code") == "c0062"


def test_directory_pattern_with_extra_depth_does_not_match():
    """The real, empirically-found v1 bug class: a file one directory too
    deep for its convention must be caught, not silently accepted."""
    match = match_directory("customers/c0062/nested/tenant.yaml", "customers/{code}")
    assert match is None


def test_directory_pattern_with_missing_depth_does_not_match():
    match = match_directory("customers/tenant.yaml", "customers/{code}")
    assert match is None


def test_directory_pattern_ignores_the_filename_entirely():
    """v1's own real hub-path/control-path shape: any filename is accepted
    under a matching directory when there's no filename_pattern at all."""
    match = match_directory("deploy/control/dev/deployment.yaml", "deploy/control/{control}")
    assert match is not None
    assert match.group("control") == "dev"
    match = match_directory("deploy/control/dev/anything-else.yaml", "deploy/control/{control}")
    assert match is not None
    assert match.group("control") == "dev"


def test_multiple_directory_captures_are_each_extracted():
    match = match_directory(
        "deploy/hubs/z00/s01/c0224/dev/deployment.yaml",
        "deploy/hubs/{hub}/{spoke}/{customer}/{ring}",
    )
    assert match is not None
    assert match.groupdict() == {"hub": "z00", "spoke": "s01", "customer": "c0224", "ring": "dev"}


def test_a_directory_capture_never_matches_across_a_slash():
    """{code} is [^/]+ — it cannot absorb an extra path segment."""
    match = match_directory("customers/c0062/extra/tenant.yaml", "customers/{code}")
    assert match is None


def test_directory_pattern_can_be_a_pure_literal_with_no_capture():
    """provider-path's real shape: the directory has no capture at all —
    the capture lives entirely in the filename."""
    match = match_directory("providers/westeurope.yaml", "providers")
    assert match is not None
    assert match.groupdict() == {}


# ---------------------------------------------------------------------------
# match_filename()
# ---------------------------------------------------------------------------


def test_filename_pattern_matches_a_literal():
    assert match_filename("customers/c0062/tenant.yaml", "tenant.yaml") is not None
    assert match_filename("customers/c0062/config.yaml", "tenant.yaml") is None


def test_literal_dot_in_filename_pattern_is_escaped_not_treated_as_regex_wildcard():
    """'tenant.yaml' must not match 'tenantXyaml' — the '.' is literal."""
    assert match_filename("customers/c0062/tenantXyaml", "tenant.yaml") is None
    assert match_filename("customers/c0062/tenant.yaml", "tenant.yaml") is not None


def test_filename_pattern_can_capture_with_no_directory_capture():
    """provider-path's real shape: {region} lives entirely in the filename."""
    match = match_filename("providers/westeurope.yaml", "{region}.yaml")
    assert match is not None
    assert match.group("region") == "westeurope"


def test_filename_pattern_does_not_see_the_directory():
    """A directory-shaped mismatch must not be caught by match_filename() —
    that's match_directory()'s job."""
    match = match_filename("providers/azure/westeurope.yaml", "{region}.yaml")
    assert match is not None  # only the bare filename is considered
    assert match.group("region") == "westeurope"


# ---------------------------------------------------------------------------
# compile_pattern() / matches_segment_pattern()
# ---------------------------------------------------------------------------


def test_compile_pattern_returns_a_reusable_compiled_regex():
    compiled = compile_pattern("{region}.yaml")
    assert compiled.match("westeurope.yaml").group("region") == "westeurope"
    assert compiled.match("azure/westeurope.yaml") is None


def test_matches_segment_pattern_true_and_false():
    assert matches_segment_pattern("^[a-z]{3}$", "dev") is True
    assert matches_segment_pattern("^[a-z]{3}$", "development") is False
