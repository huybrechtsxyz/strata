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


def _solution(
    tmp_path: Path, *, with_tag: bool = True, policy: str = "", integration: bool = False, pin: str = "2.0.0"
) -> Path:
    """Deployment `app` (version `prd`, pinning `catalog` to `pin`) and `other` (no version). With `integration`,
    `catalog` names the integration `feed` that holds its registry's credentials."""
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
        "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: infra\n      provisioner: tf\n      namespaces:\n        - apps\n" + policy,
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
    if integration:
        tag += "  integration: feed\n"
        _write(
            root,
            "integration.yaml",
            "integration",
            "feed",
            "  type: proget\n  capabilities: [sources]\n"
            "  authentication:\n    method: token\n    token:\n      token: FEED_API_KEY\n      username: api\n",
        )
    _write(
        root,
        "artifact.yaml",
        "artifact",
        "catalog",
        f"  image_name: int/catalog\n  registry: cdn.example.com\n{tag}",
    )
    _write(
        root,
        "environment.yaml",
        "environment",
        "prd",
        "  variables:\n    - key: CATALOG_TAG\n      store: artifact\n      value: catalog\n      field: image_tag\n",
    )
    _write(root, "version.yaml", "version", "prd", f"  pins:\n    artifacts:\n      catalog: '{pin}'\n")
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
    assert set(rows) == {"catalog", "redis"}
    catalog = rows["catalog"]
    assert (catalog["origin"], catalog["image_name"], catalog["image_tag"]) == ("artifact", "int/catalog", "2.0.0")
    assert (catalog["tag_source"], catalog["pin_version"], catalog["pin_flag"]) == ("pin", "prd", False)
    assert catalog["registry"] == "cdn.example.com"
    assert catalog["via"] == ["variable:CATALOG_TAG"]
    assert rows["redis"]["origin"] == "module-service"


def test_a_deployment_without_a_version_reports_the_declared_tag(tmp_path):
    rows = {row["name"]: row for row in _data(_invoke(_solution(tmp_path), "other", "-o", "json"))["images"]}
    assert (rows["catalog"]["image_tag"], rows["catalog"]["tag_source"]) == ("1.0.0", "declared")


def test_pin_overrides_the_version_and_is_flagged(tmp_path):
    data = _data(_invoke(_solution(tmp_path), "other", "--pin", "prd", "-o", "json"))
    assert (data["version"], data["pin"]) == ("prd", "prd")
    catalog = next(row for row in data["images"] if row["name"] == "catalog")
    assert (catalog["image_tag"], catalog["pin_flag"]) == ("2.0.0", True)


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
    catalog = next(row for row in envelope["data"]["images"] if row["name"] == "catalog")
    assert (catalog["image_tag"], catalog["tag_source"], catalog["image_ref"]) == (None, "none", "int/catalog")
    assert [d["code"] for d in envelope["diagnostics"]] == ["image_without_tag"]


def test_console_prints_a_table_with_the_tag_source(tmp_path):
    result = _invoke(_solution(tmp_path), "app")
    assert result.exit_code == EXIT_SUCCESS, result.output
    header = next(line for line in result.output.splitlines() if line.strip().startswith("NAME"))
    assert header.split()[:5] == ["NAME", "ORIGIN", "IMAGE", "TAG", "SOURCE"]
    assert any(
        line.split()[:5] == ["catalog", "artifact", "int/catalog", "2.0.0", "pin"]
        for line in result.output.splitlines()
    )


# A tag and a digest (docs/work/artifact-gap-analysis.md, design 4).

_DIGEST = "sha256:" + "9f86d081" * 8


def test_a_pinned_tag_at_digest_is_listed_as_its_two_parts(tmp_path):
    rows = {
        row["name"]: row
        for row in _data(_invoke(_solution(tmp_path, pin=f"2.0.0@{_DIGEST}"), "app", "-o", "json"))["images"]
    }
    catalog = rows["catalog"]
    assert (catalog["image_tag"], catalog["image_digest"]) == ("2.0.0", _DIGEST)
    assert catalog["image_ref"] == f"int/catalog:2.0.0@{_DIGEST}"
    assert rows["redis"]["image_digest"] is None


def test_console_adds_a_digest_column_only_when_a_row_has_one(tmp_path):
    result = _invoke(_solution(tmp_path, pin=f"2.0.0@{_DIGEST}"), "app")
    assert result.exit_code == EXIT_SUCCESS, result.output
    header = next(line for line in result.output.splitlines() if line.strip().startswith("NAME"))
    assert header.split()[:6] == ["NAME", "ORIGIN", "IMAGE", "TAG", "DIGEST", "SOURCE"]
    assert any(
        line.split()[:6] == ["catalog", "artifact", "int/catalog", "2.0.0", _DIGEST, "pin"]
        for line in result.output.splitlines()
    )
    assert any(
        line.split()[:5] == ["redis", "module-service", "redis", "7", "-"] for line in result.output.splitlines()
    )


def test_a_digest_without_a_tag_is_not_a_row_with_no_tag(tmp_path):
    result = _invoke(_solution(tmp_path, pin=_DIGEST), "app", "-o", "json")
    envelope = json.loads(result.output)
    assert result.exit_code == EXIT_SUCCESS, result.output
    catalog = next(row for row in envelope["data"]["images"] if row["name"] == "catalog")
    assert (catalog["image_tag"], catalog["image_digest"]) == (None, _DIGEST)
    assert envelope["diagnostics"] == []


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


# ---------------------------------------------------------------------------
# image_tag_policy (docs/design/image-tag-policy.md)
# ---------------------------------------------------------------------------

_DENY = "  image_tag_policy: {}\n"
_WARN = "  image_tag_policy:\n    enforcement: warn\n"


def test_a_deny_policy_fails_a_row_with_no_tag_with_the_validation_exit_code(tmp_path):
    root = _solution(tmp_path, with_tag=False, policy=_DENY)
    result = _invoke(root, "other", "-o", "json")
    assert result.exit_code == EXIT_VALIDATION, result.output
    envelope = json.loads(result.output)
    assert [(d["severity"], d["code"]) for d in envelope["diagnostics"]] == [("error", "image_without_tag")]
    assert {row["name"] for row in envelope["data"]["images"]} == {"catalog", "redis"}


def test_a_warn_policy_reports_but_exits_zero(tmp_path):
    root = _solution(tmp_path, with_tag=False, policy=_WARN)
    result = _invoke(root, "other", "-o", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert [(d["severity"], d["code"]) for d in json.loads(result.output)["diagnostics"]] == [
        ("warning", "image_without_tag")
    ]


def test_a_policy_reports_a_floating_tag_the_plain_command_ignores(tmp_path):
    root = _solution(tmp_path, policy=_DENY)
    (root / "module.yaml").write_text(
        (root / "module.yaml").read_text(encoding="utf-8").replace("redis:7", "redis:latest"), encoding="utf-8"
    )
    assert _invoke(root, "app").exit_code == EXIT_VALIDATION
    (tmp_path / "plain").mkdir()
    plain = _solution(tmp_path / "plain")
    (plain / "module.yaml").write_text(
        (plain / "module.yaml").read_text(encoding="utf-8").replace("redis:7", "redis:latest"), encoding="utf-8"
    )
    assert _invoke(plain, "app").exit_code == EXIT_SUCCESS


def test_a_policy_that_is_satisfied_exits_zero_with_no_findings(tmp_path):
    result = _invoke(_solution(tmp_path, policy=_DENY), "app", "-o", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert json.loads(result.output)["diagnostics"] == []


def test_the_policy_judges_the_deployment_under_pin(tmp_path):
    root = _solution(tmp_path, with_tag=False, policy=_DENY)
    assert _invoke(root, "other").exit_code == EXIT_VALIDATION
    assert _invoke(root, "other", "--pin", "prd").exit_code == EXIT_SUCCESS


# `integration` (docs/work/artifact-gap-analysis.md, design 6 step 1): which credentials a copy needs, by name.


def _rows(result) -> dict:
    return {row["name"]: row for row in _data(result)["images"]}


def test_json_names_an_artifacts_integration(tmp_path):
    rows = _rows(_invoke(_solution(tmp_path, integration=True), "app", "-o", "json"))
    assert rows["catalog"]["integration"] == "feed"
    assert rows["redis"]["integration"] is None


def test_json_reports_no_integration_when_the_artifact_names_none(tmp_path):
    assert _rows(_invoke(_solution(tmp_path), "app", "-o", "json"))["catalog"]["integration"] is None


def test_the_table_shows_an_integration_column_only_when_an_image_has_one(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with_integration = _invoke(_solution(tmp_path / "a", integration=True), "app")
    without = _invoke(_solution(tmp_path / "b"), "app")

    assert with_integration.exit_code == EXIT_SUCCESS, with_integration.output
    lines = with_integration.output.splitlines()
    header = next(line for line in lines if "NAME" in line and "ORIGIN" in line)
    assert header.split() == ["NAME", "ORIGIN", "IMAGE", "TAG", "SOURCE", "INTEGRATION", "USED", "BY"]
    column = header.index("INTEGRATION")

    def _cell(name: str) -> str:
        return next(line for line in lines if line.strip().startswith(name))[column:].split()[0]

    assert (_cell("catalog"), _cell("redis")) == ("feed", "-")
    assert without.exit_code == EXIT_SUCCESS, without.output
    assert "INTEGRATION" not in without.output


def test_the_integration_never_carries_a_credential(tmp_path):
    """Only the name: nothing is resolved, so not even the key the integration reads its token from appears."""
    result = _invoke(_solution(tmp_path, integration=True), "app", "-o", "json")
    assert "FEED_API_KEY" not in result.output
