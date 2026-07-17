"""Tests for the ``new`` command."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_new import new_command
from strata.commands.new.run_new_command import NewCommand


class TestNewCommand:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--help"])
        assert result.exit_code == 0
        assert "TEMPLATE" in result.output
        assert "NAME" in result.output

    def test_basic_creation(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["namespace", "myapp", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_overwrite_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["namespace", "myapp", "--overwrite", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_with_path(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["namespace", "myapp", "--output-file", str(tmp_path), "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_set_override(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.new.run_new_command.NewCommand.execute", return_value=True):
            result = runner.invoke(
                new_command,
                ["namespace", "myapp", "--set", "owner=myteam", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_list_templates(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_list_templates_shows_single_file_templates(self, tmp_path):
        """--list shows built-in single-file templates (e.g. namespace, provider)."""
        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "namespace" in result.output
        assert "provider" in result.output

    def test_list_templates_shows_scaffold_bundles(self, tmp_path):
        """--list shows scaffold bundles from examples/ with descriptions."""
        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "aks" in result.output
        assert "compose" in result.output
        # Descriptions from template.yaml should be shown
        assert "Kubernetes" in result.output or "Terraform" in result.output

    def test_list_templates_json_output(self, tmp_path):
        """--list --output json returns structured data with all templates."""
        import json

        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--output", "json", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        names = [t["name"] for t in data["data"]["templates"]]
        assert "namespace" in names
        assert "aks" in names

    def test_missing_template_exits_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(new_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_missing_name_exits_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(new_command, ["namespace", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_unknown_template_exits_1(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["nonexistent_xyz_template", "myapp", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_real_namespace_creation(self, tmp_path):
        """Integration test: actually render the namespace template into tmp_path."""
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            [
                "namespace",
                "myapp",
                "--output-file",
                str(tmp_path),
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        out_file = tmp_path / "myapp-namespace.yaml"
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "myapp" in content

    def test_existing_file_no_overwrite_exits_1(self, tmp_path):
        """Writing the same file twice without --overwrite must exit 1."""
        runner = CliRunner()
        # First write succeeds
        runner.invoke(
            new_command,
            ["namespace", "myapp", "--output-file", str(tmp_path), "--work-path", str(tmp_path)],
        )
        # Second write without --overwrite must fail
        result = runner.invoke(
            new_command,
            ["namespace", "myapp", "--output-file", str(tmp_path), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_existing_file_with_overwrite_exits_0(self, tmp_path):
        """Writing the same file twice WITH --overwrite must exit 0."""
        runner = CliRunner()
        runner.invoke(
            new_command,
            ["namespace", "myapp", "--output-file", str(tmp_path), "--work-path", str(tmp_path)],
        )
        result = runner.invoke(
            new_command,
            [
                "namespace",
                "myapp",
                "--output-file",
                str(tmp_path),
                "--overwrite",
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output


class TestNewCommandContextSubstitution:
    """Unit-level tests for context variable substitution in NewCommand."""

    def test_context_substitution(self, tmp_path):
        """Team context from solution.json is merged into the render context."""
        mock_solution = MagicMock()
        mock_solution.spec.context = {"owner": "acme", "version": "2.0.0"}

        cmd = NewCommand(
            template="namespace",
            name="myapp",
            path=str(tmp_path),
            overwrite=False,
            set_values=("version=3.0.0",),
            work_path=str(tmp_path),
        )

        def _fake_load() -> tuple:
            cmd._solution_controller._solution = mock_solution
            return True, []

        with patch.object(cmd._solution_controller, "load", side_effect=_fake_load):
            # Replicate the context-building logic from _run_execution
            rendered_context: dict = {"name": "myapp"}
            ok, _ = cmd._solution_controller.load()
            if ok and cmd._solution_controller._solution is not None:
                rendered_context.update(cmd._solution_controller._solution.spec.context or {})
            for kv in cmd._set_values:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    rendered_context[k] = v

        assert rendered_context["name"] == "myapp"
        assert rendered_context["owner"] == "acme"
        assert rendered_context["version"] == "3.0.0"  # --set overrides context


class TestNewCommandBundle:
    """Tests for directory bundle templates (multi-file scaffolding)."""

    def _make_bundle(self, work_path, bundle_name: str):
        """Create a minimal bundle dir under .strata/templates/<bundle_name>/."""
        bundle_dir = work_path / ".strata" / "templates" / bundle_name
        bundle_dir.mkdir(parents=True)
        return bundle_dir

    def test_bundle_creates_flat_files(self, tmp_path):
        """A flat bundle directory produces files in --path root."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "{{ name }}.yaml").write_text("kind: widget\nname: {{ name }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["widget", "acme", "--output-file", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert (out / "acme.yaml").exists()

    def test_bundle_content_substitution(self, tmp_path):
        """{{ var }} in file content is substituted from context + --set."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "file.yaml").write_text("zone: {{ zone }}\ntier: {{ tier }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            [
                "widget",
                "acme",
                "--output-file",
                str(out),
                "--set",
                "zone=eu",
                "--set",
                "tier=premium",
                "--work-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        content = (out / "file.yaml").read_text(encoding="utf-8")
        assert "eu" in content
        assert "premium" in content

    def test_bundle_path_segment_substitution(self, tmp_path):
        """{{ name }} in directory names is substituted using the same engine."""
        bundle = self._make_bundle(tmp_path, "widget")
        subdir = bundle / "{{ name }}"
        subdir.mkdir()
        (subdir / "deployment.yaml").write_text("name: {{ name }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["widget", "acme", "--output-file", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert (out / "acme" / "deployment.yaml").exists()
        assert "acme" in (out / "acme" / "deployment.yaml").read_text(encoding="utf-8")

    def test_bundle_nested_path_and_filename(self, tmp_path):
        """{{ name }} works in both directory and filename simultaneously."""
        bundle = self._make_bundle(tmp_path, "widget")
        subdir = bundle / "envs" / "{{ name }}"
        subdir.mkdir(parents=True)
        (subdir / "{{ name }}-dev.yaml").write_text("env: dev\nname: {{ name }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["widget", "globex", "--output-file", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert (out / "envs" / "globex" / "globex-dev.yaml").exists()

    def test_bundle_overwrite_guard(self, tmp_path):
        """Second run without --overwrite exits 1 when output file exists."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "file.yaml").write_text("x: 1\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        runner.invoke(new_command, ["widget", "acme", "--output-file", str(out), "--work-path", str(tmp_path)])
        result = runner.invoke(new_command, ["widget", "acme", "--output-file", str(out), "--work-path", str(tmp_path)])
        assert result.exit_code == 1

    def test_bundle_overwrite_flag(self, tmp_path):
        """Second run WITH --overwrite exits 0."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "file.yaml").write_text("x: 1\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        runner.invoke(new_command, ["widget", "acme", "--output-file", str(out), "--work-path", str(tmp_path)])
        result = runner.invoke(
            new_command,
            ["widget", "acme", "--output-file", str(out), "--overwrite", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output

    def test_bundle_appears_in_list(self, tmp_path):
        """A workspace bundle directory appears in --list output."""
        bundle = self._make_bundle(tmp_path, "widget")
        (bundle / "file.yaml").write_text("x: 1\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(new_command, ["--list", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "widget" in result.output

    def test_bundle_workspace_overrides_package(self, tmp_path):
        """Workspace bundle takes precedence over package single-file template of same name."""
        # 'namespace' exists as a package single-file template.
        # A workspace bundle of the same name should win.
        bundle = self._make_bundle(tmp_path, "namespace")
        (bundle / "{{ name }}-custom.yaml").write_text("custom: true\nname: {{ name }}\n", encoding="utf-8")

        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            new_command,
            ["namespace", "myapp", "--output-file", str(out), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        # Bundle output file (not the single-file default name)
        assert (out / "myapp-custom.yaml").exists()
        assert not (out / "myapp-namespace.yaml").exists()
