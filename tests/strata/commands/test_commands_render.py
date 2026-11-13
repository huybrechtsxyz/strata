#!/usr/bin/env python3
"""Tests for `strata render` (`base-parameters-v2.md` (work doc), phase 4)."""

import json

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\n{meta}spec:\n"

DOCUMENTS = [
    (
        "workspace",
        "main",
        "  providers: []\n  provisioners:\n    - name: tf\n      tool: terraform\n"
        "      source:\n        source_path: infra\n",
        "",
    ),
    ("environment", "prd", "  secrets:\n    - { key: db, store: constant, value: hunter2 }\n", ""),
    ("parameterset", "spoke-acme", "  values: { name: acme-001 }\n", ""),
    (
        "deployment",
        "base",
        "  partial: true\n  workspace: main\n  environments: [prd]\n"
        "  properties:\n    subscription: sub-${param:spoke.name}\n",
        "",
    ),
    ("deployment", "acme", "  extends: base\n", "  params: { spoke: spoke-acme }\n"),
]


@pytest.fixture
def solution(tmp_path):
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s", meta="") + "  {}\n", "utf-8")
    for kind, name, spec, meta in DOCUMENTS:
        (root / f"{kind}-{name}.yaml").write_text(_HEADER.format(kind=kind, name=name, meta=meta) + spec, "utf-8")
    return root


def _run(*args):
    return CliRunner().invoke(cli, ["render", *[str(arg) for arg in args]])


def test_render_prints_each_value_and_where_it_comes_from(solution):
    result = _run("deployment", "acme", "--path", solution)
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "chain: acme <- base" in result.output
    assert "parameter sets: spoke: spoke-acme" in result.output
    line = next(line for line in result.output.splitlines() if "spec.properties.subscription" in line)
    assert "sub-acme-001" in line and "base" in line
    assert "'sub-${param:spoke.name}': spoke.name = acme-001 (spoke-acme)" in line


def test_render_json(solution):
    result = _run("deployment", "acme", "--path", solution, "-o", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    data = json.loads(result.output)["data"]
    assert data["chain"] == ["acme", "base"]
    assert data["params"] == {"spoke": {"name": "acme-001"}}
    field = next(item for item in data["fields"] if item["path"] == "spec.properties.subscription")
    assert field["document"] == "base"
    assert field["reads"] == [{"role": "spoke", "key": "name", "value": "acme-001", "parameter_set": "spoke-acme"}]


def test_render_masks_secrets(solution):
    for output in ("console", "json"):
        result = _run("environment", "prd", "--path", solution, "-o", output)
        assert result.exit_code == EXIT_SUCCESS, result.output
        assert "hunter2" not in result.output


@pytest.mark.parametrize("name", ["base", "nope"])
def test_render_fails_for_a_base_or_an_unknown_name(solution, name):
    result = _run("deployment", name, "--path", solution)
    assert result.exit_code == EXIT_VALIDATION, result.output


def test_render_takes_only_the_kinds_built_with_extends(solution):
    assert _run("workspace", "main", "--path", solution).exit_code == EXIT_USAGE
