#!/usr/bin/env python3
"""Tests for `artifact_usage` (docs/work/artifact-gap-analysis.md, design 8): which artifacts some deployment
uses, the `artifact_unused` warning, and `version update` leaving unused artifacts out."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_VALIDATION
from strata.controllers.artifact_usage import check_unused_artifacts, unused_artifacts, used_artifacts
from strata.controllers.solution_context import open_solution
from strata.controllers.version_controller import reconcile_version

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _solution(tmp_path: Path, *, deployments: str = "app", service_artifact: str | None = "web") -> Path:
    """A Compose module whose service names `service_artifact`, artifacts `web` and `orphan` (nothing uses it),
    version `prd` that pins nothing, and the deployments named in `deployments` (`app`, `base`)."""
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
        "  provisioners:\n    - name: stacks\n      tool: compose\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: apps\n      provisioner: stacks\n      namespaces:\n        - apps\n",
    )
    image = f"      artifact: {service_artifact}\n" if service_artifact else "      image: redis:7\n"
    _write(
        root,
        "module.yaml",
        "module",
        "stack",
        "  source:\n    source_path: services/stack\n  type: compose\n  default_labels:\n    app: stack\n"
        f"  services:\n    - name: web\n{image}",
    )
    _write(
        root,
        "namespace.yaml",
        "namespace",
        "apps",
        "  default_labels:\n    app: apps\n  modules:\n    - name: stack\n      module: stack\n",
    )
    _write(root, "artifacts/web.yaml", "artifact", "web", "  image_name: example/web\n  image_tag: '1.0.0'\n")
    _write(root, "artifacts/orphan.yaml", "artifact", "orphan", "  image_name: example/orphan\n")
    _write(root, "environment.yaml", "environment", "prd", "  {}\n")
    _write(root, "version.yaml", "version", "prd", "  pins: {}\n")
    if "app" in deployments:
        _write(
            root,
            "app.yaml",
            "deployment",
            "app",
            "  workspace: main\n  docker_context: apps-prd\n  environments:\n    - prd\n",
        )
    if "base" in deployments:
        _write(root, "base.yaml", "deployment", "base", "  partial: true\n  workspace: main\n")
    return root


def _context(root: Path):
    context = open_solution(root)
    context.resolve()
    assert context.ok, context.diagnostics.messages()
    return context


def test_an_artifact_a_deployment_runs_is_used_and_one_nothing_references_is_not(tmp_path):
    context = _context(_solution(tmp_path))
    assert used_artifacts(context) == {"web"}
    assert unused_artifacts(context) == ["orphan"]


def test_the_warning_names_the_artifact_in_its_own_file(tmp_path):
    findings = check_unused_artifacts(_context(_solution(tmp_path)))

    assert [(d.code, d.severity.value, d.location) for d in findings.items] == [
        ("artifact_unused", "warning", "meta.name")
    ]
    assert findings.items[0].source.endswith("orphan.yaml")
    assert "orphan" in findings.messages()[0]
    assert findings.ok, "a warning, not an error"


def test_with_no_deployment_every_artifact_is_unused(tmp_path):
    assert unused_artifacts(_context(_solution(tmp_path, deployments=""))) == ["orphan", "web"]


def test_a_partial_base_deploys_nothing_and_uses_nothing(tmp_path):
    assert unused_artifacts(_context(_solution(tmp_path, deployments="base"))) == ["orphan", "web"]


def test_nothing_is_reported_when_a_deployment_cannot_be_evaluated(tmp_path, monkeypatch):
    """An artifact that deployment uses would otherwise be called unused."""
    import strata.controllers.artifact_usage as artifact_usage
    from strata.utils.errors import UsageError

    def _fails(*_args, **_kwargs):
        raise UsageError("cannot resolve")

    context = _context(_solution(tmp_path))
    monkeypatch.setattr(artifact_usage, "effective_images", _fails)
    assert used_artifacts(context) is None
    assert unused_artifacts(context) == []
    assert check_unused_artifacts(context).items == []


@pytest.mark.parametrize("service_artifact", ["web", None], ids=["artifact-pin", "image-pin"])
def test_looking_for_unused_artifacts_logs_no_pin(tmp_path, monkeypatch, service_artifact):
    """`strata validate` applies nothing, so it must not log a pin as applied (ADR-0019 decision 7 is about
    applications): `effective_images()` re-derives the tags with logging off."""
    import strata.controllers.artifact_tokens as artifact_tokens
    import strata.controllers.workload_controller as workload_controller

    logged: list[str] = []
    for module in (artifact_tokens, workload_controller):
        monkeypatch.setattr(module, "log_pin_applied", lambda _category, name, *_args: logged.append(name))
    root = _solution(tmp_path, service_artifact=service_artifact)
    _write(
        root,
        "version.yaml",
        "version",
        "prd",
        "  pins:\n    artifacts:\n      web: '2.0.0'\n    images:\n      web: redis:7.2\n",
    )
    _write(
        root,
        "app.yaml",
        "deployment",
        "app",
        "  workspace: main\n  docker_context: apps-prd\n  version: prd\n  environments:\n    - prd\n",
    )
    context = _context(root)

    check_unused_artifacts(context)
    rows = used_artifacts(context)

    assert rows == ({"web"} if service_artifact else set())
    assert logged == []


def _validate(root: Path, *args: str):
    return CliRunner().invoke(cli, ["validate", str(root), "-o", "json", *args])


def test_validate_reports_the_unused_artifact(tmp_path):
    result = _validate(_solution(tmp_path))

    assert result.exit_code == EXIT_SUCCESS, result.output
    codes = [d["code"] for d in json.loads(result.output)["diagnostics"]]
    assert codes == ["artifact_unused"]


def test_validate_strict_fails_on_the_unused_artifact(tmp_path):
    assert _validate(_solution(tmp_path), "--strict").exit_code == EXIT_VALIDATION


def test_validate_does_not_look_for_unused_artifacts_in_a_broken_solution(tmp_path):
    root = _solution(tmp_path)
    (root / "app.yaml").write_text(
        _HEADER.format(kind="deployment", name="app") + "  workspace: ghost\n  environments:\n    - prd\n",
        encoding="utf-8",
    )
    codes = [d["code"] for d in json.loads(_validate(root).output)["diagnostics"]]
    assert "artifact_unused" not in codes
    assert codes, "the broken reference itself is reported"


def test_reconcile_offers_no_pin_for_an_unused_artifact(tmp_path):
    context = _context(_solution(tmp_path))
    entry = context.controller.index.get(*_version_ref())
    assert entry is not None

    offered = reconcile_version(context.controller.index, context.controller.solution, entry.model)
    skipped = reconcile_version(
        context.controller.index, context.controller.solution, entry.model, unused_artifacts=["orphan"]
    )

    assert {(r.name, r.action) for r in offered if r.category == "artifacts"} == {
        ("web", "add"),
        ("orphan", "needs_value"),
    }
    assert {(r.name, r.action) for r in skipped if r.category == "artifacts"} == {("web", "add")}


def _version_ref():
    from strata.models.common_models import PlatformKind

    return PlatformKind.VERSION, "prd"


def test_an_existing_pin_on_an_unused_artifact_is_not_stale(tmp_path):
    root = _solution(tmp_path)
    _write(root, "version.yaml", "version", "prd", "  pins:\n    artifacts:\n      orphan: '2.0.0'\n")
    context = _context(root)
    entry = context.controller.index.get(*_version_ref())
    assert entry is not None

    rows = reconcile_version(
        context.controller.index, context.controller.solution, entry.model, unused_artifacts=["orphan"]
    )

    assert not any(r.name == "orphan" for r in rows)


@pytest.mark.parametrize("output", ["console", "json"])
def test_version_update_says_which_unused_artifacts_it_left_out(tmp_path, output):
    result = CliRunner().invoke(cli, ["version", "update", "prd", "--path", str(_solution(tmp_path)), "-o", output])

    assert result.exit_code == EXIT_SUCCESS, result.output
    assert "artifact_unused" in result.output
    assert "'orphan'" in result.output
    if output == "json":
        candidates = json.loads(result.output)["data"]["candidates"]
        assert not any(c["name"] == "orphan" for c in candidates)
