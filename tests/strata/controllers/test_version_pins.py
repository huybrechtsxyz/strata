#!/usr/bin/env python3
"""Tests for version pin checking (`version_pins.py`)."""

from pathlib import Path

from strata.controllers.solution_context import open_solution

MANIFEST_TEMPLATE = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
  remotes:
    - name: strata-remote
      type: git
      url: https://example.com/a.git
      reference: v1.0.0
    - name: external-remote
      type: git
      url: https://example.com/b.git
      reference: v1.0.0
      fetch: external
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solution(tmp_path: Path, pins: str) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST_TEMPLATE)
    _write(
        root,
        "module.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: web-module
spec:
  type: docker-compose
  default_labels:
    environment: test
  source:
    source_path: modules/web
  services:
    - name: web
""",
    )
    _write(
        root,
        "chart-module.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: chart-module
spec:
  type: helm
  default_labels:
    environment: test
  source:
    remote: strata-remote
    chart_name: some-chart
""",
    )
    _write(root, "version.yaml", f"apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n{pins}\n")
    return root


def _resolve(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    context.resolve()
    return context


# ---------------------------------------------------------------------------
# Resolvable pins produce nothing
# ---------------------------------------------------------------------------


def test_resolvable_pins_produce_no_findings(tmp_path):
    context = _resolve(
        _solution(
            tmp_path,
            "  pins:\n"
            "    images:\n      web: nginx:1.27\n"
            "    charts:\n      chart-module: 2.0.0\n"
            "    remotes:\n      strata-remote: v2.0.0\n",
        )
    )
    assert context.ok


# ---------------------------------------------------------------------------
# Stale pins — warnings, not failures
# ---------------------------------------------------------------------------


def test_image_pin_matching_no_service_is_a_warning(tmp_path):
    context = _resolve(_solution(tmp_path, "  pins:\n    images:\n      ghost-service: nginx:1.27\n"))
    assert context.ok  # warnings alone do not fail the run
    warning = context.diagnostics.warnings[0]
    assert warning.code == "stale_pin"
    assert "ghost-service" in warning.message


def test_chart_pin_matching_no_module_is_a_warning(tmp_path):
    context = _resolve(_solution(tmp_path, "  pins:\n    charts:\n      ghost-module: 1.0.0\n"))
    assert context.ok
    assert context.diagnostics.warnings[0].code == "stale_pin"


def test_remote_pin_matching_no_remote_is_a_warning(tmp_path):
    context = _resolve(_solution(tmp_path, "  pins:\n    remotes:\n      ghost-remote: v1.0.0\n"))
    assert context.ok
    assert context.diagnostics.warnings[0].code == "stale_pin"


# ---------------------------------------------------------------------------
# Pin exists but cannot apply
# ---------------------------------------------------------------------------


def test_chart_pin_against_a_git_mode_module_is_a_warning(tmp_path):
    """The module exists, but has no chart_name — a real, non-stale mismatch."""
    context = _resolve(_solution(tmp_path, "  pins:\n    charts:\n      web-module: 1.0.0\n"))
    assert context.ok
    warning = context.diagnostics.warnings[0]
    assert warning.code == "pin_not_applicable"
    assert "not chart-based" in warning.message


def test_remote_pin_against_fetch_external_is_an_error(tmp_path):
    """ADR-0019: CI already placed it before strata ran — this must fail the run."""
    context = _resolve(_solution(tmp_path, "  pins:\n    remotes:\n      external-remote: v9.9.9\n"))
    assert not context.ok
    error = context.diagnostics.errors[0]
    assert error.code == "pin_not_applicable"
    assert "fetch:external" in error.message


# ---------------------------------------------------------------------------
# Attribution and scope
# ---------------------------------------------------------------------------


def test_finding_is_attributed_to_the_version_document(tmp_path):
    context = _resolve(_solution(tmp_path, "  pins:\n    images:\n      ghost: x\n"))
    assert "version.yaml" in str(context.diagnostics.warnings[0].source)


def test_location_names_the_category_and_key(tmp_path):
    context = _resolve(_solution(tmp_path, "  pins:\n    images:\n      ghost: x\n"))
    assert context.diagnostics.warnings[0].location == "spec.pins.images.ghost"


def test_checked_once_per_version_document_not_per_referencing_deployment(tmp_path):
    """A version file shared by two deployments must not be double-reported."""
    root = _solution(tmp_path, "  pins:\n    images:\n      ghost: x\n")
    _write(
        root,
        "workspace.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
""",
    )
    _write(
        root,
        "provider.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
""",
    )
    for name in ("dep-a", "dep-b"):
        _write(
            root,
            f"{name}.yaml",
            f"apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: {name}\n"
            "spec:\n  workspace: main\n  environments: [prd]\n  version: prd\n",
        )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )

    context = _resolve(root)
    assert len(context.diagnostics.warnings) == 1


def test_no_version_documents_means_nothing_to_check(tmp_path):
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST_TEMPLATE)
    context = _resolve(root)
    assert context.ok
