#!/usr/bin/env python3
"""Tests for `strata promote status`/`apply`/`view` (docs/work/promotion.md
Phases 3/5/6)."""

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
"""

PROVIDER = """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
"""

WORKSPACE = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: dspapi
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
"""


@pytest.fixture
def runner():
    return CliRunner()


def _write(root, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def solution(tmp_path):
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(root, "provider.yaml", PROVIDER)
    _write(root, "workspace.yaml", WORKSPACE)
    return root


def _version(name: str, *, workspace: str = "dspapi", promotion: str | None = None, pins: str = "  pins: {}\n") -> str:
    promotion_yaml = f"  promotion:\n{promotion}\n" if promotion else ""
    return f"""apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: {name}
spec:
  workspace: {workspace}
{promotion_yaml}{pins}"""


def _deployment(name: str, *, version: str, workspace: str = "dspapi") -> str:
    return f"""apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: {name}
spec:
  partial: true
  workspace: {workspace}
  version: {version}
"""


def _status(runner, workspace, solution, *extra):
    return runner.invoke(cli, ["promote", "status", workspace, "--path", str(solution), *extra])


# ---------------------------------------------------------------------------
# Worked example A shape: no tags at all
# ---------------------------------------------------------------------------


def test_untagged_workspace_prints_empty_table_not_an_error(runner, solution):
    _write(solution, "version.yaml", _version("dspapi-prd"))
    _write(solution, "deployment.yaml", _deployment("dep", version="dspapi-prd"))

    result = _status(runner, "dspapi", solution)
    assert result.exit_code == EXIT_SUCCESS
    assert "no tagged version documents" in result.output


def test_workspace_with_no_version_documents_at_all_prints_empty_table(runner, solution):
    result = _status(runner, "dspapi", solution)
    assert result.exit_code == EXIT_SUCCESS
    assert "no tagged version documents" in result.output


# ---------------------------------------------------------------------------
# Worked example B shape: multi-ring, wave divergence
# ---------------------------------------------------------------------------


def _worked_example_b(solution) -> None:
    _write(
        solution,
        "version-dev.yaml",
        _version(
            "dspapi-dev",
            promotion="    ring: dev\n    order: 1",
            pins='  pins:\n    images:\n      dspapi: "1.3.0-rc2"\n',
        ),
    )
    _write(
        solution,
        "version-qas.yaml",
        _version(
            "dspapi-qas",
            promotion="    ring: qas\n    order: 2",
            pins='  pins:\n    images:\n      dspapi: "1.2.0"\n',
        ),
    )
    _write(
        solution,
        "version-prd-canary.yaml",
        _version(
            "dspapi-prd-canary",
            promotion="    ring: prd\n    order: 3\n    wave: canary",
            pins='  pins:\n    images:\n      dspapi: "1.1.0"\n',
        ),
    )
    _write(
        solution,
        "version-prd-general.yaml",
        _version(
            "dspapi-prd",
            promotion="    ring: prd\n    order: 3\n    wave: general",
            pins='  pins:\n    images:\n      dspapi: "1.0.0"\n',
        ),
    )
    _write(solution, "deployment-dev.yaml", _deployment("dev-dep", version="dspapi-dev"))
    _write(solution, "deployment-qas.yaml", _deployment("qas-dep", version="dspapi-qas"))
    _write(solution, "deployment-canary.yaml", _deployment("canary-dep", version="dspapi-prd-canary"))
    _write(solution, "deployment-general.yaml", _deployment("general-dep", version="dspapi-prd"))


def test_multi_ring_wave_divergence_renders_every_row_and_marks_the_behind_one(runner, solution):
    _worked_example_b(solution)

    result = _status(runner, "dspapi", solution)
    assert result.exit_code == EXIT_SUCCESS
    output = result.output
    assert "1.3.0-rc2" in output
    assert "1.2.0" in output
    assert "1.1.0" in output
    assert "1.0.0" in output
    # Only general's row (behind canary's 1.1.0) carries the marker.
    general_line = next(line for line in output.splitlines() if "1.0.0" in line)
    canary_line = next(line for line in output.splitlines() if "1.1.0" in line)
    assert "behind" in general_line
    assert "behind" not in canary_line


def test_multi_ring_json_output_carries_every_row(runner, solution):
    _worked_example_b(solution)

    result = _status(runner, "dspapi", solution, "--output", "json")
    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    assert envelope["data"]["workspace"] == "dspapi"
    rows = envelope["data"]["rows"]
    assert len(rows) == 4

    general_row = next(r for r in rows if r["value"] == "1.0.0")
    assert general_row == {
        "ring": "prd",
        "order": 3,
        "wave": "general",
        "category": "images",
        "target": "dspapi",
        "value": "1.0.0",
        "status": "current",
        "behind": True,
    }

    canary_row = next(r for r in rows if r["value"] == "1.1.0")
    assert canary_row["behind"] is False


# ---------------------------------------------------------------------------
# A tagged ring with no pins declared yet still gets a row
# ---------------------------------------------------------------------------


def test_tagged_ring_with_no_pins_still_produces_a_row(runner, solution):
    _write(solution, "version.yaml", _version("dspapi-prd", promotion="    ring: prd\n    order: 3"))
    _write(solution, "deployment.yaml", _deployment("dep", version="dspapi-prd"))

    result = _status(runner, "dspapi", solution)
    assert result.exit_code == EXIT_SUCCESS
    assert "prd" in result.output
    assert "no tagged version documents" not in result.output


# ---------------------------------------------------------------------------
# Promotion findings are surfaced, never fatal
# ---------------------------------------------------------------------------


def test_promotion_violation_is_shown_but_does_not_fail_the_command(runner, solution):
    _write(solution, "version-a.yaml", _version("dspapi-a", promotion="    ring: prd\n    order: 3"))
    _write(solution, "version-b.yaml", _version("dspapi-b", promotion="    ring: prod\n    order: 3"))
    _write(solution, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(solution, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))

    result = _status(runner, "dspapi", solution)
    assert result.exit_code == EXIT_SUCCESS  # read-only: never fails on a finding
    assert "promotion_ring_order_not_bijective" in result.output or "biject" in result.output


def test_outside_a_solution_exits_usage_failure(runner, tmp_path):
    result = _status(runner, "dspapi", tmp_path)
    assert result.exit_code == EXIT_USAGE


# ---------------------------------------------------------------------------
# promote apply (docs/work/promotion.md Phase 5)
# ---------------------------------------------------------------------------


def _apply(runner, workspace, ring, solution, *extra):
    return runner.invoke(cli, ["promote", "apply", workspace, ring, "--path", str(solution), *extra])


def test_apply_copies_the_preceding_orders_common_pin_into_the_target(runner, solution):
    _worked_example_b(solution)

    result = _apply(runner, "dspapi", "prd", solution, "--wave", "general")

    assert result.exit_code == EXIT_SUCCESS
    assert "'dspapi-qas'" in result.output
    assert "images.dspapi = 1.2.0" in result.output

    written = (solution / "version-prd-general.yaml").read_text(encoding="utf-8")
    assert "version: 1.2.0" in written
    assert "status: current" in written


def test_apply_json_output_reports_every_copied_pin(runner, solution):
    _worked_example_b(solution)

    result = _apply(runner, "dspapi", "prd", solution, "--wave", "general", "--output", "json")

    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    assert envelope["data"]["workspace"] == "dspapi"
    assert envelope["data"]["ring"] == "prd"
    assert envelope["data"]["wave"] == "general"
    assert envelope["data"]["source_version"] == "dspapi-qas"
    assert envelope["data"]["target_version"] == "dspapi-prd"
    assert envelope["data"]["copied"] == [{"category": "images", "target": "dspapi", "value": "1.2.0"}]


def test_apply_requires_wave_for_a_multi_wave_ring(runner, solution):
    _worked_example_b(solution)

    result = _apply(runner, "dspapi", "prd", solution)

    assert result.exit_code == EXIT_USAGE
    assert "more than one wave" in result.output


def test_apply_single_wave_ring_needs_no_wave_flag(runner, solution):
    _worked_example_b(solution)

    result = _apply(runner, "dspapi", "qas", solution)

    assert result.exit_code == EXIT_SUCCESS
    assert "'dspapi-dev'" in result.output


def test_apply_unknown_ring_exits_usage_failure(runner, solution):
    _worked_example_b(solution)

    result = _apply(runner, "dspapi", "ghost", solution)

    assert result.exit_code == EXIT_USAGE
    assert "No ring named 'ghost'" in result.output


def test_apply_with_no_preceding_order_exits_usage_failure(runner, solution):
    _worked_example_b(solution)

    result = _apply(runner, "dspapi", "dev", solution)

    assert result.exit_code == EXIT_USAGE
    assert "no preceding order" in result.output


def test_apply_zero_common_keys_reports_nothing_copied_without_failing(runner, solution):
    _write(solution, "version-a.yaml", _version("dspapi-a", promotion="    ring: dev\n    order: 1"))
    _write(
        solution,
        "version-b.yaml",
        _version(
            "dspapi-b", promotion="    ring: qas\n    order: 2", pins='  pins:\n    charts:\n      other: "1.0.0"\n'
        ),
    )
    _write(solution, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(solution, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))

    result = _apply(runner, "dspapi", "qas", solution)

    assert result.exit_code == EXIT_SUCCESS
    assert "nothing copied" in result.output


def test_apply_refuses_when_workspace_has_an_unresolved_promotion_error(runner, solution):
    _write(solution, "version-a.yaml", _version("dspapi-a", promotion="    ring: prd\n    order: 3"))
    _write(solution, "version-b.yaml", _version("dspapi-b", promotion="    ring: prod\n    order: 3"))
    _write(solution, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(solution, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))

    result = _apply(runner, "dspapi", "prd", solution)

    assert result.exit_code == EXIT_USAGE
    assert "unresolved promotion inconsistencies" in result.output


def test_apply_outside_a_solution_exits_usage_failure(runner, tmp_path):
    result = _apply(runner, "dspapi", "prd", tmp_path)
    assert result.exit_code == EXIT_USAGE


# ---------------------------------------------------------------------------
# promote view (docs/work/promotion.md Phase 6)
# ---------------------------------------------------------------------------


def _view(runner, workspace, solution, *extra):
    return runner.invoke(cli, ["promote", "view", workspace, "--path", str(solution), *extra])


def test_view_console_output_round_trips_into_a_promotion_view(runner, solution):
    from strata.controllers.promotion_controller import PromotionView

    _worked_example_b(solution)

    result = _view(runner, "dspapi", solution, "--quiet")

    assert result.exit_code == EXIT_SUCCESS
    # --quiet suppresses the header/footer chrome but never the result itself.
    payload = yaml.safe_load(result.output)
    view = PromotionView.model_validate(payload)
    assert view.workspace == "dspapi"
    assert [r.ring for r in view.rings] == ["dev", "qas", "prd"]
    assert view.rings[2].waves[0].version.meta.name == "dspapi-prd-canary"


def test_view_json_output_round_trips_into_a_promotion_view(runner, solution):
    from strata.controllers.promotion_controller import PromotionView

    _worked_example_b(solution)

    result = _view(runner, "dspapi", solution, "--output", "json")

    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    view = PromotionView.model_validate(envelope["data"])
    assert view.workspace == "dspapi"
    assert len(view.rings) == 3


def test_view_untagged_workspace_is_an_empty_view_not_an_error(runner, solution):
    _write(solution, "version.yaml", _version("dspapi-prd"))
    _write(solution, "deployment.yaml", _deployment("dep", version="dspapi-prd"))

    result = _view(runner, "dspapi", solution, "--quiet")

    assert result.exit_code == EXIT_SUCCESS
    payload = yaml.safe_load(result.output)
    assert payload == {"workspace": "dspapi", "rings": []}


def test_view_output_path_writes_the_bare_view_to_a_file(runner, solution, tmp_path):
    from strata.controllers.promotion_controller import PromotionView

    _worked_example_b(solution)
    destination = tmp_path / "promotion-view.yaml"

    result = _view(runner, "dspapi", solution, "--output-path", str(destination))

    assert result.exit_code == EXIT_SUCCESS
    assert destination.exists()
    view = PromotionView.model_validate(yaml.safe_load(destination.read_text(encoding="utf-8")))
    assert len(view.rings) == 3
    # stdout stays a small confirmation, not a second copy of the whole view.
    assert str(destination) in result.output


def test_view_output_path_with_json_format_writes_valid_json(runner, solution, tmp_path):
    from strata.controllers.promotion_controller import PromotionView

    _worked_example_b(solution)
    destination = tmp_path / "promotion-view.json"

    result = _view(runner, "dspapi", solution, "--output-path", str(destination), "--output", "json")

    assert result.exit_code == EXIT_SUCCESS
    envelope = json.loads(result.output)
    assert envelope["data"] == {"written_to": str(destination), "rings": 3}
    view = PromotionView.model_validate(json.loads(destination.read_text(encoding="utf-8")))
    assert len(view.rings) == 3


def test_view_promotion_violation_is_shown_but_does_not_fail_the_command(runner, solution):
    _write(solution, "version-a.yaml", _version("dspapi-a", promotion="    ring: prd\n    order: 3"))
    _write(solution, "version-b.yaml", _version("dspapi-b", promotion="    ring: prod\n    order: 3"))
    _write(solution, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(solution, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))

    result = _view(runner, "dspapi", solution)

    assert result.exit_code == EXIT_SUCCESS  # read-only: never fails on a finding
    assert "biject" in result.output


def test_view_outside_a_solution_exits_usage_failure(runner, tmp_path):
    result = _view(runner, "dspapi", tmp_path)
    assert result.exit_code == EXIT_USAGE


def test_view_is_never_called_from_the_audit_trail():
    """Done-when criterion: a grep of audit_run.py confirms this command's
    own machinery is never referenced from there — a point-in-time export
    invoked on purpose, not a compliance artifact strata writes on its
    own initiative."""
    import strata.controllers.audit_run as audit_run_module

    source = Path(audit_run_module.__file__).read_text(encoding="utf-8")
    assert "promotion_controller" not in source
    assert "promote_command" not in source
    assert "build_promotion_view" not in source
