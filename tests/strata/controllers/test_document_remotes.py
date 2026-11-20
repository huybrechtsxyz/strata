#!/usr/bin/env python3
"""Documents read from other repositories: `spec.discovery.remotes`.

docs/design/remote-commit-provenance.md, "Documents in other repositories". The solution (`platform-config` in the doc)
holds the shared
documents; each zone's environments and deployments live in a checkout of their own, beside the solution or inside
it, listed in `discovery.remotes`.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from strata.controllers.build_controller import build_run
from strata.controllers.solution_context import open_solution
from strata.models.common_models import PlatformKind

_API = "apiVersion: strata.huybrechts.xyz/v2\n"
_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
_needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _manifest(remotes: dict[str, str], *, listed: list[str] | None = None, exclude: list[str] | None = None) -> str:
    """`strata.yaml` with one git path remote per `{name: path}`, and `discovery` listing `listed` (default: all)."""
    lines = [f"{_API}kind: solution\nmeta:\n  name: platform-config\nspec:\n  remotes:\n"]
    for name, path in remotes.items():
        lines.append(f"    - name: {name}\n      type: git\n      url: https://git.example.com/{name}.git\n")
        lines.append(f"      path: {path}\n")
    lines.append("  discovery:\n")
    lines.append(f"    remotes: [{', '.join(remotes if listed is None else listed)}]\n")
    if exclude:
        lines.append(f"    exclude: [{', '.join(exclude)}]\n")
    return "".join(lines)


def _shared(root: Path) -> None:
    """The trunk: provider, resource and workspace, which the zone's deployment names."""
    _write(root, "infra/main.tf", "# root module\n")
    _write(root, "provider.yaml", f"{_API}kind: provider\nmeta:\n  name: p1\nspec:\n  type: local\n  region: local\n")
    _write(
        root,
        "resource.yaml",
        f"{_API}kind: resource\nmeta:\n  name: r1\nspec:\n  provider_type: local\n  resource_type: server\n"
        "  category: compute\n  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        f"{_API}kind: workspace\nmeta:\n  name: main\nspec:\n  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "      scope: infra\n  resources:\n    - name: r1\n      resource: r1\n",
    )


def _zone(checkout: Path, zone: str = "zone01") -> None:
    """A zone's own documents: its environment and its deployment, on the trunk's workspace."""
    _write(checkout, "env-hub.yaml", f"{_API}kind: environment\nmeta:\n  name: {zone}-hub\nspec: {{}}\n")
    _write(
        checkout,
        "spoke/deploy-spoke.yaml",
        f"{_API}kind: deployment\nmeta:\n  name: {zone}-spoke\nspec:\n  workspace: main\n"
        f"  environments:\n    - {zone}-hub\n",
    )


def _solution(tmp_path: Path, *, zone_path: str = "../config-zone01", **manifest) -> tuple[Path, Path]:
    root = tmp_path / "platform-config"
    _write(root, "strata.yaml", _manifest({"config-zone01": zone_path}, **manifest))
    _shared(root)
    checkout = (root / zone_path).resolve()
    _zone(checkout)
    return root, checkout


def _codes(context) -> list:
    return [d.code for d in context.diagnostics.errors]


# ---------------------------------------------------------------------------
# The schema: path remotes only (C1 rule 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("remote", "message"),
    [
        ("    - name: zones\n      type: git\n      url: https://h/z.git\n      reference: main\n", "with a 'path'"),
        ("    - name: zones\n      type: local\n      url: ./zones\n", "with a 'path'"),
        ("    - name: other\n      type: git\n      url: https://h/o.git\n      path: ../o\n", "not a remote declared"),
    ],
    ids=["ref-mode", "local", "undeclared"],
)
def test_only_a_declared_git_path_remote_can_be_a_document_remote(tmp_path: Path, remote: str, message: str):
    root = tmp_path / "platform-config"
    _write(
        root,
        "strata.yaml",
        f"{_API}kind: solution\nmeta:\n  name: s\nspec:\n  remotes:\n{remote}  discovery:\n    remotes: [zones]\n",
    )

    context = open_solution(root)

    assert not context.ok
    assert any(message in d.message for d in context.diagnostics.errors), context.diagnostics.messages()


def test_a_document_remote_is_listed_once(tmp_path: Path):
    root, _ = _solution(tmp_path, listed=["config-zone01", "config-zone01"])

    assert not open_solution(root).ok


# ---------------------------------------------------------------------------
# Discovery over several roots (C1 rules 2, 3, 5, 8)
# ---------------------------------------------------------------------------


def test_documents_in_a_checkout_beside_the_solution_join_it(tmp_path: Path):
    root, checkout = _solution(tmp_path)

    context = open_solution(root)

    assert context.ok, context.diagnostics.messages()
    deployment = context.controller.index.get(PlatformKind.DEPLOYMENT, "zone01-spoke")
    assert deployment is not None and deployment.source == checkout / "spoke" / "deploy-spoke.yaml"
    assert context.controller.document_remotes == {"config-zone01": checkout}
    context.resolve()
    assert context.ok, context.diagnostics.messages()  # the zone's deployment resolves against the trunk


def test_a_checkout_inside_the_solution_root_loads_once(tmp_path: Path):
    root, checkout = _solution(tmp_path, zone_path="repos/config-zone01")

    context = open_solution(root)

    assert context.ok, context.diagnostics.messages()
    assert context.controller.index.get(PlatformKind.DEPLOYMENT, "zone01-spoke").source == checkout / "spoke" / (
        "deploy-spoke.yaml"
    )


def test_a_name_in_two_roots_is_the_existing_duplicate_error(tmp_path: Path):
    root, checkout = _solution(tmp_path)
    _write(root, "env-hub.yaml", f"{_API}kind: environment\nmeta:\n  name: zone01-hub\nspec: {{}}\n")

    context = open_solution(root)

    [error] = [d for d in context.diagnostics.errors if d.code == "duplicate_document"]
    assert str(root / "env-hub.yaml") in error.message and str(checkout / "env-hub.yaml") in error.message


def test_exclude_applies_inside_each_root_relative_to_it(tmp_path: Path):
    root, checkout = _solution(tmp_path, exclude=["templates/**"])
    broken = f"{_API}kind: environment\nmeta:\n  name: Not A Valid Name\nspec: {{}}\n"
    _write(root, "templates/env.yaml", broken)
    _write(checkout, "templates/env.yaml", broken)

    assert open_solution(root).ok


def test_a_nested_solution_inside_a_document_remote_is_a_boundary(tmp_path: Path):
    root, checkout = _solution(tmp_path)
    _write(checkout, "sandbox/strata.yaml", f"{_API}kind: solution\nmeta:\n  name: sandbox\nspec: {{}}\n")
    _write(checkout, "sandbox/env.yaml", f"{_API}kind: environment\nmeta:\n  name: zone01-hub\nspec: {{}}\n")

    assert open_solution(root).ok


def test_a_listed_remote_that_is_not_checked_out_fails_naming_it(tmp_path: Path):
    """D9: every listed document remote must be checked out."""
    root, checkout = _solution(tmp_path)
    shutil.rmtree(checkout)

    context = open_solution(root)

    [error] = [d for d in context.diagnostics.errors if d.code == "document_remote_missing"]
    assert "'config-zone01'" in error.message and str(checkout) in error.message
    assert error.location == "spec.discovery.remotes.0"
    assert error.hint is not None and "https://git.example.com/config-zone01.git" in error.hint


def test_a_document_remote_with_its_own_manifest_fails(tmp_path: Path):
    """One solution, one manifest: a checkout with a `strata.yaml` is a solution (C3's other direction)."""
    root, checkout = _solution(tmp_path)
    _write(checkout, "strata.yaml", f"{_API}kind: solution\nmeta:\n  name: zone\nspec: {{}}\n")

    assert _codes(open_solution(root)) == ["document_remote_has_manifest"]


@pytest.mark.parametrize("zone_path", [".", ".."], ids=["the-root", "a-parent"])
def test_a_document_remote_holding_the_solution_root_fails(tmp_path: Path, zone_path: str):
    root = tmp_path / "platform-config"
    _write(root, "strata.yaml", _manifest({"config-zone01": zone_path}))
    _shared(root)

    assert _codes(open_solution(root)) == ["document_remote_overlaps"]


def test_two_document_remotes_that_overlap_fail(tmp_path: Path):
    root = tmp_path / "platform-config"
    _write(root, "strata.yaml", _manifest({"zones": "../zones", "zone01": "../zones/zone01"}))
    _shared(root)
    _zone(tmp_path / "zones" / "zone01")

    context = open_solution(root)

    [error] = [d for d in context.diagnostics.errors if d.code == "document_remote_overlaps"]
    assert "'zone01'" in error.message and "'zones'" in error.message


def test_path_conventions_judge_only_documents_under_the_solution_root(tmp_path: Path):
    """`spec.paths` describes the solution's own layout; a zone document is outside it, not in breach of it."""
    root, _ = _solution(tmp_path)
    _write(
        root,
        "configuration.yaml",
        f"{_API}kind: configuration\nmeta:\n  name: cfg\nspec:\n  paths:\n    - name: spokes\n"
        '      scope: "spoke/**"\n      pattern: "deploy/hubs/{zone}/{spoke}"\n',
    )

    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    context.resolve()

    assert [d for d in context.diagnostics.items if "zone01-spoke" in d.message] == []


def test_without_discovery_remotes_a_path_remote_is_not_scanned(tmp_path: Path):
    root, _ = _solution(tmp_path, listed=[])

    context = open_solution(root)

    assert context.ok, context.diagnostics.messages()
    assert context.controller.index.get(PlatformKind.DEPLOYMENT, "zone01-spoke") is None
    assert context.controller.document_remotes == {}


# ---------------------------------------------------------------------------
# The build record (C1 rule 6)
# ---------------------------------------------------------------------------


def _committed(path: Path) -> str:
    _git(path, "init", "-b", "main")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "first")
    return _git(path, "rev-parse", "HEAD")


def _build(root: Path, tmp_path: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    build_path = tmp_path / "build"
    diagnostics = build_run(context.require_valid(), "zone01-spoke", build_path)
    assert diagnostics.ok, diagnostics.messages()
    return yaml.safe_load((build_path / "resolved.yaml").read_text(encoding="utf-8")), diagnostics


@_needs_git
def test_the_build_records_the_commit_of_each_document_remote(tmp_path: Path):
    root, checkout = _solution(tmp_path)
    head = _committed(checkout)

    resolved, diagnostics = _build(root, tmp_path)

    assert resolved["remotes"]["config-zone01"] == {
        "url": "https://git.example.com/config-zone01.git",
        "ref": None,
        "commit": head,
        "dirty": False,
    }
    assert [w.code for w in diagnostics.warnings if w.code == "remote_checkout_dirty"] == []


@_needs_git
def test_an_uncommitted_document_in_a_document_remote_makes_the_record_dirty(tmp_path: Path):
    root, checkout = _solution(tmp_path)
    _committed(checkout)
    _write(checkout, "env-hub.yaml", f"{_API}kind: environment\nmeta:\n  name: zone01-hub\nspec:\n  variables: []\n")

    resolved, diagnostics = _build(root, tmp_path)

    assert resolved["remotes"]["config-zone01"]["dirty"] is True
    assert "remote_checkout_dirty" in [w.code for w in diagnostics.warnings]


@_needs_git
def test_a_document_remote_inside_the_solution_does_not_make_the_solution_dirty(tmp_path: Path):
    root, checkout = _solution(tmp_path, zone_path="repos/config-zone01")
    _write(root, ".gitignore", "build/\n.strata/\n")
    _committed(checkout)
    _committed(root)
    _write(checkout, "notes.md", "# uncommitted, in the zone only\n")

    resolved, _ = _build(root, tmp_path)

    assert resolved["solution"]["dirty"] is False
    assert resolved["remotes"]["config-zone01"]["dirty"] is True


def test_a_document_remote_that_is_not_a_git_checkout_records_no_commit(tmp_path: Path):
    root, _ = _solution(tmp_path)

    resolved, diagnostics = _build(root, tmp_path)

    assert resolved["remotes"]["config-zone01"]["commit"] is None
    assert "audit_remote_commit_unavailable" in [w.code for w in diagnostics.warnings]
