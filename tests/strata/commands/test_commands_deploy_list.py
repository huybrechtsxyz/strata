#!/usr/bin/env python3
"""Tests for `strata deploy list` (docs/work/vscode-integrations-policies-views.md V1)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE
from strata.controllers.deployment_resolution import list_deployments
from strata.controllers.solution_context import open_solution

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    (root / relative).write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path) -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n",
    )
    _write(root, "environment.yaml", "environment", "prd", "  {}\n")
    _write(root, "base.yaml", "deployment", "base", "  partial: true\n  workspace: main\n")
    _write(root, "app.yaml", "deployment", "app", "  extends: base\n  environments:\n    - prd\n")
    return root


def _invoke(*args: str):
    return CliRunner().invoke(cli, ["deploy", "list", *args])


def test_list_resolves_extends_and_marks_partials(tmp_path):
    rows = {row.name: row for row in list_deployments(open_solution(_solution(tmp_path)).controller.index)}
    assert (rows["app"].workspace, rows["app"].environments, rows["app"].partial) == ("main", ("prd",), False)
    assert (rows["base"].workspace, rows["base"].partial) == ("main", True)
    assert Path(rows["app"].source).name == "app.yaml"


def test_list_json_shape(tmp_path):
    result = _invoke("--path", str(_solution(tmp_path)), "--output", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    rows = json.loads(result.output)["data"]["deployments"]
    assert [row["name"] for row in rows] == ["app", "base"]
    assert set(rows[0]) == {
        "name",
        "workspace",
        "extends",
        "tenant",
        "version",
        "ring",
        "order",
        "wave",
        "environments",
        "partial",
        "source",
    }
    assert rows[0]["environments"] == ["prd"]
    assert rows[0]["extends"] == "base"
    assert (rows[0]["tenant"], rows[0]["version"], rows[0]["ring"]) == (None, None, None)


def test_list_console_marks_partials(tmp_path):
    result = _invoke("--path", str(_solution(tmp_path)))
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "base (partial)" in result.output
    assert "app " in result.output and "main" in result.output and "prd" in result.output


def test_list_outside_a_solution_is_a_usage_error(tmp_path):
    result = _invoke("--path", str(tmp_path))
    assert result.exit_code == EXIT_USAGE, result.output


def test_list_does_not_require_a_valid_solution(tmp_path):
    """Schema-only: a dangling reference elsewhere does not stop the listing."""
    root = _solution(tmp_path)
    _write(root, "broken.yaml", "deployment", "broken", "  workspace: main\n  environments:\n    - ghost\n")
    result = _invoke("--path", str(root), "--output", "json")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "broken" in [row["name"] for row in json.loads(result.output)["data"]["deployments"]]


def test_the_deployment_group_no_longer_exists():
    result = CliRunner().invoke(cli, ["deployment", "list"])
    assert result.exit_code == EXIT_USAGE, result.output


def test_envelope_names_deploy_list_and_nothing_is_rendered(tmp_path):
    """It is a read-only listing: nothing is rendered or executed."""
    result = _invoke("--path", str(_solution(tmp_path)), "--output", "json")
    assert json.loads(result.output)["command"] == "deploy list"
    assert not (tmp_path / "sln" / "build").exists()


# ---------------------------------------------------------------------------
# tenant / version / ring, and filters
# ---------------------------------------------------------------------------

_TENANT = '  display_name: "{name}"\n  geographies:\n    - europe\n'


def _matrix_solution(tmp_path: Path) -> Path:
    """Two tenants and three version documents (dev ring, prd canary ring, untagged) over `base`."""
    root = _solution(tmp_path)
    for name in ("t1", "t2"):
        _write(root, f"tenant-{name}.yaml", "tenant", name, _TENANT.format(name=name))
    _write(root, "v-dev.yaml", "version", "v-dev", "  workspace: main\n  promotion:\n    ring: dev\n    order: 1\n")
    _write(
        root,
        "v-prd.yaml",
        "version",
        "v-prd",
        "  workspace: main\n  promotion:\n    ring: prd\n    order: 2\n    wave: canary\n",
    )
    _write(root, "v-plain.yaml", "version", "v-plain", "  workspace: main\n")
    _write(
        root,
        "dev-t1.yaml",
        "deployment",
        "dev-t1",
        "  extends: base\n  environments:\n    - prd\n  tenant: t1\n  version: v-dev\n",
    )
    _write(
        root,
        "prd-t1.yaml",
        "deployment",
        "prd-t1",
        "  extends: base\n  environments:\n    - prd\n  tenant: t1\n  version: v-prd\n",
    )
    _write(
        root,
        "prd-t2.yaml",
        "deployment",
        "prd-t2",
        "  extends: base\n  environments:\n    - prd\n  tenant: t2\n  version: v-prd\n",
    )
    _write(
        root,
        "plain.yaml",
        "deployment",
        "plain",
        "  extends: base\n  environments:\n    - prd\n  version: v-plain\n",
    )
    # A partial that carries tenant and version, and a leaf that only inherits them.
    _write(
        root,
        "tenant-base.yaml",
        "deployment",
        "tenant-base",
        "  partial: true\n  extends: base\n  tenant: t2\n  version: v-dev\n",
    )
    _write(root, "inherit.yaml", "deployment", "inherit", "  extends: tenant-base\n  environments:\n    - prd\n")
    return root


def _names(result) -> list[str]:
    assert result.exit_code == EXIT_SUCCESS, result.output
    return [row["name"] for row in json.loads(result.output)["data"]["deployments"]]


def _rows(root: Path) -> dict:
    return {row.name: row for row in list_deployments(open_solution(root).controller.index)}


def test_tenant_and_version_are_resolved_through_extends(tmp_path):
    rows = _rows(_matrix_solution(tmp_path))
    assert (rows["inherit"].tenant, rows["inherit"].version) == ("t2", "v-dev")
    assert rows["inherit"].extends == "tenant-base"
    assert rows["base"].extends is None


def test_ring_order_and_wave_come_from_the_version_document(tmp_path):
    rows = _rows(_matrix_solution(tmp_path))
    assert (rows["prd-t1"].ring, rows["prd-t1"].order, rows["prd-t1"].wave) == ("prd", 2, "canary")
    assert (rows["dev-t1"].ring, rows["dev-t1"].order, rows["dev-t1"].wave) == ("dev", 1, None)


def test_an_untagged_or_missing_version_has_no_ring(tmp_path):
    root = _matrix_solution(tmp_path)
    _write(
        root, "ghost.yaml", "deployment", "ghost", "  extends: base\n  environments:\n    - prd\n  version: nowhere\n"
    )
    rows = _rows(root)
    assert (rows["plain"].version, rows["plain"].ring, rows["plain"].order) == ("v-plain", None, None)
    assert (rows["ghost"].version, rows["ghost"].ring) == ("nowhere", None)


def test_console_shows_tenant_version_and_ring(tmp_path):
    result = _invoke("--path", str(_matrix_solution(tmp_path)))
    assert result.exit_code == EXIT_SUCCESS, result.output
    header = next(line for line in result.output.splitlines() if line.strip().startswith("NAME"))
    assert header.split() == ["NAME", "WORKSPACE", "TENANT", "VERSION", "RING", "ENVIRONMENTS"]
    assert any(line.split()[:5] == ["prd-t1", "main", "t1", "v-prd", "prd"] for line in result.output.splitlines())


def test_filter_by_workspace_tenant_version_and_ring(tmp_path):
    root = str(_matrix_solution(tmp_path))
    assert sorted(_names(_invoke("--path", root, "-o", "json", "--tenant", "t1"))) == ["dev-t1", "prd-t1"]
    assert sorted(_names(_invoke("--path", root, "-o", "json", "--version", "v-prd"))) == ["prd-t1", "prd-t2"]
    assert sorted(_names(_invoke("--path", root, "-o", "json", "--ring", "prd"))) == ["prd-t1", "prd-t2"]
    assert "app" in _names(_invoke("--path", root, "-o", "json", "--workspace", "main"))


def test_filters_combine_with_and(tmp_path):
    root = str(_matrix_solution(tmp_path))
    assert _names(_invoke("--path", root, "-o", "json", "--tenant", "t2", "--ring", "prd")) == ["prd-t2"]
    assert _names(_invoke("--path", root, "-o", "json", "--tenant", "t1", "--version", "v-plain")) == []


def test_filters_match_resolved_values_not_declared_ones(tmp_path):
    root = str(_matrix_solution(tmp_path))
    assert sorted(_names(_invoke("--path", root, "-o", "json", "--tenant", "t2"))) == [
        "inherit",
        "prd-t2",
        "tenant-base",
    ]


def test_deployable_leaves_out_partial_bases(tmp_path):
    names = _names(_invoke("--path", str(_matrix_solution(tmp_path)), "-o", "json", "--deployable"))
    assert "base" not in names and "tenant-base" not in names
    assert {"app", "inherit", "prd-t1"} <= set(names)


def test_an_empty_match_is_not_an_error(tmp_path):
    root = str(_matrix_solution(tmp_path))
    result = _invoke("--path", root, "--tenant", "t1", "--version", "v-plain")
    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "no deployments match" in result.output


@pytest.mark.parametrize(
    ("option", "value"),
    [("--workspace", "ghost"), ("--tenant", "ghost"), ("--version", "ghost"), ("--ring", "prdd")],
)
def test_a_filter_naming_nothing_is_a_usage_error(tmp_path, option, value):
    """A typo must not turn into an empty CI matrix that passes."""
    result = _invoke("--path", str(_matrix_solution(tmp_path)), option, value)
    assert result.exit_code == EXIT_USAGE, result.output
    assert value in result.output
