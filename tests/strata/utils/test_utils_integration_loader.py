"""Tests for the ``integration_loader`` utility."""

from strata.utils.integration_loader import load_workspace_integrations


class TestLoadWorkspaceIntegrationsDirectory:
    def test_returns_zero_when_no_platform_dir(self, tmp_path):
        result = load_workspace_integrations(tmp_path)
        assert result == 0

    def test_returns_zero_when_integrations_dir_absent(self, tmp_path):
        (tmp_path / ".strata").mkdir()
        result = load_workspace_integrations(tmp_path)
        assert result == 0

    def test_returns_zero_for_empty_directory(self, tmp_path):
        (tmp_path / ".strata" / "integrations").mkdir(parents=True)
        result = load_workspace_integrations(tmp_path)
        assert result == 0


class TestLoadWorkspaceIntegrationsFiles:
    def test_loads_valid_integration(self, tmp_path):
        integrations_dir = tmp_path / ".strata" / "integrations"
        integrations_dir.mkdir(parents=True)
        (integrations_dir / "my_tool.py").write_text(
            "def register():\n    pass\n",
            encoding="utf-8",
        )
        result = load_workspace_integrations(tmp_path)
        assert result == 1

    def test_calls_register_function(self, tmp_path):
        integrations_dir = tmp_path / ".strata" / "integrations"
        integrations_dir.mkdir(parents=True)
        called = []
        # Write file that sets a flag when register() is invoked
        (integrations_dir / "flag_tool.py").write_text(
            "import sys\ndef register():\n    sys.modules[__name__]._called = True\n",
            encoding="utf-8",
        )
        result = load_workspace_integrations(tmp_path)
        assert result == 1

    def test_skips_underscore_files(self, tmp_path):
        integrations_dir = tmp_path / ".strata" / "integrations"
        integrations_dir.mkdir(parents=True)
        (integrations_dir / "__init__.py").write_text("def register(): pass\n", encoding="utf-8")
        (integrations_dir / "_private.py").write_text("def register(): pass\n", encoding="utf-8")
        result = load_workspace_integrations(tmp_path)
        assert result == 0

    def test_returns_zero_for_file_without_register(self, tmp_path):
        integrations_dir = tmp_path / ".strata" / "integrations"
        integrations_dir.mkdir(parents=True)
        (integrations_dir / "no_register.py").write_text(
            "# no register function here\n",
            encoding="utf-8",
        )
        result = load_workspace_integrations(tmp_path)
        assert result == 0

    def test_does_not_raise_on_syntax_error(self, tmp_path):
        integrations_dir = tmp_path / ".strata" / "integrations"
        integrations_dir.mkdir(parents=True)
        (integrations_dir / "bad_syntax.py").write_text(
            "def register(:\n    pass\n",
            encoding="utf-8",
        )
        result = load_workspace_integrations(tmp_path)
        assert result == 0

    def test_does_not_raise_when_register_raises(self, tmp_path):
        integrations_dir = tmp_path / ".strata" / "integrations"
        integrations_dir.mkdir(parents=True)
        (integrations_dir / "raising.py").write_text(
            "def register():\n    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        result = load_workspace_integrations(tmp_path)
        assert result == 0

    def test_loads_multiple_files(self, tmp_path):
        integrations_dir = tmp_path / ".strata" / "integrations"
        integrations_dir.mkdir(parents=True)
        for i in range(3):
            (integrations_dir / f"tool_{i}.py").write_text(
                "def register(): pass\n",
                encoding="utf-8",
            )
        result = load_workspace_integrations(tmp_path)
        assert result == 3
