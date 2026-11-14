#!/usr/bin/env python3
"""Tests for `document_unreferenced` (docs/work/unreferenced-documents.md)."""

import json
from pathlib import Path

from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS
from strata.controllers.solution_context import SolutionContext, open_solution

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path, *, deployment: bool = True) -> Path:
    """A workspace with one namespace and module, reached from deployment `app`."""
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(root, "environment.yaml", "environment", "prd", "  {}\n")
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n  namespaces:\n    - apps\n"
        "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n",
    )
    _write(root, "namespace.yaml", "namespace", "apps", "  modules:\n    - name: web\n      module: web\n")
    _write(
        root,
        "module.yaml",
        "module",
        "web",
        "  type: compose\n  source:\n    source_path: web\n  services:\n    - name: web\n      image: nginx:1.27\n",
    )
    if deployment:
        _write(root, "app.yaml", "deployment", "app", "  workspace: main\n  environments:\n    - prd\n")
    return root


def _unreferenced(root: Path) -> list[str]:
    context: SolutionContext = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    context.resolve()
    found = [d for d in context.diagnostics.items if d.code == "document_unreferenced"]
    assert all(d.severity.value == "info" and d.hint for d in found)
    return sorted(d.message for d in found)


def test_everything_a_deployment_reaches_is_not_reported(tmp_path: Path):
    assert _unreferenced(_solution(tmp_path)) == []


def test_a_module_no_namespace_lists_is_reported(tmp_path: Path):
    root = _solution(tmp_path)
    _write(root, "spare.yaml", "module", "spare", "  type: compose\n  source:\n    source_path: spare\n")

    assert _unreferenced(root) == ["Module 'spare' is reached by no deployment."]


def test_a_chain_nothing_reaches_is_reported_whole(tmp_path: Path):
    """A namespace no workspace lists, and the module only that namespace lists: both."""
    root = _solution(tmp_path)
    _write(root, "ns2.yaml", "namespace", "lab", "  modules:\n    - name: tool\n      module: tool\n")
    _write(root, "tool.yaml", "module", "tool", "  type: compose\n  source:\n    source_path: tool\n")

    assert _unreferenced(root) == [
        "Module 'tool' is reached by no deployment.",
        "Namespace 'lab' is reached by no deployment.",
    ]


def test_a_value_token_reaches_the_document_it_reads(tmp_path: Path):
    root = _solution(tmp_path)
    _write(
        root,
        "resource.yaml",
        "resource",
        "db",
        "  provider_type: local\n  resource_type: server\n  category: compute\n",
    )
    _write(
        root,
        "environment.yaml",
        "environment",
        "prd",
        '  variables:\n    - key: CATEGORY\n      store: constant\n      value: "${value:resource.db.spec.category}"\n',
    )

    assert _unreferenced(root) == []


def test_the_same_resource_without_the_token_is_reported(tmp_path: Path):
    root = _solution(tmp_path)
    _write(
        root,
        "resource.yaml",
        "resource",
        "db",
        "  provider_type: local\n  resource_type: server\n  category: compute\n",
    )

    assert _unreferenced(root) == ["Resource 'db' is reached by no deployment."]


def test_a_partial_base_is_not_reported(tmp_path: Path):
    root = _solution(tmp_path)
    _write(root, "env-base.yaml", "environment", "env-base", "  partial: true\n")

    assert _unreferenced(root) == []


def test_kinds_with_their_own_check_or_bound_by_type_are_not_reported(tmp_path: Path):
    root = _solution(tmp_path)
    _write(root, "artifact.yaml", "artifact", "img", "  image_name: acme/img\n")
    _write(root, "version.yaml", "version", "v1", "  pins: {}\n")

    assert _unreferenced(root) == []


def _unused_bases(root: Path) -> list[str]:
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    context.resolve()
    found = [d for d in context.diagnostics.items if d.code == "base_unused"]
    assert all(d.severity.value == "info" and d.hint for d in found)
    return sorted(d.message for d in found)


def test_a_partial_base_nothing_extends_is_reported_per_kind(tmp_path: Path):
    root = _solution(tmp_path)
    _write(root, "env-base.yaml", "environment", "env-base", "  partial: true\n")
    _write(root, "dep-base.yaml", "deployment", "dep-base", "  partial: true\n  workspace: main\n")

    assert _unused_bases(root) == [
        "Deployment 'dep-base' is a partial base that no deployment extends.",
        "Environment 'env-base' is a partial base that no environment extends.",
    ]


def test_an_extended_base_is_not_reported_and_a_chain_reports_its_unused_end(tmp_path: Path):
    """`app` extends `mid`, `mid` extends `root-base`: nothing to report. Another base extending `root-base` that no
    leaf extends is the one reported, not `root-base`."""
    root = _solution(tmp_path)
    _write(root, "root-base.yaml", "deployment", "root-base", "  partial: true\n  workspace: main\n")
    _write(root, "mid.yaml", "deployment", "mid", "  partial: true\n  extends: root-base\n")
    _write(root, "app.yaml", "deployment", "app", "  extends: mid\n  environments:\n    - prd\n")
    _write(root, "lonely.yaml", "deployment", "lonely", "  partial: true\n  extends: root-base\n")

    assert _unused_bases(root) == ["Deployment 'lonely' is a partial base that no deployment extends."]


def test_an_extended_environment_base_is_not_reported(tmp_path: Path):
    """The index holds the resolved environment, without `extends`; the check reads the documents as written."""
    root = _solution(tmp_path)
    _write(root, "env-base.yaml", "environment", "env-base", "  partial: true\n")
    _write(root, "environment.yaml", "environment", "prd", "  extends: env-base\n")

    assert _unused_bases(root) == []


def _graph_unreferenced(root: Path, *args: str):
    return CliRunner().invoke(cli, ["graph", str(root), "--unreferenced", *args])


def test_graph_unreferenced_lists_both_kinds_of_cleanup_candidate_in_json(tmp_path: Path):
    root = _solution(tmp_path)
    _write(root, "spare.yaml", "module", "spare", "  type: compose\n  source:\n    source_path: spare\n")
    _write(root, "env-base.yaml", "environment", "env-base", "  partial: true\n")

    result = _graph_unreferenced(root, "-o", "json")

    assert result.exit_code == EXIT_SUCCESS, result.output
    rows = json.loads(result.output)["data"]["unreferenced"]
    assert [(row["kind"], row["name"], row["reason"]) for row in rows] == [
        ("environment", "env-base", "base_unused"),
        ("module", "spare", "unreferenced"),
    ]
    assert rows[1]["path"].endswith("spare.yaml")


def test_graph_unreferenced_console_says_why_and_where(tmp_path: Path):
    root = _solution(tmp_path)
    _write(root, "spare.yaml", "module", "spare", "  type: compose\n  source:\n    source_path: spare\n")

    result = _graph_unreferenced(root)

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "module/spare  reached by no deployment  (spare.yaml)" in result.output


def test_graph_unreferenced_with_nothing_to_clean_up(tmp_path: Path):
    result = _graph_unreferenced(_solution(tmp_path))

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "(every document is used)" in result.output


def test_a_solution_without_a_deployable_deployment_is_not_judged(tmp_path: Path):
    root = _solution(tmp_path, deployment=False)
    _write(root, "spare.yaml", "module", "spare", "  type: compose\n  source:\n    source_path: spare\n")

    assert _unreferenced(root) == []
