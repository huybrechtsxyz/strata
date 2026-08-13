#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_utils_templater.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Template processing functionality for files with placeholders.
===============================================================================
"""

import tempfile
from pathlib import Path

from strata.utils.templater import TemplateProcessor


def test_process_single_template_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        template_dir = Path(tmpdir)
        template_file = template_dir / "main.template.tf"
        with open(template_file, "w", encoding="utf-8") as f:
            f.write('organization = "{{ ORG }}"\nproject = "{{ PROJECT }}"')
        monkeypatch.setenv("ORG", "my-org")
        monkeypatch.setenv("PROJECT", "my-project")
        processor = TemplateProcessor(template_dir, cleanup_templates=False)
        assert processor.process_single_template(template_file)
        output_file = template_dir / "main.tf"
        assert output_file.exists()
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert 'organization = "my-org"' in content
        assert 'project = "my-project"' in content


def test_process_all_templates_and_cleanup(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        template_dir = Path(tmpdir)
        template_file = template_dir / "vars.template.tf"
        with open(template_file, "w", encoding="utf-8") as f:
            f.write('region = "{{ REGION }}"')
        monkeypatch.setenv("REGION", "eu-west-1")
        processor = TemplateProcessor(template_dir, cleanup_templates=True)
        assert processor.process_all_templates()
        output_file = template_dir / "vars.tf"
        assert output_file.exists()
        # Template file should be deleted
        assert not template_file.exists()
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert 'region = "eu-west-1"' in content


def test_process_template_missing_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        template_dir = Path(tmpdir)
        template_file = template_dir / "missing.template.tf"
        with open(template_file, "w", encoding="utf-8") as f:
            f.write('foo = "{{ NOT_SET }}"')
        # Do not set NOT_SET — should fail with StrictUndefined
        processor = TemplateProcessor(template_dir, cleanup_templates=False)
        assert not processor.process_single_template(template_file)


# =============================================================================
# TemplateProcessor.render — static method tests
# =============================================================================


class TestTemplateProcessorRender:
    def test_render_substitutes_known_var(self):
        result = TemplateProcessor.render("hello {{ name }}", {"name": "world"})
        assert result == "hello world"

    def test_render_leaves_unknown_var_visible(self):
        result = TemplateProcessor.render("hello {{ unknown }}", {"name": "world"})
        assert "unknown" in result

    def test_render_leaves_empty_context_visible(self):
        result = TemplateProcessor.render("{{ name }}", {})
        assert "name" in result

    def test_render_multiple_vars(self):
        result = TemplateProcessor.render("{{ greeting }} {{ name }}!", {"greeting": "Hi", "name": "Alice"})
        assert result == "Hi Alice!"

    def test_render_empty_string(self):
        result = TemplateProcessor.render("", {"name": "val"})
        assert result == ""

    def test_render_no_placeholders(self):
        result = TemplateProcessor.render("plain text", {"name": "val"})
        assert result == "plain text"

    def test_render_with_conditional(self):
        content = "{% if show %}visible{% endif %}"
        assert TemplateProcessor.render(content, {"show": True}) == "visible"
        assert TemplateProcessor.render(content, {"show": False}) == ""

    def test_render_with_loop(self):
        content = "{% for item in items %}{{ item }} {% endfor %}"
        result = TemplateProcessor.render(content, {"items": ["a", "b", "c"]})
        assert result == "a b c "

    def test_render_preserves_non_jinja_dollar_syntax(self):
        """Dollar-sign variables (VS Code, shell) pass through untouched."""
        result = TemplateProcessor.render("${input:cliArgs}", {"name": "val"})
        assert result == "${input:cliArgs}"


# =============================================================================
# TemplateProcessor.check_syntax / find_variables — introspection tests
# =============================================================================


class TestTemplateProcessorCheckSyntax:
    def test_valid_template_returns_none(self):
        assert TemplateProcessor.check_syntax("{% for x in items %}{{ x }}{% endfor %}") is None

    def test_empty_template_returns_none(self):
        assert TemplateProcessor.check_syntax("") is None

    def test_unclosed_block_is_reported(self):
        error = TemplateProcessor.check_syntax("{% for x in items %}{{ x }}")
        assert error is not None
        assert "for" in error

    def test_error_includes_line_number(self):
        error = TemplateProcessor.check_syntax("line one\n{% for x in items %}\n")
        assert error is not None
        assert "line 2" in error

    def test_unknown_filter_is_reported(self):
        """Filters resolve during the tracking pass, not during parse."""
        error = TemplateProcessor.check_syntax("{{ name | no_such_filter }}")
        assert error is not None
        assert "no_such_filter" in error

    def test_builtin_filter_is_accepted(self):
        assert TemplateProcessor.check_syntax("{{ name | upper }}") is None


class TestTemplateProcessorFindVariables:
    def test_finds_top_level_variable(self):
        assert TemplateProcessor.find_variables("{{ name }}") == {"name"}

    def test_empty_template_returns_empty_set(self):
        assert TemplateProcessor.find_variables("") == set()

    def test_template_without_variables_returns_empty_set(self):
        assert TemplateProcessor.find_variables("plain text") == set()

    def test_attribute_access_reports_only_the_root(self):
        assert TemplateProcessor.find_variables("{{ topology.nodes[0].id }}") == {"topology"}

    def test_loop_variables_are_excluded(self):
        assert TemplateProcessor.find_variables("{% for n in nodes %}{{ n.id }}{% endfor %}") == {"nodes"}

    def test_set_assignments_are_excluded(self):
        assert TemplateProcessor.find_variables("{% set total = count %}{{ total }}") == {"count"}

    def test_filters_are_excluded(self):
        assert TemplateProcessor.find_variables("{{ name | upper }}") == {"name"}

    def test_multiple_variables(self):
        assert TemplateProcessor.find_variables("{{ a }}{% if b %}{{ c }}{% endif %}") == {"a", "b", "c"}
