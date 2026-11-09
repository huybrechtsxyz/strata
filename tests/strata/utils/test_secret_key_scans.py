#!/usr/bin/env python3
"""Tests for the scans that find which secrets an input uses (docs/work/deployment-documentation.md)."""

from pathlib import Path

import pytest

from strata.utils.templater import template_secret_keys
from strata.utils.terraform_outputs import read_declared_variables
from strata.utils.value_tokens import referenced_secret_keys


def test_referenced_secret_keys_walks_nested_data_and_ignores_other_kinds():
    node = {"a": ["${secret:A}", {"b": "x ${secret:B} ${var:V} ${output:s.k}"}], "c": 1}
    assert referenced_secret_keys(node) == {"A", "B"}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("{{ secrets.A }} {{ secrets['B'] }}", {"A", "B"}),
        ("{{ variables.X }}", set()),
        ("{{ secrets[name] }}", None),
        ("{% for k in secrets %}{{ k }}{% endfor %}", None),
        ("{{ secrets.items() }}", None),
        ("{{ secrets | tojson }}", None),
        ("{% if %}", None),
    ],
)
def test_template_secret_keys(source, expected):
    assert template_secret_keys(source) == expected


def test_read_declared_variables_reads_root_module_variable_blocks(tmp_path: Path):
    (tmp_path / "variables.tf").write_text('variable "A" {}\nvariable "b" {\n  type = string\n}\n', encoding="utf-8")
    (tmp_path / "child").mkdir()
    (tmp_path / "child" / "variables.tf").write_text('variable "C" {}\n', encoding="utf-8")
    assert read_declared_variables(tmp_path) == {"A", "b"}
