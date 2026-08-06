"""Unit tests for TerraformBuilder."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.builders.terraform_builder import TerraformBuilder
from strata.models.common_models import ProvisionerType, SourceModel
from strata.models.workspace_model import WorkspaceIacModel


def _mock_svc(validated=True, build_path=None):
    svc = MagicMock()
    svc.is_validated.return_value = validated
    if build_path:
        svc.get_build_path.return_value = build_path
    return svc


def _minimal_vars_dict(resource_types=None):
    """Return a minimal _build_terraform_vars result."""
    return {
        "workspace": {"workspace_name": "ws"},
        "providers": {"platform_providers": {}},
        "topologies": {"topologies": {}},
        "resources_by_category": {rt: {} for rt in (resource_types or [])},
        "modules": {"modules": {}},
        "required_variables": {"variables": []},
        "required_features": {"features": []},
        "required_secrets": {"secrets": []},
    }


class TestTerraformBuilderInit:
    def test_default_init(self):
        builder = TerraformBuilder()
        assert builder.verbose is False
        assert builder.variable_refs == {}
        assert builder.feature_refs == {}
        assert builder.secret_refs == {}
        assert not builder.has_errors()
        assert not builder.has_messages()

    def test_verbose_flag(self):
        builder = TerraformBuilder(verbose=True)
        assert builder.verbose is True


class TestTerraformBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = TerraformBuilder()
        svc = _mock_svc(validated=False)
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is False
        assert builder.has_errors()
        assert any("not validated" in e for e in builder.get_errors())

    def test_dry_run_skips_file_check(self, tmp_path):
        builder = TerraformBuilder()
        svc = _mock_svc(validated=True, build_path=tmp_path / "out")
        # No platform.json on disk — should not matter in dry_run
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_platform_json_missing_returns_false(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        builder = TerraformBuilder()
        svc = _mock_svc(validated=True, build_path=build_dir)
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()
        assert any("not found" in e for e in builder.get_errors())

    def test_platform_json_present_returns_true(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        (build_dir / "platform.json").write_text("{}")
        builder = TerraformBuilder()
        svc = _mock_svc(validated=True, build_path=build_dir)
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()

    def test_verbose_adds_message_on_success(self, tmp_path):
        builder = TerraformBuilder(verbose=True)
        svc = _mock_svc(validated=True, build_path=tmp_path / "out")
        builder.before_build(svc, tmp_path, tmp_path, dry_run=True)
        assert builder.has_messages()
        assert any("Pre-build" in m for m in builder.get_messages())


class TestTerraformBuilderBuild:
    def test_dry_run_with_platform_model_returns_true(self, tmp_path):
        builder = TerraformBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(validated=True, build_path=build_dir)
        platform_model = MagicMock()

        with patch.object(builder, "_build_terraform_vars", return_value=_minimal_vars_dict()):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert result is True
        assert not builder.has_errors()
        messages = "\n".join(builder.get_messages())
        assert "DRY-RUN" in messages

    def test_dry_run_lists_planned_resource_files(self, tmp_path):
        builder = TerraformBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(build_path=build_dir)
        platform_model = MagicMock()
        vars_dict = _minimal_vars_dict(resource_types=["vm", "storage"])

        with patch.object(builder, "_build_terraform_vars", return_value=vars_dict):
            builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        messages = "\n".join(builder.get_messages())
        assert "resx_vm.auto.tfvars.json" in messages
        assert "resx_storage.auto.tfvars.json" in messages

    def test_dry_run_reports_requirement_counts(self, tmp_path):
        builder = TerraformBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(build_path=build_dir)
        platform_model = MagicMock()
        vars_dict = _minimal_vars_dict()
        vars_dict["required_variables"] = {"variables": [{"key": "v1"}, {"key": "v2"}]}
        vars_dict["required_secrets"] = {"secrets": [{"key": "s1"}]}

        with patch.object(builder, "_build_terraform_vars", return_value=vars_dict):
            builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        messages = "\n".join(builder.get_messages())
        assert "2 variable(s)" in messages
        assert "1 secret(s)" in messages

    def test_platform_json_missing_without_model_returns_false(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        # No platform.json
        builder = TerraformBuilder()
        svc = _mock_svc(build_path=build_dir)
        result = builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=None)
        assert result is False
        assert builder.has_errors()
        assert any("Platform model not found" in e for e in builder.get_errors())

    def test_exception_in_build_returns_false(self, tmp_path):
        builder = TerraformBuilder()
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()

        with patch.object(builder, "_build_terraform_vars", side_effect=RuntimeError("boom")):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert result is False
        assert builder.has_errors()
        assert any("Failed to build Terraform artifacts" in e for e in builder.get_errors())

    def test_refs_reset_on_each_build(self, tmp_path):
        builder = TerraformBuilder()
        builder.variable_refs = {"old_var": {"key": "old_var"}}
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()

        with patch.object(builder, "_build_terraform_vars", return_value=_minimal_vars_dict()):
            builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert builder.variable_refs == {}


class TestTerraformBuilderAfterBuild:
    def test_dry_run_returns_true(self, tmp_path):
        builder = TerraformBuilder()
        svc = _mock_svc()
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_dry_run_verbose_message(self, tmp_path):
        builder = TerraformBuilder(verbose=True)
        svc = _mock_svc()
        builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert builder.has_messages()
        assert any("DRY-RUN" in m for m in builder.get_messages())

    def test_all_base_files_present_returns_true(self, tmp_path):
        terraform_dir = tmp_path / "dep-1.0.0" / "terraform"
        terraform_dir.mkdir(parents=True)
        base_files = [
            "workspace.auto.tfvars.json",
            "providers.auto.tfvars.json",
            "topologies.auto.tfvars.json",
            "modules.auto.tfvars.json",
            "namespaces.auto.tfvars.json",
            "firewalls.auto.tfvars.json",
            "dns.auto.tfvars.json",
            "dns_secret_records.auto.tfvars.json",
            "networks.auto.tfvars.json",
            "tf_required_variables.json",
            "tf_required_features.json",
            "tf_required_secrets.json",
        ]
        for f in base_files:
            (terraform_dir / f).write_text("{}")

        svc = _mock_svc(build_path=tmp_path / "dep-1.0.0")
        builder = TerraformBuilder()
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()

    def test_missing_files_returns_false(self, tmp_path):
        terraform_dir = tmp_path / "dep-1.0.0" / "terraform"
        terraform_dir.mkdir(parents=True)
        # Only write workspace; builder expects providers.auto.tfvars.json too
        (terraform_dir / "workspace.auto.tfvars.json").write_text("{}")

        svc = _mock_svc(build_path=tmp_path / "dep-1.0.0")
        builder = TerraformBuilder()
        # Simulate that build() planned these files (one of which is missing)
        builder._written_file_names = ["workspace.auto.tfvars.json", "providers.auto.tfvars.json"]
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()
        assert any("missing" in e for e in builder.get_errors())

    def test_verbose_reports_counts(self, tmp_path):
        terraform_dir = tmp_path / "dep-1.0.0" / "terraform"
        terraform_dir.mkdir(parents=True)
        # Only workspace is always written; simulate a minimal build output
        (terraform_dir / "workspace.auto.tfvars.json").write_text("{}")
        (terraform_dir / "resx_vm.auto.tfvars.json").write_text("{}")

        svc = _mock_svc(build_path=tmp_path / "dep-1.0.0")
        builder = TerraformBuilder(verbose=True)
        builder._written_file_names = ["workspace.auto.tfvars.json"]
        builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert builder.has_messages()


class TestTerraformBuilderTracking:
    def test_track_variable_new_key(self):
        builder = TerraformBuilder()
        builder._track_variable("db_host", "Database host", ["resource_a"])
        assert "db_host" in builder.variable_refs
        entry = builder.variable_refs["db_host"]
        assert entry["key"] == "db_host"
        assert entry["required"] is True
        assert entry["suggested_env_var"] == "TF_VAR_db_host"
        assert "resource_a" in entry["used_by"]

    def test_track_variable_merges_used_by(self):
        builder = TerraformBuilder()
        builder._track_variable("db_host", "Database host", ["resource_a"])
        builder._track_variable("db_host", "Database host", ["resource_b"])
        used_by = builder.variable_refs["db_host"]["used_by"]
        assert "resource_a" in used_by
        assert "resource_b" in used_by

    def test_track_feature_new_key(self):
        builder = TerraformBuilder()
        builder._track_feature("enable_logging", "Logging flag", ["mod_a"])
        assert "enable_logging" in builder.feature_refs
        assert builder.feature_refs["enable_logging"]["key"] == "enable_logging"

    def test_track_feature_merges_used_by(self):
        builder = TerraformBuilder()
        builder._track_feature("flag", "desc", ["a"])
        builder._track_feature("flag", "desc", ["b"])
        assert "a" in builder.feature_refs["flag"]["used_by"]
        assert "b" in builder.feature_refs["flag"]["used_by"]

    def test_track_secret_new_key(self):
        builder = TerraformBuilder()
        builder._track_secret("api_key", "API secret", ["svc_a"])
        assert "api_key" in builder.secret_refs
        assert builder.secret_refs["api_key"]["key"] == "api_key"

    def test_document_required_variables_empty(self):
        builder = TerraformBuilder()
        result = builder._document_required_variables()
        assert result == {"variables": []}

    def test_document_required_variables_with_entries(self):
        builder = TerraformBuilder()
        builder._track_variable("v1", "desc1", ["a"])
        builder._track_variable("v2", "desc2", ["b"])
        result = builder._document_required_variables()
        keys = [e["key"] for e in result["variables"]]
        assert "v1" in keys
        assert "v2" in keys

    def test_document_required_features_empty(self):
        builder = TerraformBuilder()
        result = builder._document_required_features()
        assert result == {"features": []}

    def test_document_required_secrets_empty(self):
        builder = TerraformBuilder()
        result = builder._document_required_secrets()
        assert result == {"secrets": []}


class TestTerraformBuilderWorkspaceVars:
    def _make_platform(self, workspace_name="ws", workspace_labels=None, deployment_labels=None):
        platform = MagicMock()
        platform.spec.workspace.name = workspace_name
        platform.spec.workspace.labels = workspace_labels or {}
        platform.spec.workspace.annotations = {}
        platform.spec.workspace.tags = []
        platform.meta.labels = deployment_labels or {}
        platform.meta.name = "dep"
        platform.meta.annotations = {}
        platform.meta.tags = []
        platform.apiVersion = "strata.huybrechts.xyz/v1"
        return platform

    def test_workspace_name_in_result(self):
        builder = TerraformBuilder()
        platform = self._make_platform(workspace_name="my_ws")
        result = builder._build_workspace_vars(platform, [])
        assert result["workspace_name"] == "my_ws"

    def test_version_defaults_to_1_0_0(self):
        builder = TerraformBuilder()
        platform = self._make_platform(workspace_labels={})
        result = builder._build_workspace_vars(platform, [])
        assert result["workspace_version"] == "1.0.0"

    def test_version_from_workspace_labels(self):
        builder = TerraformBuilder()
        platform = self._make_platform(workspace_labels={"version": "2.5.0"})
        result = builder._build_workspace_vars(platform, [])
        assert result["workspace_version"] == "2.5.0"

    def test_environment_from_deployment_labels(self):
        builder = TerraformBuilder()
        platform = self._make_platform(deployment_labels={"environment": "staging"})
        result = builder._build_workspace_vars(platform, [])
        assert result["environment"] == "staging"

    def test_environment_defaults_to_production(self):
        builder = TerraformBuilder()
        platform = self._make_platform(deployment_labels={})
        result = builder._build_workspace_vars(platform, [])
        assert result["environment"] == "production"


def _make_provisioner(source_path: str, repository: str | None = None) -> WorkspaceIacModel:
    """Build a WorkspaceIacModel with a terraform provisioner for testing."""
    source = SourceModel(source_path=source_path, repository=repository)
    return WorkspaceIacModel(name="platform_iac", provisioner=ProvisionerType.TERRAFORM, source=source)


def _make_deployment_svc(provisioner: WorkspaceIacModel, build_path: Path) -> MagicMock:
    """Return a mock DeploymentService that surfaces the given provisioner."""
    workspace_model = MagicMock()
    workspace_model.spec.provisioners = [provisioner]

    workspace_service = MagicMock()
    workspace_service.model = workspace_model

    deployment_svc = MagicMock()
    deployment_svc.get_workspace_service.return_value = workspace_service
    deployment_svc.get_build_path.return_value = build_path
    return deployment_svc


class TestCopyProvisionerSourceSingleRepo:
    """_copy_provisioner_source resolves to work_path when repository is absent."""

    def test_no_repository_resolves_to_work_path(self, tmp_path):
        """Source dir must be work_path/source_path when repository is not set."""
        src_dir = tmp_path / "terraform"
        src_dir.mkdir()
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="terraform")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(depl_svc, build_path, tmp_path, repo_map={}, dry_run=True)

        assert result is True
        assert not builder.has_errors()
        messages = "\n".join(builder.get_messages())
        assert str(tmp_path / "terraform") in messages

    def test_no_repository_missing_src_dir_returns_false(self, tmp_path):
        """Error reported when source_path does not exist and repository is absent."""
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="nonexistent")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(depl_svc, build_path, tmp_path, repo_map={}, dry_run=True)

        assert result is False
        assert builder.has_errors()
        assert any("nonexistent" in e for e in builder.get_errors())

    def test_with_repository_uses_repo_map(self, tmp_path):
        """When repository is set and present in repo_map, repo_map root is used."""
        repo_root = tmp_path / "my-repo"
        src_dir = repo_root / "terraform"
        src_dir.mkdir(parents=True)
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner(source_path="terraform", repository="my_repo")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(
                depl_svc, build_path, tmp_path, repo_map={"my_repo": str(repo_root)}, dry_run=True
            )

        assert result is True
        messages = "\n".join(builder.get_messages())
        assert str(src_dir) in messages


def _make_provisioner_with_ref(
    source_path: str, repository: str | None = None, reference: str | None = None
) -> WorkspaceIacModel:
    """Build a WorkspaceIacModel with a terraform provisioner and optional ref pinning."""
    source = SourceModel(source_path=source_path, repository=repository, reference=reference)
    return WorkspaceIacModel(name="platform_iac", provisioner=ProvisionerType.TERRAFORM, source=source)


class TestCopyProvisionerSourceRefPinning:
    """_copy_provisioner_source handles source.reference for ref-pinned extraction."""

    def test_dry_run_with_reference_reports_ref(self, tmp_path):
        """In dry-run mode, when source.reference is set, message mentions the ref."""
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner_with_ref(source_path="terraform", repository="my_repo", reference="v1.4.0")
        depl_svc = _make_deployment_svc(prov, build_path)

        repo_root = tmp_path / "my-repo"
        repo_root.mkdir()

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(
                depl_svc, build_path, tmp_path, repo_map={"my_repo": str(repo_root)}, dry_run=True
            )

        assert result is True
        messages = "\n".join(builder.get_messages())
        assert "v1.4.0" in messages
        assert "DRY-RUN" in messages

    def test_no_reference_uses_standard_copy(self, tmp_path):
        """Without source.reference, standard working-tree copy is used."""
        src_dir = tmp_path / "terraform"
        src_dir.mkdir()
        (src_dir / "main.tf").write_text("resource {}")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner_with_ref(source_path="terraform", reference=None)
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(depl_svc, build_path, tmp_path, repo_map={}, dry_run=False)

        assert result is True
        assert (build_path / "terraform" / "main.tf").exists()

    def test_reference_on_non_git_dir_falls_back_to_copy(self, tmp_path):
        """When source.reference is set but repo_root has no .git, falls back to tree copy."""
        repo_root = tmp_path / "my-repo"
        src_dir = repo_root / "terraform"
        src_dir.mkdir(parents=True)
        (src_dir / "main.tf").write_text("resource {}")
        build_path = tmp_path / "build"
        build_path.mkdir()

        prov = _make_provisioner_with_ref(source_path="terraform", repository="my_repo", reference="v1.0.0")
        depl_svc = _make_deployment_svc(prov, build_path)

        builder = TerraformBuilder()
        with patch.object(builder, "_build_template_context", return_value={}):
            result = builder._copy_provisioner_source(
                depl_svc, build_path, tmp_path, repo_map={"my_repo": str(repo_root)}, dry_run=False
            )

        assert result is True
        assert (build_path / "terraform" / "main.tf").exists()
        messages = "\n".join(builder.get_messages())
        assert "not a git repository" in messages


class TestEmitVariableValue:
    """Tests for TerraformBuilder._emit_variable_value — typed emission logic."""

    def test_no_type_emits_value_as_is(self):
        """Without type field, value passes through unchanged."""
        from strata.models.store_models import VariableStoreModel

        var = VariableStoreModel(key="k", store="constant", value="hello")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == "hello"

    def test_no_type_dict_value_passes_through(self):
        """Without type, dict values pass through as-is (backward compat)."""
        from strata.models.store_models import VariableStoreModel

        val = {"nested": {"key": "value"}}
        var = VariableStoreModel(key="k", store="constant", value=val)
        result = TerraformBuilder._emit_variable_value(var)
        assert result == val

    def test_type_string_stringifies(self):
        """type=string forces string serialization."""
        from strata.models.store_models import VariableStoreModel

        var = VariableStoreModel(key="k", store="constant", value=42, type="string")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == "42"

    def test_type_number_emits_native(self):
        from strata.models.store_models import VariableStoreModel

        var = VariableStoreModel(key="k", store="constant", value=42, type="number")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == 42
        assert isinstance(result, int)

    def test_type_bool_emits_native(self):
        from strata.models.store_models import VariableStoreModel

        var = VariableStoreModel(key="k", store="constant", value=True, type="bool")
        result = TerraformBuilder._emit_variable_value(var)
        assert result is True

    def test_type_object_emits_dict(self):
        from strata.models.store_models import VariableStoreModel

        val = {"pools": {"default": {"size": "Standard_D4s_v3"}}}
        var = VariableStoreModel(key="k", store="constant", value=val, type="object")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == val
        assert isinstance(result, dict)

    def test_type_list_emits_list(self):
        from strata.models.store_models import VariableStoreModel

        val = ["10.0.0.0/8", "172.16.0.0/12"]
        var = VariableStoreModel(key="k", store="constant", value=val, type="list")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == val
        assert isinstance(result, list)

    def test_type_map_emits_dict(self):
        from strata.models.store_models import VariableStoreModel

        val = {"env": "prod", "team": "platform"}
        var = VariableStoreModel(key="k", store="constant", value=val, type="map")
        result = TerraformBuilder._emit_variable_value(var)
        assert result == val
