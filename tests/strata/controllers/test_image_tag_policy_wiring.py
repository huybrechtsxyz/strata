#!/usr/bin/env python3
"""`image_tag_policy` in `build_run()` and `deploy_run()` (docs/design/image-tag-policy.md, phase 3)."""

from pathlib import Path

import pytest

from strata.controllers.build_controller import build_run
from strata.controllers.deploy_controller import deploy_run
from strata.controllers.policy_results import read_policy_results
from strata.controllers.solution_context import open_solution
from strata.utils.transport import CommandResult

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(
    tmp_path: Path, *, policy: str = "  image_tag_policy: {}\n", artifact_tag: str = "", version_pins: bool = False
) -> Path:
    """One Terraform step and artifact `catalog`, reached by a `store: artifact` variable.

    With `version_pins`, a `prd` version document pins `catalog` to 1.4.0 but the deployment does not name it,
    so only `--pin prd` applies it.
    """
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    (root / "infra").mkdir()
    (root / "infra" / "main.tf").write_text("# root module\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(
        root,
        "resource.yaml",
        "resource",
        "r1",
        "  provider_type: local\n  resource_type: server\n  category: compute\n  default_tags:\n    managed-by: strata\n",
    )
    _write(
        root,
        "workspace.yaml",
        "workspace",
        "main",
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apply_infra\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n" + policy,
    )
    _write(root, "artifact.yaml", "artifact", "catalog", f"  image_name: registry/catalog\n{artifact_tag}")
    _write(
        root,
        "environment.yaml",
        "environment",
        "prd",
        "  variables:\n    - key: CATALOG_TAG\n      store: artifact\n      value: catalog\n      field: image_tag\n",
    )
    if version_pins:
        _write(root, "version.yaml", "version", "prd", "  pins:\n    artifacts:\n      catalog: '1.4.0'\n")
    _write(root, "deployment.yaml", "deployment", "app", "  workspace: main\n  environments:\n    - prd\n")
    return root


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


@pytest.fixture
def terraform(monkeypatch):
    calls: list[list[str]] = []

    def _run(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        calls.append(list(args))
        return CommandResult(returncode=0, stdout="", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _run)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    return calls


def _codes(diagnostics) -> list[str | None]:
    return [d.code for d in diagnostics.errors + diagnostics.warnings]


# ---------------------------------------------------------------------------
# build run
# ---------------------------------------------------------------------------


def test_build_run_fails_before_writing_anything_when_an_image_has_no_tag(tmp_path):
    build_path = tmp_path / "build"
    diagnostics = build_run(_context(_solution(tmp_path)), "app", build_path)
    assert _codes(diagnostics) == ["image_without_tag"]
    assert not build_path.exists()


def test_build_run_does_not_clean_an_existing_build_path_when_the_policy_denies(tmp_path):
    build_path = tmp_path / "build"
    build_path.mkdir()
    (build_path / "keep.txt").write_text("previous build", encoding="utf-8")
    diagnostics = build_run(_context(_solution(tmp_path)), "app", build_path, clean=True)
    assert not diagnostics.ok
    assert (build_path / "keep.txt").is_file()


def test_build_run_dry_run_also_fails(tmp_path):
    diagnostics = build_run(_context(_solution(tmp_path)), "app", tmp_path / "build", dry_run=True)
    assert _codes(diagnostics) == ["image_without_tag"]


def test_build_run_passes_and_records_the_result_when_every_image_is_tagged(tmp_path):
    build_path = tmp_path / "build"
    root = _solution(tmp_path, artifact_tag="  image_tag: '1.0.0'\n")
    diagnostics = build_run(_context(root), "app", build_path)
    assert diagnostics.ok, diagnostics.messages()
    [recorded] = read_policy_results(build_path)
    assert (recorded.policy_name, recorded.policy_type, recorded.phase, recorded.enforcement, recorded.passed) == (
        "image_tag_policy",
        "image_tag",
        "build",
        "deny",
        True,
    )
    assert recorded.violations == []


def test_build_run_continues_on_a_warn_breach_and_records_it_as_passed_with_violations(tmp_path):
    build_path = tmp_path / "build"
    root = _solution(tmp_path, policy="  image_tag_policy:\n    enforcement: warn\n")
    diagnostics = build_run(_context(root), "app", build_path)
    assert diagnostics.ok
    assert "image_without_tag" in _codes(diagnostics)
    [recorded] = read_policy_results(build_path)
    assert (recorded.passed, len(recorded.violations)) == (True, 1)


def test_build_run_does_not_judge_an_image_when_the_policy_is_unset(tmp_path):
    build_path = tmp_path / "build"
    diagnostics = build_run(_context(_solution(tmp_path, policy="")), "app", build_path)
    assert diagnostics.ok, diagnostics.messages()
    assert read_policy_results(build_path) is None


def test_build_run_honours_a_pin_given_with_dash_dash_pin(tmp_path):
    root = _solution(tmp_path, version_pins=True)
    assert not build_run(_context(root), "app", tmp_path / "unpinned").ok
    assert build_run(_context(root), "app", tmp_path / "pinned", pin="prd").ok


def test_build_run_skips_the_policy_for_a_ring_that_is_not_listed(tmp_path):
    root = _solution(tmp_path, policy="  image_tag_policy:\n    rings: [prd]\n", version_pins=True)
    version = root / "version.yaml"
    version.write_text(
        version.read_text(encoding="utf-8").replace("  pins:", "  promotion:\n    ring: dev\n    order: 1\n  pins:"),
        encoding="utf-8",
    )
    (root / "deployment.yaml").write_text(
        _HEADER.format(kind="deployment", name="app")
        + "  workspace: main\n  version: prd\n  environments:\n    - prd\n",
        encoding="utf-8",
    )
    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)
    assert diagnostics.ok, diagnostics.messages()
    assert "image_tag_policy_not_applied" in [d.code for d in diagnostics.infos]


# ---------------------------------------------------------------------------
# deploy run
# ---------------------------------------------------------------------------


def _built(tmp_path: Path, **solution) -> tuple[Path, Path]:
    root = _solution(tmp_path, **solution)
    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)
    assert diagnostics.ok, diagnostics.messages()
    return root, build_path


def test_deploy_run_denies_an_image_whose_tag_was_removed_after_the_build_and_runs_no_step(tmp_path, terraform):
    root, build_path = _built(tmp_path, artifact_tag="  image_tag: '1.0.0'\n")
    (root / "artifact.yaml").write_text(
        _HEADER.format(kind="artifact", name="catalog") + "  image_name: registry/catalog\n", encoding="utf-8"
    )

    diagnostics = deploy_run(_context(root), "app", build_path, force=True)

    assert _codes(diagnostics) == ["image_without_tag"]
    assert terraform == []
    results = {r.policy_name: r for r in read_policy_results(build_path)}
    assert (results["image_tag_policy"].phase, results["image_tag_policy"].passed) == ("deploy", False)


def test_deploy_run_replaces_the_build_result_with_its_own(tmp_path, terraform):
    root, build_path = _built(tmp_path, policy="  image_tag_policy:\n    enforcement: warn\n", version_pins=True)
    [from_build] = read_policy_results(build_path)
    assert (from_build.phase, len(from_build.violations)) == ("build", 1)

    diagnostics = deploy_run(_context(root), "app", build_path, force=True, pin="prd")

    assert diagnostics.ok, diagnostics.messages()
    [from_deploy] = read_policy_results(build_path)
    assert (from_deploy.phase, from_deploy.passed, from_deploy.violations) == ("deploy", True, [])
    assert terraform


def test_deploy_run_without_a_policy_records_nothing_and_does_not_judge_the_images(tmp_path, terraform):
    root, build_path = _built(tmp_path, policy="")
    diagnostics = deploy_run(_context(root), "app", build_path, force=True)
    assert diagnostics.ok, diagnostics.messages()
    assert read_policy_results(build_path) is None


def test_deploy_run_evaluates_the_policy_in_a_smoke_test_too(tmp_path, terraform):
    root, build_path = _built(tmp_path, artifact_tag="  image_tag: '1.0.0'\n")
    (root / "artifact.yaml").write_text(
        _HEADER.format(kind="artifact", name="catalog") + "  image_name: registry/catalog\n", encoding="utf-8"
    )
    diagnostics = deploy_run(_context(root), "app", build_path, smoke_test=True)
    assert _codes(diagnostics) == ["image_without_tag"]
    # A smoke test writes nothing: the build's entry is still the only one.
    assert [(r.phase, r.passed) for r in read_policy_results(build_path)] == [("build", True)]
