"""Tests for PromoteController.run_start() (ADR-0072 layer resolution wiring).

``run_start()`` previously had zero dedicated test coverage. These tests focus on
the new behavior: ``_load_registered_deployments()`` now validates each
deployment's ``spec.layers`` against ``configuration.spec.paths`` (ADR-0072) via
``DeploymentService.validate_layers()``, turning a resolution failure (e.g. an
unknown ``follows`` name) into a hard ``self._add_error()`` that excludes the
offending deployment from the promotion set, rather than only ever surfacing as
an advisory message.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from strata.controllers.promote_controller import PromoteController

_CONFIG_YAML = """\
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test-config
spec:
  paths:
    - name: hub-scheme
      scope: "deploy/hubs/*"
      pattern: "deploy/hubs/{hub}/{ring}"
      resolves: layers
      segments:
        - name: hub
        - name: ring
  promotions:
    progressions:
      - name: standard
        rings:
          - name: dev
            environments: [dev1]
    strategies:
      - name: hub-wave
        type: remote
        progression: standard
        scope: hub
"""

_GOOD_DEPLOYMENT = """\
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: dep1
spec:
  environments:
    - "environments/dev1.yaml"
  layers:
    follows: hub-scheme
"""

_BAD_DEPLOYMENT = """\
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: dep2
spec:
  environments:
    - "environments/dev1.yaml"
  layers:
    follows: nonexistent-scheme
"""


def _make_workspace(tmp_path: Path, *, include_bad_deployment: bool) -> Path:
    wp = tmp_path
    (wp / ".strata").mkdir()
    (wp / ".strata" / "configuration.yaml").write_text(_CONFIG_YAML)

    good_path = wp / "deploy" / "hubs" / "hub1" / "prd" / "deploy.yaml"
    good_path.parent.mkdir(parents=True)
    good_path.write_text(_GOOD_DEPLOYMENT)

    deployments = [{"name": "dep1", "path": "deploy/hubs/hub1/prd/deploy.yaml"}]

    if include_bad_deployment:
        bad_path = wp / "deploy" / "hubs" / "hub1" / "qa" / "deploy.yaml"
        bad_path.parent.mkdir(parents=True)
        bad_path.write_text(_BAD_DEPLOYMENT)
        deployments.append({"name": "dep2", "path": "deploy/hubs/hub1/qa/deploy.yaml"})

    solution = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "solution",
        "meta": {"name": "test-solution"},
        "spec": {
            "solution_id": "abc-00001",
            "repositories": [],
            "profiles": [],
            "deployments": deployments,
        },
    }
    (wp / ".strata" / "solution.json").write_text(json.dumps(solution))
    return wp


class TestRunStartLayerResolution:
    def test_valid_layers_no_errors(self, tmp_path):
        wp = _make_workspace(tmp_path, include_bad_deployment=False)
        ctrl = PromoteController()
        result = ctrl.run_start(
            target_type="remote",
            target_name="iac_core",
            version="v1.0.0",
            to_ring="dev",
            wave=None,
            work_path=wp,
            dry_run=True,
        )
        assert not ctrl.has_errors(), ctrl.get_errors()
        assert result["deployments"] == ["dep1"]

    def test_bad_layers_hard_error_excludes_deployment(self, tmp_path):
        """dep2's unknown spec.layers.follows is now a hard error, not an advisory
        message, and dep2 is excluded from the promotion set entirely."""
        wp = _make_workspace(tmp_path, include_bad_deployment=True)
        ctrl = PromoteController()
        result = ctrl.run_start(
            target_type="remote",
            target_name="iac_core",
            version="v1.0.0",
            to_ring="dev",
            wave=None,
            work_path=wp,
            dry_run=True,
        )
        assert ctrl.has_errors()
        errors = ctrl.get_errors()
        assert any("dep2" in e and "nonexistent-scheme" in e for e in errors)
        # dep1 still promotes fine; dep2 is excluded, not silently included.
        assert result["deployments"] == ["dep1"]

    def test_no_relevant_convention_skips_layer_check_gracefully(self, tmp_path):
        """Without spec.paths at all, and no spec.layers declared on the deployment,
        layer resolution is a genuine pass-through — nothing to validate against,
        matches today's pass-through behavior. (A deployment that *does* declare
        spec.layers.follows against a configuration with no matching convention is
        correctly a hard error — see test_bad_layers_hard_error_excludes_deployment.)
        """
        wp = tmp_path
        (wp / ".strata").mkdir()
        config_no_paths = yaml.safe_load(_CONFIG_YAML)
        del config_no_paths["spec"]["paths"]
        (wp / ".strata" / "configuration.yaml").write_text(yaml.dump(config_no_paths))

        dep_path = wp / "deploy" / "hubs" / "hub1" / "prd" / "deploy.yaml"
        dep_path.parent.mkdir(parents=True)
        dep_path.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: deployment\n"
            "meta:\n"
            "  name: dep1\n"
            "spec:\n"
            "  environments:\n"
            '    - "environments/dev1.yaml"\n'
        )

        solution = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "solution",
            "meta": {"name": "test-solution"},
            "spec": {
                "solution_id": "abc-00001",
                "repositories": [],
                "profiles": [],
                "deployments": [{"name": "dep1", "path": "deploy/hubs/hub1/prd/deploy.yaml"}],
            },
        }
        (wp / ".strata" / "solution.json").write_text(json.dumps(solution))

        ctrl = PromoteController()
        result = ctrl.run_start(
            target_type="remote",
            target_name="iac_core",
            version="v1.0.0",
            to_ring="dev",
            wave=None,
            work_path=wp,
            dry_run=True,
        )
        assert not ctrl.has_errors(), ctrl.get_errors()
        assert result["deployments"] == ["dep1"]
