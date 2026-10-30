#!/usr/bin/env python3
"""Tests for `strata deploy images` (docs/work/effective-images.md)."""

import json
from pathlib import Path

from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    (root / relative).write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path, *, with_tag: bool = True) -> Path:
    """Deployment `app` (version `prd`, pinning `datahub` to 2.0.0) and `other` (no version)."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n  namespaces:\n    - apps\n"
        "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n",
    )
    _write(
        root,
        "module.yaml",
        "module",
        "stack",
        "  source:\n    source_path: services/stack\n  type: compose\n  default_labels:\n    app: stack\n"
        "  services:\n    - name: redis\n      image: redis:7\n",
    )
    _write(
        root,
        "namespace.yaml",
        "namespace",
        "apps",
        "  default_labels:\n    app: apps\n  modules:\n    - name: stack\n      module: stack\n",
    )
    tag = "  image_tag: '1.0.0'\n" if with_tag else ""
    _write(
        root, "artifact.yaml", "artifact", "datahub", f"  image_name: int/datahub\n  registry: cdn.example.com\n{tag}"
    )
    _write(
        root,
        "environment.yaml",
        "environment",
        "prd",
        "  variables:\n    - key: DATAHUB_TAG\n      store: artifact\n      value: datahub\n      field: image_tag\n",
    )
    _write(root, "version.yaml", "version", "prd", "  pins:\n    artifacts:\n      datahub: '2.0.0'\n")
    _write(root, "app.yaml", "deployment", "app", "  workspace: main\n  version: prd\n  environments:\n    - prd\n")
    _write(root, "other.yaml", "deployment", "other", "  workspace: main\n  environments:\n    - prd\n")
    return root


def _invoke(root: Path, *args: str):
    return CliRunner().invoke(cli, ["deploy", "images", *args, "--path", str(root)])


def _data(result) -> dict:
    assert result.exit_code == EXIT_SUCCESS, result.output
    return json.loads(result.output)["data"]


def test_json_lists_artifact_and_module_service_images(tmp_path):
    data = _data(_invoke(_solution(tmp_path), "app", "-o", "json"))
    assert (data["deployment"], data["version"], data["pin"]) == ("app", "prd", None)
    rows = {row["name"]: row for row in data["images"]}
    assert set(rows) == {"datahub", "redis"}
    datahub = rows["datahub"]
    assert (datahub["origin"], datahub["image_name"], datahub["image_tag"]) == ("artifact", "int/datahub", "2.0.0")
    assert (datahub["tag_source"], datahub["pin_version"], datahub["pin_flag"]) == ("pin", "prd", False)
    assert datahub["registry"] == "cdn.example.com"
    assert datahub["via"] == ["variable:DATAHUB_TAG"]
    assert rows["redis"]["origin"] == "module-service"


def test_a_deployment_without_a_version_reports_the_declared_tag(tmp_path):
    rows = {row["name"]: row for row in _data(_invoke(_solution(tmp_path), "other", "-o", "json"))["images"]}
    assert (rows["datahub"]["image_tag"], rows["datahub"]["tag_source"]) == ("1.0.0", "declared")


def test_pin_overrides_the_version_and_is_flagged(tmp_path):
    data = _data(_invoke(_solution(tmp_path), "other", "--pin", "prd", "-o", "json"))
    assert (data["version"], data["pin"]) == ("prd", "prd")
    datahub = next(row for row in data["images"] if row["name"] == "datahub")
    assert (datahub["image_tag"], datahub["pin_flag"]) == ("2.0.0", True)


def test_an_unknown_pin_is_a_usage_error(tmp_path):
    result = _invoke(_solution(tmp_path), "app", "--pin", "ghost")
    assert result.exit_code == EXIT_USAGE, result.output
    assert "ghost" in result.output


def test_an_unknown_deployment_is_a_usage_error(tmp_path):
    result = _invoke(_solution(tmp_path), "ghost")
    assert result.exit_code == EXIT_USAGE, result.output


def test_a_row_with_no_tag_warns_but_does_not_fail(tmp_path):
    root = _solution(tmp_path, with_tag=False)
    result = _invoke(root, "other", "-o", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    envelope = json.loads(result.output)
    datahub = next(row for row in envelope["data"]["images"] if row["name"] == "datahub")
    assert (datahub["image_tag"], datahub["tag_source"], datahub["image_ref"]) == (None, "none", "int/datahub")
    assert [d["code"] for d in envelope["diagnostics"]] == ["image_without_tag"]


def test_console_prints_a_table_with_the_tag_source(tmp_path):
    result = _invoke(_solution(tmp_path), "app")
    assert result.exit_code == EXIT_SUCCESS, result.output
    header = next(line for line in result.output.splitlines() if line.strip().startswith("NAME"))
    assert header.split()[:5] == ["NAME", "ORIGIN", "IMAGE", "TAG", "SOURCE"]
    assert any(
        line.split()[:5] == ["datahub", "artifact", "int/datahub", "2.0.0", "pin"]
        for line in result.output.splitlines()
    )


def test_it_requires_a_valid_solution(tmp_path):
    root = _solution(tmp_path)
    _write(root, "broken.yaml", "deployment", "broken", "  workspace: ghost\n  environments:\n    - prd\n")
    result = _invoke(root, "app")
    assert result.exit_code == EXIT_VALIDATION, result.output


def test_it_needs_no_build_and_writes_nothing(tmp_path):
    root = _solution(tmp_path)
    before = {path.name for path in root.iterdir()}
    _data(_invoke(root, "app", "-o", "json"))
    assert {path.name for path in root.iterdir()} == before
