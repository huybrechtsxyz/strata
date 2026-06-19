"""Unit tests for AnsibleBuilder."""

from unittest.mock import MagicMock, patch

import yaml

from strata.builders.ansible_builder import AnsibleBuilder


def _mock_svc(validated=True, build_path=None):
    svc = MagicMock()
    svc.is_validated.return_value = validated
    if build_path:
        svc.get_build_path.return_value = build_path
    return svc


def _minimal_vars_dict(resource_types=None):
    """Return a minimal _build_ansible_vars result with empty sections."""
    return {
        "workspace": {"strata_workspace": {"name": "ws"}},
        "providers": {"strata_providers": {}},
        "topologies": {"strata_topologies": {}},
        "resources": {"strata_resources": {}},
        "resources_by_type": {rt: {} for rt in (resource_types or [])},
        "modules": {"strata_modules": {}},
        "namespaces": {"strata_namespaces": {}},
        "firewalls": {"strata_firewalls": {}},
        "dns": {"strata_dns_zones": {}},
        "networks": {"strata_networks": {}},
    }


def _full_vars_dict(resource_types=None):
    """Return a _build_ansible_vars result with non-empty sections."""
    return {
        "workspace": {"strata_workspace": {"name": "ws"}},
        "providers": {"strata_providers": {"p1": {"type": "hetzner"}}},
        "topologies": {"strata_topologies": {"t1": {"type": "vm"}}},
        "resources": {"strata_resources": {"r1": {"type": "server"}}},
        "resources_by_type": {rt: {"r1": {}} for rt in (resource_types or [])},
        "modules": {"strata_modules": {"m1": {}}},
        "namespaces": {"strata_namespaces": {"ns1": {}}},
        "firewalls": {"strata_firewalls": {"fw1": {}}},
        "dns": {"strata_dns_zones": {"dns1": {}}},
        "networks": {"strata_networks": {"net1": {}}},
    }


class TestAnsibleBuilderInit:
    def test_default_init(self):
        builder = AnsibleBuilder()
        assert builder.verbose is False
        assert not builder.has_errors()
        assert not builder.has_messages()

    def test_verbose_flag(self):
        builder = AnsibleBuilder(verbose=True)
        assert builder.verbose is True


class TestAnsibleBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = AnsibleBuilder()
        svc = _mock_svc(validated=False)
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is False
        assert builder.has_errors()
        assert any("not validated" in e for e in builder.get_errors())

    def test_dry_run_skips_file_check(self, tmp_path):
        builder = AnsibleBuilder()
        svc = _mock_svc(validated=True, build_path=tmp_path / "out")
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_platform_json_missing_returns_false(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        builder = AnsibleBuilder()
        svc = _mock_svc(validated=True, build_path=build_dir)
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()
        assert any("not found" in e for e in builder.get_errors())

    def test_platform_json_present_returns_true(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        (build_dir / "platform.json").write_text("{}")
        builder = AnsibleBuilder()
        svc = _mock_svc(validated=True, build_path=build_dir)
        result = builder.before_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()

    def test_verbose_adds_message_on_success(self, tmp_path):
        builder = AnsibleBuilder(verbose=True)
        svc = _mock_svc(validated=True, build_path=tmp_path / "out")
        builder.before_build(svc, tmp_path, tmp_path, dry_run=True)
        assert builder.has_messages()
        assert any("Pre-build" in m for m in builder.get_messages())


class TestAnsibleBuilderBuild:
    def test_dry_run_with_platform_model_returns_true(self, tmp_path):
        builder = AnsibleBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(validated=True, build_path=build_dir)
        platform_model = MagicMock()

        with patch.object(builder, "_build_ansible_vars", return_value=_minimal_vars_dict()):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert result is True
        assert not builder.has_errors()
        messages = "\n".join(builder.get_messages())
        assert "DRY-RUN" in messages

    def test_dry_run_lists_planned_resource_type_files(self, tmp_path):
        builder = AnsibleBuilder()
        build_dir = tmp_path / "dep-1.0.0"
        svc = _mock_svc(build_path=build_dir)
        platform_model = MagicMock()
        vars_dict = _full_vars_dict(resource_types=["objectstorage", "virtualmachine"])

        with patch.object(builder, "_build_ansible_vars", return_value=vars_dict):
            builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        messages = "\n".join(builder.get_messages())
        assert "strata_resx_objectstorage.yml" in messages
        assert "strata_resx_virtualmachine.yml" in messages

    def test_platform_json_missing_without_model_returns_false(self, tmp_path):
        build_dir = tmp_path / "dep-1.0.0"
        build_dir.mkdir()
        builder = AnsibleBuilder()
        svc = _mock_svc(build_path=build_dir)
        result = builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=None)
        assert result is False
        assert builder.has_errors()
        assert any("Platform model not found" in e for e in builder.get_errors())

    def test_exception_in_build_returns_false(self, tmp_path):
        builder = AnsibleBuilder()
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()

        with patch.object(builder, "_build_ansible_vars", side_effect=RuntimeError("boom")):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True, platform_model=platform_model)

        assert result is False
        assert builder.has_errors()
        assert any("Failed to build Ansible artifacts" in e for e in builder.get_errors())

    def test_writes_yaml_files(self, tmp_path):
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()
        vars_dict = _full_vars_dict(resource_types=["objectstorage"])

        with patch.object(builder, "_build_ansible_vars", return_value=vars_dict):
            with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
                result = builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=platform_model)

        assert result is True
        assert (ansible_dir / "strata_workspace.yml").exists()
        assert (ansible_dir / "strata_providers.yml").exists()
        assert (ansible_dir / "strata_topologies.yml").exists()
        assert (ansible_dir / "strata_resources.yml").exists()
        assert (ansible_dir / "strata_modules.yml").exists()
        assert (ansible_dir / "strata_namespaces.yml").exists()
        assert (ansible_dir / "strata_firewalls.yml").exists()
        assert (ansible_dir / "strata_dns.yml").exists()
        assert (ansible_dir / "strata_networks.yml").exists()
        assert (ansible_dir / "strata_resx_objectstorage.yml").exists()

    def test_skips_empty_section_files(self, tmp_path):
        """Empty data sections must NOT produce var files."""
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()
        vars_dict = _minimal_vars_dict()  # all sections empty

        with patch.object(builder, "_build_ansible_vars", return_value=vars_dict):
            with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
                result = builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=platform_model)

        assert result is True
        assert (ansible_dir / "strata_workspace.yml").exists()
        # None of the empty-section files should be written
        for name in [
            "strata_providers.yml",
            "strata_topologies.yml",
            "strata_resources.yml",
            "strata_modules.yml",
            "strata_namespaces.yml",
            "strata_firewalls.yml",
            "strata_dns.yml",
            "strata_networks.yml",
        ]:
            assert not (ansible_dir / name).exists(), f"{name} should not be written for empty section"

    def test_written_yaml_is_valid(self, tmp_path):
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        svc = _mock_svc(build_path=tmp_path)
        platform_model = MagicMock()
        vars_dict = _minimal_vars_dict()

        with patch.object(builder, "_build_ansible_vars", return_value=vars_dict):
            with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
                builder.build(svc, tmp_path, tmp_path, dry_run=False, platform_model=platform_model)

        content = (ansible_dir / "strata_workspace.yml").read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        assert "strata_workspace" in parsed
        assert parsed["strata_workspace"]["name"] == "ws"


class TestAnsibleBuilderAfterBuild:
    def test_dry_run_returns_true(self, tmp_path):
        builder = AnsibleBuilder()
        svc = _mock_svc()
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_dry_run_verbose_message(self, tmp_path):
        builder = AnsibleBuilder(verbose=True)
        svc = _mock_svc()
        builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert any("DRY-RUN" in m for m in builder.get_messages())

    def test_no_files_returns_false(self, tmp_path):
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        ansible_dir.mkdir()
        svc = _mock_svc()
        with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
            result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()

    def test_with_files_returns_true(self, tmp_path):
        builder = AnsibleBuilder()
        ansible_dir = tmp_path / "ansible"
        ansible_dir.mkdir()
        (ansible_dir / "strata_workspace.yml").write_text("---\nstrata_workspace: {}")
        svc = _mock_svc()
        with patch.object(builder, "_resolve_ansible_paths", return_value=[ansible_dir]):
            result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()


class TestAnsibleBuilderVarAssembly:
    """Test the variable assembly methods with mock platform models."""

    def _make_platform(self, **overrides):
        """Create a minimal mock platform model."""
        platform = MagicMock()
        platform.meta.name = "test_deploy"
        platform.meta.labels = {"version": "2.0.0", "environment": "staging"}
        platform.meta.annotations = {"description": "Test deployment"}
        platform.meta.tags = ["ci"]
        platform.apiVersion = MagicMock(value="strata.huybrechts.xyz/v1")
        platform.spec.workspace.name = "my_workspace"
        platform.spec.workspace.labels = {"version": "1.0.0"}
        platform.spec.workspace.annotations = {"description": "Test workspace"}
        platform.spec.workspace.tags = ["prod"]
        platform.spec.providers = overrides.get("providers", [])
        platform.spec.topologies = overrides.get("topologies", [])
        platform.spec.resources = overrides.get("resources", [])
        platform.spec.modules = overrides.get("modules", [])
        platform.spec.namespaces = overrides.get("namespaces", [])
        platform.spec.firewalls = overrides.get("firewalls", [])
        platform.spec.dns_zones = overrides.get("dns_zones", [])
        platform.spec.networks = overrides.get("networks", [])
        return platform

    def test_workspace_vars(self):
        builder = AnsibleBuilder()
        platform = self._make_platform()
        result = builder._build_workspace_vars(platform, [])
        ws = result["strata_workspace"]
        assert ws["name"] == "my_workspace"
        assert ws["deployment_name"] == "test_deploy"
        assert ws["environment"] == "staging"

    def test_provider_vars(self):
        builder = AnsibleBuilder()
        provider = MagicMock()
        provider.name = "aws_eu"
        provider.properties.type = "aws"
        provider.properties.region = "eu-west-1"
        provider.properties.engine = "terraform"
        provider.properties.version = "5.0"
        provider.description = "AWS EU"
        provider.labels = {"team": "infra"}
        provider.tags = ["cloud"]
        platform = self._make_platform(providers=[provider])
        result = builder._build_provider_vars(platform, [])
        assert "aws_eu" in result["strata_providers"]
        assert result["strata_providers"]["aws_eu"]["region"] == "eu-west-1"

    def test_resource_vars(self):
        builder = AnsibleBuilder()
        resource = MagicMock()
        resource.name = "bucket_logs"
        resource.properties.resource_type = "objectstorage"
        resource.properties.provider_type = "aws"
        resource.properties.category = "storage"
        resource.properties.subcategory = "s3"
        resource.properties.unit_cost = 0.023
        resource.annotations = {"description": "Log bucket"}
        resource.labels = {}
        resource.tags = []
        resource.count = 3
        resource.configuration = {"versioning": True}
        resource.storage = None
        resource.firewalls = None
        resource.firewall = None
        resource.role = None
        platform = self._make_platform(resources=[resource])
        result = builder._build_resource_vars(platform, [])
        assert "bucket_logs" in result["strata_resources"]
        assert result["strata_resources"]["bucket_logs"]["count"] == 3
        assert result["strata_resources"]["bucket_logs"]["configuration"]["versioning"] is True

    def test_resources_by_type(self):
        builder = AnsibleBuilder()
        r1 = MagicMock()
        r1.name = "bucket_a"
        r1.properties.resource_type = "objectstorage"
        r1.properties.provider_type = "aws"
        r1.properties.category = "storage"
        r1.properties.subcategory = "s3"
        r1.properties.unit_cost = 0.0
        r1.annotations = {}
        r1.labels = {}
        r1.tags = []
        r1.count = 1
        r1.configuration = None
        r1.storage = None
        r1.firewalls = None
        r1.firewall = None
        r1.role = None

        r2 = MagicMock()
        r2.name = "vm_web"
        r2.properties.resource_type = "virtualmachine"
        r2.properties.provider_type = "aws"
        r2.properties.category = "compute"
        r2.properties.subcategory = "ec2"
        r2.properties.unit_cost = 0.10
        r2.annotations = {}
        r2.labels = {}
        r2.tags = []
        r2.count = 2
        r2.configuration = None
        r2.storage = None
        r2.firewalls = None
        r2.firewall = None
        r2.role = None

        platform = self._make_platform(resources=[r1, r2])
        result = builder._build_resources_by_type(platform, [])
        assert "objectstorage" in result
        assert "virtualmachine" in result
        assert "bucket_a" in result["objectstorage"]
        assert "vm_web" in result["virtualmachine"]

    def test_topology_vars(self):
        builder = AnsibleBuilder()
        comp = MagicMock()
        comp.resource = "vm_manager"
        comp.role = "manager"
        comp.count = 3
        topo = MagicMock()
        topo.name = "swarm"
        topo.type = "dockerswarm"
        topo.provider = "hetzner_eu"
        topo.provisioner = "terraform"
        topo.components = [comp]
        topo.volumes = []
        platform = self._make_platform(topologies=[topo])
        result = builder._build_topology_vars(platform, [])
        assert "swarm" in result["strata_topologies"]
        assert result["strata_topologies"]["swarm"]["components"][0]["count"] == 3


class TestAnsibleBuilderPlannedFiles:
    def test_base_files_list_empty_sections(self):
        """With all sections empty, only workspace.yml should be planned."""
        builder = AnsibleBuilder()
        files = builder._get_planned_files(_minimal_vars_dict())
        assert "strata_workspace.yml" in files
        assert "strata_resources.yml" not in files
        assert len(files) == 1

    def test_base_files_list_full_sections(self):
        """With all sections populated, all 9 base files should be planned."""
        builder = AnsibleBuilder()
        files = builder._get_planned_files(_full_vars_dict())
        assert "strata_workspace.yml" in files
        assert "strata_resources.yml" in files
        assert len(files) == 9  # workspace + 8 non-empty sections

    def test_includes_resource_type_files(self):
        builder = AnsibleBuilder()
        files = builder._get_planned_files(_full_vars_dict(resource_types=["objectstorage", "vm"]))
        assert "strata_resx_objectstorage.yml" in files
        assert "strata_resx_vm.yml" in files
        assert len(files) == 11  # 9 base + 2 type files
