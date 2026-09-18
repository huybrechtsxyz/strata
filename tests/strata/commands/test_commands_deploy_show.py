"""Tests for `deploy show` — the preview surface for `deploy run`.

`deploy show` answers "what would `deploy run` do?", so it must speak the same
vocabulary: the same stage selection (`--stage`/`--scope`), the same secret
scoping, and — per ADR-0083 D11 — disclosure of which stages a run would skip.
It must never *filter* disabled stages out; that would make "disabled in this
environment" indistinguishable from "deleted from the deployment", the exact
confusion stage gating exists to remove.

This module had no predecessor: the command shipped untested, which is how
`--stage` came to be documented for behaviour it never had.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_deploy import deploy
from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.commands.deploy.show_deploy_command import ShowDeployCommand
from strata.utils.resolved_values import ResolvedValues


def _make_show_command(tmp_path: Path, stage=None, scope=None) -> ShowDeployCommand:
    """Build the command without touching the filesystem or any service.

    `_initialize` is what loads the workspace, so patching it out is what makes a
    unit test of `_collect()` possible at all.
    """
    with patch.object(BaseDeployCommand, "_initialize", return_value=None):
        cmd = ShowDeployCommand(work_path=str(tmp_path), file="deploy.yaml", stage=stage, scope=scope)
    cmd._work_path = tmp_path
    cmd._file_path = tmp_path / "deploy.yaml"
    cmd._environment_snapshot = None
    return cmd


def _make_stage(name, *, provisioner="terraform", scope=None, depends_on=None, enabled=None, secrets=None):
    """A stage mock with every Optional field set explicitly.

    An unset MagicMock attribute is truthy *and* iterates empty, so leaving
    `enabled`/`depends_on`/`secrets` unset would silently exercise the wrong
    branch rather than fail.
    """
    stage = MagicMock()
    stage.name = name
    stage.provisioner = provisioner
    stage.scope = scope
    stage.depends_on = depends_on
    stage.enabled = enabled
    stage.secrets = secrets
    return stage


def _make_item(key, store="environment"):
    item = MagicMock()
    item.key = key
    item.store.value = store
    return item


def _make_env_service(*, variables=(), secrets=(), features=(), name="prd"):
    env_service = MagicMock()
    env_service.get_name.return_value = name
    env_service.path = f"/env/{name}.yaml"
    env_service.get_variables.return_value = [_make_item(k) for k in variables]
    env_service.get_secrets.return_value = [_make_item(k) for k in secrets]
    env_service.get_features.return_value = [_make_item(k) for k in features]

    model = MagicMock()
    model.meta.name = name
    model.meta.labels = None
    model.meta.annotations = None
    model.spec.properties = None
    model.spec.custom = None
    model.spec.overrides = None
    env_service.model = model
    return env_service


def _wire(cmd, *, stages=(), env_service=None, remotes=(), resolved=None):
    """Attach the mocked services `_collect()` reads, and stub value resolution."""
    deployment_service = MagicMock()
    deployment_service.get_name.return_value = "demo"
    deployment_service.model.spec.stages = list(stages)
    deployment_service.get_environment_service.return_value = env_service
    cmd._deployment_service = deployment_service

    configuration_service = MagicMock()
    configuration_service.model.spec.remotes = list(remotes)
    cmd._configuration_service = configuration_service

    cmd._resolve_values = MagicMock(return_value=(True, resolved or ResolvedValues(), []))
    return cmd


# ---------------------------------------------------------------------------
# Step 1 — baseline: the shape `_collect()` produces, and resolving exactly once
# ---------------------------------------------------------------------------


class TestCollectBaseline:
    """Characterisation. These assertions predate the hoist and must survive it."""

    def test_output_keys(self, tmp_path):
        cmd = _wire(_make_show_command(tmp_path), stages=[_make_stage("core")], env_service=_make_env_service())
        cmd._collect()

        assert set(cmd._output_data) == {
            "file",
            "deployment",
            "workspace",
            "environment",
            "environment_file",
            "remotes",
            "stages",
            "environment_detail",
        }

    def test_stage_row_shape(self, tmp_path):
        stage = _make_stage("core", provisioner="platform_iac", scope="infra", depends_on=["base"])
        cmd = _wire(_make_show_command(tmp_path), stages=[stage], env_service=_make_env_service())
        cmd._collect()

        row = cmd._output_data["stages"][0]
        assert row["name"] == "core"
        assert row["provisioner"] == "platform_iac"
        assert row["scope"] == "infra"
        assert row["depends_on"] == ["base"]

    def test_provisioner_defaults_to_terraform(self, tmp_path):
        stage = _make_stage("core", provisioner=None)
        cmd = _wire(_make_show_command(tmp_path), stages=[stage], env_service=_make_env_service())
        cmd._collect()

        assert cmd._output_data["stages"][0]["provisioner"] == "terraform"

    def test_depends_on_omitted_when_absent(self, tmp_path):
        cmd = _wire(_make_show_command(tmp_path), stages=[_make_stage("core")], env_service=_make_env_service())
        cmd._collect()

        assert "depends_on" not in cmd._output_data["stages"][0]

    def test_environment_detail_value_rows(self, tmp_path):
        env_service = _make_env_service(variables=["region"], secrets=["token"], features=["beta"])
        resolved = ResolvedValues(
            variables={"region": "eu-west"},
            secrets={"token": "s3cr3tvalue"},
            features={"beta": True},
        )
        cmd = _wire(_make_show_command(tmp_path), env_service=env_service, resolved=resolved)
        ok = cmd._collect()

        detail = cmd._output_data["environment_detail"]
        assert ok is True
        assert detail["variables"] == [{"key": "region", "value": "eu-west", "store": "environment", "resolved": True}]
        assert detail["features"] == [{"key": "beta", "value": True, "store": "environment", "resolved": True}]
        secret_row = detail["secrets"][0]
        assert secret_row["key"] == "token"
        assert secret_row["value"].startswith("s3cr")
        assert "s3cr3tvalue" not in secret_row["value"]

    def test_unresolved_value_reports_failure(self, tmp_path):
        env_service = _make_env_service(variables=["missing"])
        cmd = _wire(_make_show_command(tmp_path), env_service=env_service)
        ok = cmd._collect()

        assert ok is False
        assert any("could not be resolved" in e for e in cmd._errors)

    def test_no_environment_service(self, tmp_path):
        cmd = _wire(_make_show_command(tmp_path), env_service=None)
        ok = cmd._collect()

        assert ok is True
        assert cmd._output_data["environment_detail"] is None

    def test_remote_rows(self, tmp_path):
        remote = MagicMock()
        remote.name = "haven"
        remote.reference = "v1.2.3"
        cmd = _wire(_make_show_command(tmp_path), env_service=_make_env_service(), remotes=[remote])
        cmd._collect()

        assert cmd._output_data["remotes"] == [{"name": "haven", "reference": "v1.2.3", "source": "workspace default"}]

    def test_services_not_loaded(self, tmp_path):
        cmd = _make_show_command(tmp_path)
        cmd._deployment_service = None
        cmd._configuration_service = None

        assert cmd._collect() is False
        assert "Deployment service not loaded" in cmd._errors

    def test_configuration_service_missing_still_guarded(self, tmp_path):
        """`_resolve_stages` covers `_deployment_service` only — the remotes section
        needs `_configuration_service`, so its guard cannot be dropped."""
        cmd = _make_show_command(tmp_path)
        cmd._deployment_service = MagicMock()
        cmd._configuration_service = None

        assert cmd._collect() is False


class TestResolveValuesHoisted:
    """The whole point of the hoist: one resolution pass, not two."""

    def test_resolved_exactly_once(self, tmp_path):
        env_service = _make_env_service(variables=["region"], secrets=["token"])
        resolved = ResolvedValues(variables={"region": "eu"}, secrets={"token": "abcd"})
        cmd = _wire(
            _make_show_command(tmp_path),
            stages=[_make_stage("core")],
            env_service=env_service,
            resolved=resolved,
        )
        cmd._collect()

        cmd._resolve_values.assert_called_once_with(strict=False)

    def test_no_workspace_service_loaded(self, tmp_path):
        """ADR-0026 keeps `show` environment-only. Disclosure must not change that."""
        cmd = _wire(_make_show_command(tmp_path), stages=[_make_stage("core")], env_service=_make_env_service())
        cmd._collect()

        cmd._deployment_service.get_workspace_service.assert_not_called()


# ---------------------------------------------------------------------------
# Step 2 — disclosure: which stages a run would skip
# ---------------------------------------------------------------------------


class TestGatingDisclosure:
    def test_enabled_stage_is_not_marked(self, tmp_path):
        cmd = _wire(_make_show_command(tmp_path), stages=[_make_stage("core")], env_service=_make_env_service())
        cmd._collect()

        row = cmd._output_data["stages"][0]
        assert row["would_skip"] is False
        assert row["skip_reason"] is None

    def test_literal_false_is_disclosed(self, tmp_path):
        stage = _make_stage("api", enabled=False)
        cmd = _wire(_make_show_command(tmp_path), stages=[stage], env_service=_make_env_service())
        cmd._collect()

        row = cmd._output_data["stages"][0]
        assert row["enabled"] is False
        assert row["would_skip"] is True
        assert "'enabled' is false" in row["skip_reason"]

    def test_expression_skip_reason_names_the_flag(self, tmp_path):
        """A reason that says only "skipped" relocates the confusion one level down."""
        stage = _make_stage("api", enabled="${feature:enable_api}")
        resolved = ResolvedValues(features={"enable_api": False})
        cmd = _wire(
            _make_show_command(tmp_path),
            stages=[stage],
            env_service=_make_env_service(),
            resolved=resolved,
        )
        cmd._collect()

        reason = cmd._output_data["stages"][0]["skip_reason"]
        assert "${feature:enable_api}" in reason
        assert "false" in reason

    def test_expression_true_is_not_marked(self, tmp_path):
        stage = _make_stage("api", enabled="${feature:enable_api}")
        resolved = ResolvedValues(features={"enable_api": True})
        cmd = _wire(
            _make_show_command(tmp_path),
            stages=[stage],
            env_service=_make_env_service(),
            resolved=resolved,
        )
        cmd._collect()

        assert cmd._output_data["stages"][0]["would_skip"] is False

    def test_disabled_stage_is_still_listed(self, tmp_path):
        """ADR-0083 D11: a read-only surface discloses gating, it never filters on it."""
        stages = [_make_stage("core"), _make_stage("api", enabled=False)]
        cmd = _wire(_make_show_command(tmp_path), stages=stages, env_service=_make_env_service())
        cmd._collect()

        assert [r["name"] for r in cmd._output_data["stages"]] == ["core", "api"]

    def test_console_marks_the_disabled_stage(self, tmp_path):
        stage = _make_stage("api", enabled="${feature:enable_api}")
        resolved = ResolvedValues(features={"enable_api": False})
        cmd = _wire(
            _make_show_command(tmp_path),
            stages=[stage, _make_stage("core")],
            env_service=_make_env_service(),
            resolved=resolved,
        )
        cmd._collect()

        with patch("click.echo") as echo:
            cmd._print_environment_detail()
        lines = [str(call.args[0]) if call.args else "" for call in echo.call_args_list]

        api_line = next(line for line in lines if "• api" in line)
        core_line = next(line for line in lines if "• core" in line)
        assert "enable_api" in api_line
        assert "would skip" in api_line.lower()
        assert "skip" not in core_line.lower()


# ---------------------------------------------------------------------------
# Step 3 — selection: --stage/--scope mean what they mean everywhere else
# ---------------------------------------------------------------------------


class TestStageSelection:
    def test_stage_scopes_the_stage_list(self, tmp_path):
        stages = [_make_stage("core"), _make_stage("api")]
        cmd = _wire(_make_show_command(tmp_path, stage="api"), stages=stages, env_service=_make_env_service())
        cmd._collect()

        assert [r["name"] for r in cmd._output_data["stages"]] == ["api"]

    def test_scope_filters_like_run(self, tmp_path):
        stages = [_make_stage("core", scope="infra"), _make_stage("api", scope="app")]
        cmd = _wire(_make_show_command(tmp_path, scope="app"), stages=stages, env_service=_make_env_service())
        cmd._collect()

        assert [r["name"] for r in cmd._output_data["stages"]] == ["api"]

    def test_neither_flag_shows_every_stage(self, tmp_path):
        stages = [_make_stage("core"), _make_stage("api")]
        cmd = _wire(_make_show_command(tmp_path), stages=stages, env_service=_make_env_service())
        cmd._collect()

        assert [r["name"] for r in cmd._output_data["stages"]] == ["core", "api"]

    def test_unknown_stage_errors(self, tmp_path):
        """Silently showing everything would be the worst answer to a typo."""
        cmd = _wire(
            _make_show_command(tmp_path, stage="nope"),
            stages=[_make_stage("core")],
            env_service=_make_env_service(),
        )

        assert cmd._collect() is False
        assert any("nope" in e for e in cmd._errors)

    def test_selecting_a_disabled_stage_still_shows_it(self, tmp_path):
        """INSPECT mode is load-bearing: DEPLOY would filter this row out and turn
        the Step 2 marker into dead code."""
        stages = [_make_stage("core"), _make_stage("api", enabled=False)]
        cmd = _wire(_make_show_command(tmp_path, stage="api"), stages=stages, env_service=_make_env_service())
        cmd._collect()

        rows = cmd._output_data["stages"]
        assert [r["name"] for r in rows] == ["api"]
        assert rows[0]["would_skip"] is True


class TestSecretScoping:
    def test_stage_scopes_secrets_to_its_allowlist(self, tmp_path):
        stages = [
            _make_stage("core", secrets=["core_token"]),
            _make_stage("api", secrets=["api_token"]),
        ]
        env_service = _make_env_service(secrets=["core_token", "api_token"])
        resolved = ResolvedValues(secrets={"core_token": "aaaa1111", "api_token": "bbbb2222"})
        cmd = _wire(
            _make_show_command(tmp_path, stage="api"),
            stages=stages,
            env_service=env_service,
            resolved=resolved,
        )
        cmd._collect()

        rows = {r["key"]: r for r in cmd._output_data["environment_detail"]["secrets"]}
        assert rows["api_token"]["in_scope"] is True
        assert rows["api_token"]["value"] is not None
        assert rows["core_token"]["in_scope"] is False

    def test_out_of_scope_secret_is_listed_not_omitted(self, tmp_path):
        """A vanished secret is indistinguishable from one that was never declared —
        the same "excluded vs. absent" confusion ADR-0083 exists to remove."""
        stages = [
            _make_stage("core", secrets=["core_token"]),
            _make_stage("api", secrets=["api_token"]),
        ]
        env_service = _make_env_service(secrets=["core_token", "api_token"])
        resolved = ResolvedValues(secrets={"core_token": "aaaa1111", "api_token": "bbbb2222"})
        cmd = _wire(
            _make_show_command(tmp_path, stage="api"),
            stages=stages,
            env_service=env_service,
            resolved=resolved,
        )
        cmd._collect()

        rows = {r["key"]: r for r in cmd._output_data["environment_detail"]["secrets"]}
        assert set(rows) == {"core_token", "api_token"}
        assert rows["core_token"]["value"] is None
        assert rows["core_token"]["resolved"] is True
        assert rows["core_token"]["store"] == "environment"

    def test_wildcard_allowlist_shows_every_secret(self, tmp_path):
        stages = [_make_stage("api", secrets=["*"])]
        env_service = _make_env_service(secrets=["core_token", "api_token"])
        resolved = ResolvedValues(secrets={"core_token": "aaaa1111", "api_token": "bbbb2222"})
        cmd = _wire(
            _make_show_command(tmp_path, stage="api"),
            stages=stages,
            env_service=env_service,
            resolved=resolved,
        )
        cmd._collect()

        rows = cmd._output_data["environment_detail"]["secrets"]
        assert all(r["in_scope"] for r in rows)

    def test_unscoped_view_shows_every_secret(self, tmp_path):
        stages = [
            _make_stage("core", secrets=["core_token"]),
            _make_stage("api", secrets=["api_token"]),
        ]
        env_service = _make_env_service(secrets=["core_token", "api_token"])
        resolved = ResolvedValues(secrets={"core_token": "aaaa1111", "api_token": "bbbb2222"})
        cmd = _wire(_make_show_command(tmp_path), stages=stages, env_service=env_service, resolved=resolved)
        cmd._collect()

        rows = cmd._output_data["environment_detail"]["secrets"]
        assert all(r["in_scope"] for r in rows)
        assert all(r["value"] is not None for r in rows)

    def test_variables_and_features_are_never_scoped(self, tmp_path):
        """Mirrors the deploy-time contract: only secrets carry a per-stage allowlist
        (`ResolvedValues.for_stage()`); variables and features are injected whole."""
        stages = [_make_stage("core", secrets=["core_token"]), _make_stage("api", secrets=["api_token"])]
        env_service = _make_env_service(variables=["region"], secrets=["core_token"], features=["beta"])
        resolved = ResolvedValues(
            variables={"region": "eu-west"},
            secrets={"core_token": "aaaa1111"},
            features={"beta": True},
        )
        cmd = _wire(
            _make_show_command(tmp_path, stage="api"),
            stages=stages,
            env_service=env_service,
            resolved=resolved,
        )
        cmd._collect()

        detail = cmd._output_data["environment_detail"]
        assert detail["variables"][0]["value"] == "eu-west"
        assert detail["features"][0]["value"] is True

    def test_out_of_scope_secret_does_not_fail_the_command(self, tmp_path):
        """Out of scope is not unresolved — it must not be counted as a failure."""
        stages = [_make_stage("core", secrets=["core_token"]), _make_stage("api", secrets=["api_token"])]
        env_service = _make_env_service(secrets=["core_token", "api_token"])
        resolved = ResolvedValues(secrets={"core_token": "aaaa1111", "api_token": "bbbb2222"})
        cmd = _wire(
            _make_show_command(tmp_path, stage="api"),
            stages=stages,
            env_service=env_service,
            resolved=resolved,
        )

        assert cmd._collect() is True


# ---------------------------------------------------------------------------
# Step 4 — CLI wiring
# ---------------------------------------------------------------------------


class TestDeployShowCli:
    """Flags parse and reach the constructor. Output content is asserted against
    `_collect()` above — driving this command end-to-end would need a full
    workspace/profile/environment fixture, which no deploy-command test has."""

    def test_show_basic(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.show_deploy_command.ShowDeployCommand.execute", return_value=True):
            result = runner.invoke(deploy, ["show", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_stage_flag_reaches_the_command(self, tmp_path):
        runner = CliRunner()
        with patch.object(ShowDeployCommand, "__init__", return_value=None) as init:
            with patch.object(ShowDeployCommand, "execute", return_value=True):
                with patch("strata.commands.cli_deploy.handle_command_exit"):
                    runner.invoke(deploy, ["show", "--stage", "api", "--work-path", str(tmp_path)])
        assert init.call_args.kwargs["stage"] == "api"

    def test_scope_flag_reaches_the_command(self, tmp_path):
        runner = CliRunner()
        with patch.object(ShowDeployCommand, "__init__", return_value=None) as init:
            with patch.object(ShowDeployCommand, "execute", return_value=True):
                with patch("strata.commands.cli_deploy.handle_command_exit"):
                    runner.invoke(deploy, ["show", "--scope", "app", "--work-path", str(tmp_path)])
        assert init.call_args.kwargs["scope"] == "app"

    def test_neither_flag_passes_none(self, tmp_path):
        runner = CliRunner()
        with patch.object(ShowDeployCommand, "__init__", return_value=None) as init:
            with patch.object(ShowDeployCommand, "execute", return_value=True):
                with patch("strata.commands.cli_deploy.handle_command_exit"):
                    runner.invoke(deploy, ["show", "--work-path", str(tmp_path)])
        assert init.call_args.kwargs["stage"] is None
        assert init.call_args.kwargs["scope"] is None

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.deploy.show_deploy_command.ShowDeployCommand.execute", return_value=False):
            result = runner.invoke(deploy, ["show", "--work-path", str(tmp_path)])
        assert result.exit_code != 0
