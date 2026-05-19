"""Unit tests for PlatformBuilder."""

from unittest.mock import MagicMock, patch

from xyz_platform.builders.platform_builder import PlatformBuilder


def _mock_deployment_service(validated=True, workspace_service=None, build_path=None):
    """Return a minimal MagicMock DeploymentService."""
    svc = MagicMock()
    svc.is_validated.return_value = validated
    svc.get_workspace_service.return_value = workspace_service
    if build_path is not None:
        svc.get_build_path.return_value = build_path
    return svc


class TestPlatformBuilderInit:
    def test_default_init(self):
        builder = PlatformBuilder()
        assert builder.verbose is False
        assert builder.configuration_service is None
        assert builder._last_platform_model is None

    def test_verbose_flag(self):
        builder = PlatformBuilder(verbose=True)
        assert builder.verbose is True

    def test_configuration_service_stored(self):
        mock_cfg = MagicMock()
        builder = PlatformBuilder(configuration_service=mock_cfg)
        assert builder.configuration_service is mock_cfg

    def test_no_errors_on_init(self):
        builder = PlatformBuilder()
        assert not builder.has_errors()
        assert not builder.has_messages()


class TestPlatformBuilderBeforeBuild:
    def test_not_validated_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        svc = _mock_deployment_service(validated=False)
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is False
        assert builder.has_errors()
        assert any("not validated" in e for e in builder.get_errors())

    def test_no_workspace_service_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        svc = _mock_deployment_service(validated=True, workspace_service=None)
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is False
        assert builder.has_errors()
        assert any("Workspace" in e for e in builder.get_errors())

    def test_valid_service_returns_true(self, tmp_path):
        builder = PlatformBuilder()
        svc = _mock_deployment_service(validated=True, workspace_service=MagicMock(), build_path=tmp_path / "dep")
        result = builder.before_build(svc, tmp_path, tmp_path)
        assert result is True
        assert not builder.has_errors()

    def test_verbose_adds_message(self, tmp_path):
        builder = PlatformBuilder(verbose=True)
        svc = _mock_deployment_service(validated=True, workspace_service=MagicMock(), build_path=tmp_path / "dep")
        builder.before_build(svc, tmp_path, tmp_path)
        assert builder.has_messages()
        assert any("Pre-build" in m for m in builder.get_messages())


class TestPlatformBuilderBuild:
    def test_dry_run_no_file_write(self, tmp_path):
        builder = PlatformBuilder()
        mock_platform = MagicMock()
        svc = MagicMock()
        svc.get_build_path.return_value = tmp_path / "out"

        with patch.object(builder, "_build_platform", return_value=(mock_platform, ["assembled"])):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=True)

        assert result is True
        assert not builder.has_errors()
        assert builder._last_platform_model is mock_platform
        # No files written
        assert not (tmp_path / "out" / "platform.json").exists()
        # Dry-run messages emitted
        messages = "\n".join(builder.get_messages())
        assert "DRY-RUN" in messages
        assert "platform.json" in messages

    def test_dry_run_reports_build_messages(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()
        svc.get_build_path.return_value = tmp_path / "out"

        with patch.object(builder, "_build_platform", return_value=(MagicMock(), ["progress note"])):
            builder.build(svc, tmp_path, tmp_path, dry_run=True)

        assert "progress note" in builder.get_messages()

    def test_build_platform_returns_none_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()

        with patch.object(builder, "_build_platform", return_value=(None, ["assembly failed"])):
            result = builder.build(svc, tmp_path, tmp_path, dry_run=False)

        assert result is False
        assert builder._last_platform_model is None

    def test_exception_in_build_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()

        with patch.object(builder, "_build_platform", side_effect=RuntimeError("boom")):
            result = builder.build(svc, tmp_path, tmp_path)

        assert result is False
        assert builder.has_errors()
        assert any("Failed to build platform model" in e for e in builder.get_errors())

    def test_last_platform_model_set_on_success(self, tmp_path):
        builder = PlatformBuilder()
        mock_platform = MagicMock()
        svc = MagicMock()
        svc.get_build_path.return_value = tmp_path / "out"

        with patch.object(builder, "_build_platform", return_value=(mock_platform, [])):
            builder.build(svc, tmp_path, tmp_path, dry_run=True)

        assert builder._last_platform_model is mock_platform

    def test_last_platform_model_none_after_failure(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()

        with patch.object(builder, "_build_platform", return_value=(None, [])):
            builder.build(svc, tmp_path, tmp_path)

        assert builder._last_platform_model is None


class TestPlatformBuilderAfterBuild:
    def test_dry_run_returns_true(self, tmp_path):
        builder = PlatformBuilder()
        svc = MagicMock()
        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert result is True
        assert not builder.has_errors()

    def test_dry_run_verbose_message(self, tmp_path):
        builder = PlatformBuilder(verbose=True)
        svc = MagicMock()
        builder.after_build(svc, tmp_path, tmp_path, dry_run=True)
        assert builder.has_messages()
        assert any("DRY-RUN" in m for m in builder.get_messages())

    def test_files_present_returns_true(self, tmp_path):
        builder = PlatformBuilder()
        build_dir = tmp_path / "mydeployment-1.0.0"
        build_dir.mkdir()
        (build_dir / "platform.json").write_text("{}")
        (build_dir / "platform.yaml").write_text("")

        svc = MagicMock()
        svc.get_build_path.return_value = build_dir

        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is True
        assert not builder.has_errors()

    def test_files_missing_returns_false(self, tmp_path):
        builder = PlatformBuilder()
        build_dir = tmp_path / "mydeployment-1.0.0"
        build_dir.mkdir()
        # No files created

        svc = MagicMock()
        svc.get_build_path.return_value = build_dir

        result = builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert result is False
        assert builder.has_errors()
        assert any("not created" in e for e in builder.get_errors())

    def test_verbose_reports_path_on_success(self, tmp_path):
        builder = PlatformBuilder(verbose=True)
        build_dir = tmp_path / "mydeployment-1.0.0"
        build_dir.mkdir()
        (build_dir / "platform.json").write_text("{}")
        (build_dir / "platform.yaml").write_text("")

        svc = MagicMock()
        svc.get_build_path.return_value = build_dir

        builder.after_build(svc, tmp_path, tmp_path, dry_run=False)
        assert builder.has_messages()
