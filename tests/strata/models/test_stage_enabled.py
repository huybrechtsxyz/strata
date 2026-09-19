"""Tests for stage gating schema + validation — ADR-0083 Phase 2.

Phase 2 adds the ``stages[].enabled`` field and its validate-time reference
checks. Nothing gates yet: these tests pin the authoring contract only.
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from strata.models.deployment_model import DeploymentStageModel
from strata.services.deployment_service import DeploymentService


class TestEnabledFieldParsing:
    """The authoring contract for ``stages[].enabled``."""

    def test_omitted_defaults_to_none_meaning_always_run(self):
        stage = DeploymentStageModel(name="s", provisioner="p")

        assert stage.enabled is None

    def test_accepts_literal_booleans(self):
        assert DeploymentStageModel(name="s", provisioner="p", enabled=True).enabled is True
        assert DeploymentStageModel(name="s", provisioner="p", enabled=False).enabled is False

    def test_accepts_expression_string(self):
        stage = DeploymentStageModel(name="s", provisioner="p", enabled="${feature:enable_api}")

        assert stage.enabled == "${feature:enable_api}"

    def test_accepts_literal_string(self):
        assert DeploymentStageModel(name="s", provisioner="p", enabled="false").enabled == "false"

    def test_surrounding_whitespace_is_stripped(self):
        stage = DeploymentStageModel(name="s", provisioner="p", enabled="  ${feature:x}  ")

        assert stage.enabled == "${feature:x}"

    def test_bool_is_not_coerced_to_string(self):
        """Union order must not turn `true` into the string 'True'."""
        stage = DeploymentStageModel(name="s", provisioner="p", enabled=True)

        assert isinstance(stage.enabled, bool)

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_string_is_rejected(self, blank):
        """A blank value would coerce to False and disable the stage by accident."""
        with pytest.raises(ValidationError, match="blank"):
            DeploymentStageModel(name="s", provisioner="p", enabled=blank)

    @pytest.mark.parametrize("bad", [1, 0, 1.5, ["true"], {"a": 1}])
    def test_non_bool_non_string_scalars_are_rejected(self, bad):
        with pytest.raises(ValidationError, match="must be a boolean or an expression string"):
            DeploymentStageModel(name="s", provisioner="p", enabled=bad)


def _service_with_stages(stages, *, variables=(), features=(), env_loaded=True) -> DeploymentService:
    """Build a DeploymentService with just enough shape for the refs check."""
    svc = DeploymentService.__new__(DeploymentService)
    model = MagicMock()
    model.spec.stages = stages
    svc.model = model

    if env_loaded:
        env_service = MagicMock()
        env_service.get_variables.return_value = [MagicMock(key=k) for k in variables]
        env_service.get_features.return_value = [MagicMock(key=k) for k in features]
    else:
        env_service = None
    svc._environment_service = env_service
    svc.get_environment_service = MagicMock(return_value=env_service)  # type: ignore[method-assign]
    return svc


def _refs_errors(svc: DeploymentService):
    """Invoke the check with the arguments _validate_dynamic passes."""
    return svc._validate_stage_enabled_refs(None, None)


def _stage(name: str, enabled=None) -> MagicMock:
    stage = MagicMock()
    stage.name = name
    stage.enabled = enabled
    return stage


class TestValidateStageEnabledRefs:
    def test_no_gated_stages_produces_no_errors(self):
        svc = _service_with_stages([_stage("a"), _stage("b", enabled=True)])

        assert _refs_errors(svc) == []

    def test_no_stages_at_all_produces_no_errors(self):
        svc = _service_with_stages([])

        assert _refs_errors(svc) == []

    def test_declared_feature_reference_passes(self):
        svc = _service_with_stages(
            [_stage("api", enabled="${feature:enable_api}")],
            features=["enable_api"],
        )

        assert _refs_errors(svc) == []

    def test_declared_variable_reference_passes(self):
        svc = _service_with_stages(
            [_stage("api", enabled="${var:tier}")],
            variables=["tier"],
        )

        assert _refs_errors(svc) == []

    def test_literal_string_without_expression_passes(self):
        svc = _service_with_stages([_stage("api", enabled="false")])

        assert _refs_errors(svc) == []

    def test_undeclared_feature_errors_and_names_the_key(self):
        svc = _service_with_stages(
            [_stage("api", enabled="${feature:typo_flag}")],
            features=["enable_api"],
        )

        errors = _refs_errors(svc)

        assert len(errors) == 1
        assert "Stage 'api'" in errors[0]
        assert "typo_flag" in errors[0]
        assert "enable_api" in errors[0], "must list what IS available"

    def test_secret_reference_is_rejected_outright(self):
        """A gate's resolved value is persisted as the manifest skip reason."""
        svc = _service_with_stages(
            [_stage("api", enabled="${secret:db_password}")],
            features=["enable_api"],
        )

        errors = _refs_errors(svc)

        assert len(errors) == 1
        assert "Secrets cannot gate a stage" in errors[0]
        assert "db_password" in errors[0]

    def test_secret_rejected_even_when_declared_keys_are_unavailable(self):
        """The secret rule is unconditional — it is not a 'declared key' check."""
        svc = _service_with_stages(
            [_stage("api", enabled="${secret:db_password}")],
            env_loaded=False,
        )

        errors = _refs_errors(svc)

        assert len(errors) == 1
        assert "Secrets cannot gate a stage" in errors[0]

    def test_undeclared_keys_skipped_when_key_set_is_incomplete(self):
        """A partial key set would yield false 'not declared' errors."""
        svc = _service_with_stages(
            [_stage("api", enabled="${feature:anything}")],
            env_loaded=False,
        )

        assert _refs_errors(svc) == []

    def test_reports_every_offending_stage(self):
        svc = _service_with_stages(
            [
                _stage("a", enabled="${feature:missing_one}"),
                _stage("b", enabled="${var:missing_two}"),
                _stage("c", enabled="${feature:present}"),
            ],
            features=["present"],
        )

        errors = _refs_errors(svc)

        assert len(errors) == 2
        assert any("missing_one" in e for e in errors)
        assert any("missing_two" in e for e in errors)

    def test_multiple_references_in_one_expression_all_checked(self):
        svc = _service_with_stages(
            [_stage("a", enabled="${feature:one}-${var:two}")],
            features=["one"],
        )

        errors = _refs_errors(svc)

        assert len(errors) == 1
        assert "two" in errors[0]


class TestLoadEnvironmentDeclaredKeys:
    """Raw-YAML fallback so the check also runs under a plain `strata validate`."""

    def _service(self, tmp_path, env_files, tenant=None) -> DeploymentService:
        svc = DeploymentService.__new__(DeploymentService)
        model = MagicMock()
        model.spec.tenant = tenant
        model.spec.environments = [MagicMock(file=f) for f in env_files]
        svc.model = model
        svc._environment_service = None
        svc.get_environment_service = MagicMock(return_value=None)  # type: ignore[method-assign]
        svc._merged_repo_map = MagicMock(return_value={})  # type: ignore[method-assign]
        svc._resolve_file_path = MagicMock(side_effect=lambda f, w, r: str(tmp_path / f))  # type: ignore[method-assign]
        return svc

    def _write_env(self, tmp_path, name, variables=(), features=()):
        lines = ["apiVersion: strata.huybrechts.xyz/v1", "kind: environment", "spec:"]
        if variables:
            lines.append("  variables:")
            lines += [f"    - key: {k}\n      store: constant\n      value: x" for k in variables]
        if features:
            lines.append("  features:")
            lines += [f"    - key: {k}\n      store: constant\n      value: true" for k in features]
        (tmp_path / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_collects_variable_and_feature_keys(self, tmp_path):
        self._write_env(tmp_path, "env.yaml", variables=["TIER"], features=["FLAG"])
        svc = self._service(tmp_path, ["env.yaml"])

        assert svc._load_environment_declared_keys(str(tmp_path), None) == {"TIER", "FLAG"}

    def test_unions_keys_across_every_environment_file(self, tmp_path):
        self._write_env(tmp_path, "a.yaml", variables=["A"])
        self._write_env(tmp_path, "b.yaml", features=["B"])
        svc = self._service(tmp_path, ["a.yaml", "b.yaml"])

        assert svc._load_environment_declared_keys(str(tmp_path), None) == {"A", "B"}

    def test_secrets_are_not_collected(self, tmp_path):
        (tmp_path / "env.yaml").write_text(
            "kind: environment\nspec:\n  secrets:\n    - key: TOKEN\n      store: environment\n      value: TOKEN\n",
            encoding="utf-8",
        )
        svc = self._service(tmp_path, ["env.yaml"])

        assert svc._load_environment_declared_keys(str(tmp_path), None) == set()

    def test_missing_file_yields_none_not_partial_set(self, tmp_path):
        self._write_env(tmp_path, "a.yaml", variables=["A"])
        svc = self._service(tmp_path, ["a.yaml", "gone.yaml"])

        assert svc._load_environment_declared_keys(str(tmp_path), None) is None

    def test_tenant_deployments_are_skipped(self, tmp_path):
        """Tenant environments prepend keys this fallback does not resolve."""
        self._write_env(tmp_path, "env.yaml", variables=["A"])
        svc = self._service(tmp_path, ["env.yaml"], tenant="contoso")

        assert svc._load_environment_declared_keys(str(tmp_path), None) is None

    def test_no_work_path_yields_none(self, tmp_path):
        self._write_env(tmp_path, "env.yaml", variables=["A"])
        svc = self._service(tmp_path, ["env.yaml"])

        assert svc._load_environment_declared_keys(None, None) is None

    def test_no_environments_yields_none(self, tmp_path):
        svc = self._service(tmp_path, [])

        assert svc._load_environment_declared_keys(str(tmp_path), None) is None

    def test_loaded_environment_service_takes_precedence(self, tmp_path):
        svc = self._service(tmp_path, ["env.yaml"])
        env_service = MagicMock()
        env_service.get_variables.return_value = [MagicMock(key="FROM_SERVICE")]
        env_service.get_features.return_value = []
        svc._environment_service = env_service
        svc.get_environment_service = MagicMock(return_value=env_service)  # type: ignore[method-assign]

        assert svc._load_environment_declared_keys(str(tmp_path), None) == {"FROM_SERVICE"}

    def test_does_not_call_the_raising_public_getter(self, tmp_path):
        """`get_environment_service()` raises when services were never loaded —
        the normal case during a plain `strata validate`."""
        self._write_env(tmp_path, "env.yaml", variables=["A"])
        svc = self._service(tmp_path, ["env.yaml"])
        svc.get_environment_service = MagicMock(side_effect=AssertionError("must not be called"))  # type: ignore[method-assign]

        assert svc._load_environment_declared_keys(str(tmp_path), None) == {"A"}


class TestValidateStageDependsOn:
    """ADR-0083 Phase 5 — depends_on is validated now that it controls execution order.

    Before this, a typo'd or circular depends_on was completely inert (it only drew
    diagram edges), so it could sit unnoticed in a deployment file indefinitely.
    """

    def _service(self, stages) -> DeploymentService:
        svc = DeploymentService.__new__(DeploymentService)
        model = MagicMock()
        model.spec.stages = stages
        svc.model = model
        return svc

    def _dep_stage(self, name, depends_on=None) -> MagicMock:
        stage = MagicMock()
        stage.name = name
        stage.depends_on = depends_on
        return stage

    def test_sound_graph_passes(self):
        svc = self._service([self._dep_stage("a"), self._dep_stage("b", ["a"])])

        assert svc._validate_stage_depends_on() == []

    def test_no_stages_passes(self):
        assert self._service([])._validate_stage_depends_on() == []

    def test_unknown_dependency_fails(self):
        svc = self._service([self._dep_stage("a", ["ghost"])])

        errors = svc._validate_stage_depends_on()

        assert len(errors) == 1
        assert "ghost" in errors[0]

    def test_cycle_fails(self):
        svc = self._service([self._dep_stage("a", ["b"]), self._dep_stage("b", ["a"])])

        errors = svc._validate_stage_depends_on()

        assert len(errors) == 1
        assert "cycle" in errors[0]

    def test_delegates_to_the_shared_implementation(self):
        """Ordering and validation must not disagree about what a valid graph is."""
        from strata.utils import stage_selection

        svc = self._service([self._dep_stage("a")])
        with patch.object(stage_selection, "validate_stage_dependencies", return_value=["sentinel"]) as shared:
            assert svc._validate_stage_depends_on() == ["sentinel"]

        shared.assert_called_once()


class TestValidateDynamicWiring:
    """The check must actually be reached from _validate_dynamic."""

    def test_validate_dynamic_invokes_the_enabled_refs_check(self, tmp_path):
        from unittest.mock import patch

        svc = DeploymentService.__new__(DeploymentService)
        model = MagicMock()
        model.spec.partial = False
        model.spec.environments = []
        model.spec.configurations = []
        model.spec.tenant = None
        model.spec.versions = None
        svc.model = model
        svc._repo_map = {}
        svc._validation_warnings = []

        with (
            patch.object(DeploymentService, "_validate_stage_enabled_refs", return_value=["boom"]) as check,
            patch.object(DeploymentService, "_validate_sync_stages", return_value=[]),
            patch.object(DeploymentService, "_validate_helm_stage_namespaces", return_value=[]),
        ):
            ok, errors = svc._validate_dynamic(configuration_model=None, work_path=str(tmp_path))

        check.assert_called_once_with(str(tmp_path), None)
        assert ok is False
        assert "boom" in errors
