"""Unit tests for HelmDeployer."""

from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_OUTPUT,
    STEP_PLAN,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
    STEP_SHOW_PLAN,
)
from strata.deployers.helm_deployer import HelmDeployer, HelmModuleTarget, _sanitize_repo_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deployer(
    tmp_path: Optional[Path] = None,
    force: bool = False,
    verbose: bool = False,
) -> HelmDeployer:
    """Build a HelmDeployer backed by mock services."""
    stage = MagicMock()
    deployment_service = MagicMock()
    configuration_service = MagicMock()
    build_path = (tmp_path / "build") if tmp_path else Path("/build")
    work_path = tmp_path or Path("/work")
    return HelmDeployer(
        stage=stage,
        deployment_service=deployment_service,
        configuration_service=configuration_service,
        build_path=build_path,
        work_path=work_path,
        verbose=verbose,
        force=force,
    )


def _make_target(
    tmp_path: Optional[Path] = None,
    ns_name: str = "prod",
    module_name: str = "nginx",
    chart_ref: str = "myrepo/nginx",
    chart_version: Optional[str] = None,
    repo_url: Optional[str] = None,
    repo_name: Optional[str] = None,
) -> HelmModuleTarget:
    """Create a HelmModuleTarget with the given parameters."""
    base = tmp_path or Path("/build")
    values_file = base / ns_name / module_name / "values.yaml"
    meta_file = base / ns_name / module_name / "meta.yaml"
    return HelmModuleTarget(
        ns_name=ns_name,
        module_name=module_name,
        values_file=values_file,
        meta_file=meta_file,
        release_name=module_name,
        chart_namespace=ns_name,
        chart_ref=chart_ref,
        chart_version=chart_version,
        repo_url=repo_url,
        repo_name=repo_name,
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestHelmDeployerMetadata:
    def test_deployer_name_is_helm(self):
        d = _make_deployer()
        assert d.get_deployer_name() == "helm"

    def test_get_supported_steps_contains_all_eight(self):
        d = _make_deployer()
        steps = d.get_supported_steps()
        assert STEP_SETUP in steps
        assert STEP_CHECK in steps
        assert STEP_PLAN in steps
        assert STEP_APPLY in steps
        assert STEP_DESTROY in steps
        assert STEP_PLAN_DESTROY in steps
        assert STEP_SHOW_PLAN in steps
        assert STEP_OUTPUT in steps


# ---------------------------------------------------------------------------
# validate_environment
# ---------------------------------------------------------------------------


class TestHelmDeployerValidateEnvironment:
    def test_success_sets_helm_instance(self):
        d = _make_deployer()
        with patch("strata.deployers.helm_deployer.HelmIntegration") as mock_int:
            instance = MagicMock()
            instance.ensure_available.return_value = (True, "")
            instance.get_version.return_value = "3.14.0"
            mock_int.return_value = instance
            ok, msgs = d.validate_environment()
        assert ok is True
        assert d._helm is instance

    def test_unavailable_returns_false(self):
        d = _make_deployer()
        with patch("strata.deployers.helm_deployer.HelmIntegration") as mock_int:
            instance = MagicMock()
            instance.ensure_available.return_value = (False, "helm not in PATH")
            mock_int.return_value = instance
            ok, msgs = d.validate_environment()
        assert ok is False
        assert any("helm not in PATH" in m for m in msgs)


# ---------------------------------------------------------------------------
# Steps require init (_helm must be set)
# ---------------------------------------------------------------------------


class TestHelmDeployerStepsRequireInit:
    """All steps guard via _ready() — they must fail when _helm is None."""

    def test_setup_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.setup()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_check_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.check()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_plan_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.plan()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_apply_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.apply()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_destroy_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.destroy()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_plan_destroy_requires_init(self):
        d = _make_deployer()
        ok, msgs = d.plan_destroy()
        assert ok is False
        assert any("not initialized" in m for m in msgs)

    def test_output_requires_init(self):
        d = _make_deployer()
        ok, data, msgs = d.output()
        assert ok is False
        assert any("not initialized" in m for m in msgs)


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


class TestHelmDeployerSetup:
    def test_no_registry_sources_skips_repo_update(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = [_make_target(repo_url=None, repo_name=None)]
        ok, msgs = d.setup()
        assert ok is True
        assert any("No chart registries to update" in m for m in msgs)
        d._helm._run_integration.assert_not_called()

    def test_registry_source_calls_repo_add_and_update(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._helm_modules = [
            _make_target(
                repo_url="https://charts.example.com",
                repo_name="charts-example-com",
            )
        ]
        ok, msgs = d.setup()
        assert ok is True
        all_args = [c[0][0] for c in d._helm._run_integration.call_args_list]
        assert any("add" in args for args in all_args)
        assert any("update" in args for args in all_args)

    def test_deduplicates_repos(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        # Two targets referencing the same repository
        d._helm_modules = [
            _make_target(
                ns_name="prod",
                module_name="nginx",
                repo_url="https://charts.example.com",
                repo_name="charts-example-com",
            ),
            _make_target(
                ns_name="prod",
                module_name="redis",
                repo_url="https://charts.example.com",
                repo_name="charts-example-com",
            ),
        ]
        d.setup()
        # repo add should be called exactly once (deduplicated)
        add_calls = [c for c in d._helm._run_integration.call_args_list if "add" in c[0][0]]
        assert len(add_calls) == 1


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


class TestHelmDeployerCheck:
    def test_no_modules_returns_true_with_message(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.check()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_local_chart_calls_helm_lint(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._helm_modules = [_make_target(repo_url=None)]
        ok, msgs = d.check()
        assert ok is True
        all_args = [c[0][0] for c in d._helm._run_integration.call_args_list]
        assert any("lint" in args for args in all_args)

    def test_registry_chart_skips_lint(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = [_make_target(repo_url="https://charts.example.com", repo_name="charts-example-com")]
        ok, msgs = d.check()
        assert ok is True
        d._helm._run_integration.assert_not_called()
        assert any("skipped" in m for m in msgs)

    def test_lint_failure_returns_false(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="lint error: invalid chart")
        d._helm_modules = [_make_target(repo_url=None)]
        ok, msgs = d.check()
        assert ok is False


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


class TestHelmDeployerPlan:
    def test_no_modules_returns_true(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.plan()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_dry_run_command_constructed_correctly(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        target = _make_target(ns_name="staging", module_name="app")
        d._helm_modules = [target]
        ok, msgs = d.plan()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "upgrade" in args
        assert "--dry-run" in args
        assert "--install" in args
        assert "--namespace" in args
        assert target.chart_namespace in args

    def test_chart_version_included_when_set(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._helm_modules = [_make_target(chart_version="1.2.3")]
        d.plan()
        args = d._helm._run_integration.call_args[0][0]
        assert "--version" in args
        assert "1.2.3" in args

    def test_dry_run_failure_returns_false(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="chart not found")
        d._helm_modules = [_make_target()]
        ok, msgs = d.plan()
        assert ok is False


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class TestHelmDeployerApply:
    def test_no_modules_returns_true(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.apply()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_install_command_constructed_correctly(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        target = _make_target(ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, msgs = d.apply()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "upgrade" in args
        assert "--install" in args
        assert "--create-namespace" in args
        assert "--namespace" in args
        assert target.chart_namespace in args

    def test_install_failure_returns_false(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="connection refused")
        d._helm_modules = [_make_target()]
        ok, msgs = d.apply()
        assert ok is False

    def test_chart_version_included_when_set(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        d._helm_modules = [_make_target(chart_version="2.0.0")]
        d.apply()
        args = d._helm._run_integration.call_args[0][0]
        assert "--version" in args
        assert "2.0.0" in args


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------


class TestHelmDeployerDestroy:
    def test_destroy_requires_force(self):
        d = _make_deployer(force=False)
        d._helm = MagicMock()
        d._helm_modules = [_make_target()]
        ok, msgs = d.destroy()
        assert ok is False
        assert any("--force" in m for m in msgs)

    def test_no_modules_returns_true(self):
        d = _make_deployer(force=True)
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.destroy()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_uninstall_command_constructed_correctly(self):
        d = _make_deployer(force=True)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="", stderr="")
        target = _make_target(ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, msgs = d.destroy()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "uninstall" in args
        assert "--namespace" in args
        assert target.chart_namespace in args

    def test_uninstall_failure_returns_false(self):
        d = _make_deployer(force=True)
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="release not found")
        d._helm_modules = [_make_target()]
        ok, msgs = d.destroy()
        assert ok is False


# ---------------------------------------------------------------------------
# plan_destroy
# ---------------------------------------------------------------------------


class TestHelmDeployerPlanDestroy:
    def test_no_modules_returns_true(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, msgs = d.plan_destroy()
        assert ok is True
        assert any("No helm modules" in m for m in msgs)

    def test_get_manifest_called_per_module(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="apiVersion: v1", stderr="")
        target = _make_target(ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, msgs = d.plan_destroy()
        assert ok is True
        args = d._helm._run_integration.call_args[0][0]
        assert "get" in args
        assert "manifest" in args
        assert "--namespace" in args
        assert target.chart_namespace in args

    def test_missing_release_treated_as_info_only(self):
        """returncode=1 (release not installed) is not an error — plan_destroy still succeeds."""
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="release: not found")
        d._helm_modules = [_make_target()]
        ok, msgs = d.plan_destroy()
        assert ok is True
        assert any("not installed" in m for m in msgs)


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


class TestHelmDeployerOutput:
    def test_no_modules_returns_empty_dict(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm_modules = []
        ok, data, msgs = d.output()
        assert ok is True
        assert data == {}

    def test_get_values_called_per_module(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=0, stdout="replicaCount: 2\n", stderr="")
        target = _make_target(ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, data, msgs = d.output()
        assert ok is True
        key = f"{target.ns_name}/{target.module_name}"
        assert key in data
        assert data[key] == {"replicaCount": 2}

    def test_failed_get_values_returns_empty_dict_for_module(self):
        d = _make_deployer()
        d._helm = MagicMock()
        d._helm._run_integration.return_value = MagicMock(returncode=1, stdout="", stderr="release: not found")
        target = _make_target(ns_name="prod", module_name="nginx")
        d._helm_modules = [target]
        ok, data, msgs = d.output()
        assert ok is True
        key = f"{target.ns_name}/{target.module_name}"
        assert data[key] == {}


# ---------------------------------------------------------------------------
# show_plan
# ---------------------------------------------------------------------------


class TestHelmDeployerShowPlan:
    def test_always_returns_empty_dict(self):
        d = _make_deployer()
        ok, data, msgs = d.show_plan()
        assert ok is True
        assert data == {}
        assert msgs == []


# ---------------------------------------------------------------------------
# _sanitize_repo_name
# ---------------------------------------------------------------------------


class TestSanitizeRepoName:
    def test_strips_https_scheme(self):
        result = _sanitize_repo_name("https://charts.example.com")
        assert result.startswith("charts")

    def test_strips_http_scheme(self):
        result = _sanitize_repo_name("http://charts.example.com")
        assert result.startswith("charts")

    def test_replaces_dots_with_dashes(self):
        result = _sanitize_repo_name("https://charts.example.com")
        assert "." not in result
        assert "charts-example-com" in result

    def test_truncates_to_20_chars(self):
        result = _sanitize_repo_name("https://long-charts-repository-name.example.com")
        assert len(result) <= 20

    def test_no_leading_trailing_dashes(self):
        result = _sanitize_repo_name("https://charts.example.com")
        assert not result.startswith("-")
        assert not result.endswith("-")
