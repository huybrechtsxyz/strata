#!/usr/bin/env python3
"""Tests for scaffold_templates (docs/design/solution-scaffolding.md)."""

from strata.utils.scaffold_templates import dest_relative_path, is_package_owned, render_scaffold

# ---------------------------------------------------------------------------
# render_scaffold()
# ---------------------------------------------------------------------------


def test_known_variable_is_substituted():
    assert render_scaffold("Hello {{ SOLUTION_NAME }}!", {"SOLUTION_NAME": "acme"}) == "Hello acme!"


def test_unknown_variable_stays_literal_rather_than_raising():
    """Lenient (DebugUndefined) — unlike templater.py's strict output.template
    environment, a scaffold template's context is always small and fixed."""
    assert render_scaffold("Hello {{ NOT_A_REAL_VAR }}!", {"SOLUTION_NAME": "acme"}) == "Hello {{ NOT_A_REAL_VAR }}!"


def test_content_with_no_tokens_at_all_passes_through_unchanged():
    content = "# Just a heading\n\nSome text.\n"
    assert render_scaffold(content, {"SOLUTION_NAME": "acme"}) == content


def test_multiple_tokens_all_substituted():
    rendered = render_scaffold(
        "{{ SOLUTION_NAME }} v{{ STRATA_VERSION }}", {"SOLUTION_NAME": "acme", "STRATA_VERSION": "2.0.0"}
    )
    assert rendered == "acme v2.0.0"


# ---------------------------------------------------------------------------
# dest_relative_path()
# ---------------------------------------------------------------------------


def test_dot_prefixed_directory_is_renamed():
    assert dest_relative_path("dot.github/pull_request_template.md") == ".github/pull_request_template.md"


def test_dot_prefixed_nested_directory_is_renamed():
    assert dest_relative_path("dot.strata/README.md") == ".strata/README.md"
    assert (
        dest_relative_path("dot.github/ISSUE_TEMPLATE/deployment-change-request.yml")
        == ".github/ISSUE_TEMPLATE/deployment-change-request.yml"
    )


def test_dot_prefixed_filename_is_renamed():
    assert dest_relative_path("dot.gitignore") == ".gitignore"


def test_path_with_no_dot_prefix_is_unchanged():
    assert dest_relative_path("README.md") == "README.md"


# ---------------------------------------------------------------------------
# is_package_owned()
# ---------------------------------------------------------------------------


def test_strata_directory_is_package_owned():
    assert is_package_owned(".strata/README.md") is True
    assert is_package_owned(".strata/integrations/foo.yaml") is True


def test_github_directory_is_not_package_owned():
    assert is_package_owned(".github/pull_request_template.md") is False
    assert is_package_owned(".github/ISSUE_TEMPLATE/deployment-change-request.yml") is False


def test_anything_else_is_not_package_owned():
    assert is_package_owned("README.md") is False
    assert is_package_owned(".gitignore") is False
