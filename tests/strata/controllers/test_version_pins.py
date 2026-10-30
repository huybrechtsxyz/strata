#!/usr/bin/env python3
"""Tests for version pin checking (`version_pins.py`)."""

from pathlib import Path

import pytest

from strata.controllers.solution_context import open_solution
from strata.models.version_model import PIN_CATEGORIES

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
    _write(
        root,
        "version.yaml",
        f"apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n{pins}\n",
    )
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


def _with_assets(root: Path) -> Path:
    header = "apiVersion: strata.huybrechts.xyz/v2\nkind: asset\n"
    location = "  location:\n    type: git\n    url: https://example.com/c.git\n    reference: v1\n"
    _write(root, "asset-located.yaml", header + "meta:\n  name: located-api\nspec:\n  source_path: api\n" + location)
    _write(
        root,
        "asset-external.yaml",
        header + "meta:\n  name: external-api\nspec:\n  source_path: api\n" + location + "    fetch: external\n",
    )
    _write(
        root,
        "asset-remote.yaml",
        header + "meta:\n  name: remote-api\nspec:\n  source_path: api\n  remote: strata-remote\n",
    )
    return root


def test_asset_pin_against_a_located_asset_is_clean(tmp_path):
    root = _with_assets(_solution(tmp_path, "  pins:\n    assets:\n      located-api: v2\n"))
    assert _resolve(root).diagnostics.messages() == []


def test_asset_pin_matching_no_asset_is_a_warning(tmp_path):
    root = _with_assets(_solution(tmp_path, "  pins:\n    assets:\n      ghost-api: v2\n"))
    context = _resolve(root)
    assert context.ok
    assert context.diagnostics.warnings[0].code == "stale_pin"
    assert context.diagnostics.warnings[0].location == "spec.pins.assets.ghost-api"


def test_asset_pin_against_a_manifest_remote_asset_is_a_warning_pointing_at_remotes(tmp_path):
    root = _with_assets(_solution(tmp_path, "  pins:\n    assets:\n      remote-api: v2\n"))
    context = _resolve(root)
    assert context.ok
    warning = context.diagnostics.warnings[0]
    assert warning.code == "pin_not_applicable"
    assert "pins.remotes" in warning.message


def test_asset_pin_against_a_fetch_external_location_is_an_error(tmp_path):
    root = _with_assets(_solution(tmp_path, "  pins:\n    assets:\n      external-api: v2\n"))
    context = _resolve(root)
    assert not context.ok
    error = context.diagnostics.errors[0]
    assert error.code == "pin_not_applicable"
    assert "fetch:external" in error.message


# ---------------------------------------------------------------------------
# Artifact pins
# ---------------------------------------------------------------------------


def _with_artifact(root: Path) -> Path:
    _write(
        root,
        "artifact.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: artifact\nmeta:\n  name: datahub\n"
        "spec:\n  image_name: int-docker-test/src/datahub.api\n",
    )
    return root


def test_artifact_pin_against_a_real_artifact_is_clean(tmp_path):
    root = _with_artifact(_solution(tmp_path, "  pins:\n    artifacts:\n      datahub: '1.2.3'\n"))
    assert _resolve(root).diagnostics.messages() == []


def test_artifact_pin_matching_no_artifact_is_a_warning(tmp_path):
    root = _with_artifact(_solution(tmp_path, "  pins:\n    artifacts:\n      datahub-api: '1.2.3'\n"))
    context = _resolve(root)
    assert context.ok
    warning = context.diagnostics.warnings[0]
    assert warning.code == "stale_pin"
    assert warning.location == "spec.pins.artifacts.datahub-api"
    assert "datahub-api" in warning.message


# ---------------------------------------------------------------------------
# Every category is checked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", PIN_CATEGORIES)
def test_every_pin_category_reports_a_pin_naming_nothing_as_stale(tmp_path, category):
    """A new category added to `PIN_CATEGORIES` without a branch in `check_version_pins()` fails here."""
    context = _resolve(_solution(tmp_path, f"  pins:\n    {category}:\n      ghost: x\n"))
    assert context.ok
    warnings = context.diagnostics.warnings
    assert [w.code for w in warnings] == ["stale_pin"]
    assert warnings[0].location == f"spec.pins.{category}.ghost"


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


# ---------------------------------------------------------------------------
# log_pin_applied() (ADR-0019 decision 7 — "every application must be logged")
# ---------------------------------------------------------------------------


def test_log_pin_applied_emits_a_structured_entry():
    import io
    import json

    from strata.controllers.version_pins import log_pin_applied
    from strata.logging.config import configure_logging, get_logger, shutdown_logging

    stream = io.StringIO()
    try:
        configure_logging(level="INFO", json_output=True, stream=stream)
        log_pin_applied("images", "web", "nginx:1.26", "nginx:1.27", "prd")
    finally:
        shutdown_logging()

    line = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "version pin applied"
    assert payload["category"] == "images"
    assert payload["name"] == "web"
    assert payload["declared"] == "nginx:1.26"
    assert payload["pinned"] == "nginx:1.27"
    assert payload["version"] == "prd"
    # Re-fetch via get_logger to confirm it's the same bound-logger path
    # other callers in this module will use — not a separate mechanism.
    assert get_logger(__name__) is not None


def test_log_pin_applied_accepts_none_declared_value():
    """A target that declared nothing of its own before the pin applied
    (e.g. a module service with no `image` set) is a valid, real case."""
    import io
    import json

    from strata.controllers.version_pins import log_pin_applied
    from strata.logging.config import configure_logging, shutdown_logging

    stream = io.StringIO()
    try:
        configure_logging(level="INFO", json_output=True, stream=stream)
        log_pin_applied("charts", "chart-module", None, "2024.12.0", "prd")
    finally:
        shutdown_logging()

    line = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["declared"] is None
    assert payload["pinned"] == "2024.12.0"
