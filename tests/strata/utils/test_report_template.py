#!/usr/bin/env python3
"""Tests for the handover document template environment (docs/design/deployment-documentation.md, section 5)."""

import pytest
from jinja2.exceptions import SecurityError

from strata.utils.report_template import check_report_template, md_cell, report_environment


def test_md_cell_keeps_a_value_inside_one_table_cell():
    assert md_cell("a|b\nc\r\nd") == r"a\|b<br>c<br>d"
    assert md_cell(None) == ""


def test_check_reports_unknown_names_and_syntax_errors():
    assert check_report_template("{{ deployment }} {% for s in secrets %}{{ loop.index }}{% endfor %}") == []
    assert check_report_template("{{ secret_values }}")[0].startswith("'secret_values' is not in the report data")
    assert check_report_template("{% if %}")[0].startswith("template error")


def test_templates_run_sandboxed():
    with pytest.raises(SecurityError):
        report_environment().from_string("{{ x.__class__.__mro__ }}").render(x=1)
