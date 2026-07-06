"""Tests for the `schema` command group."""

import json

from click.testing import CliRunner

from strata.commands.cli_schema import schema_group
from strata.models.common_models import PlatformKind


class TestSchemaList:
    def test_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["list"])
        assert result.exit_code == 0

    def test_json_output_contains_kinds_array(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["list", "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "kinds" in data
        assert isinstance(data["kinds"], list)
        assert len(data["kinds"]) > 0

    def test_json_output_includes_all_platform_kinds(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["list", "--output", "json"])
        data = json.loads(result.output)
        present_kinds = [k["kind"] if isinstance(k, dict) else k for k in data["kinds"]]
        for kind in PlatformKind:
            assert kind.value in present_kinds

    def test_text_output_one_kind_per_line(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["list", "--output", "text"])
        assert result.exit_code == 0
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        assert len(lines) > 0
        # Each line should be a bare kind value with no extra decorations
        for line in lines:
            assert "{" not in line


class TestSchemaGet:
    def test_exits_zero_for_valid_kind(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["get", "deployment"])
        assert result.exit_code == 0

    def test_default_output_is_valid_json_schema(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["get", "deployment"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        # JSON Schema documents must have at least a "properties" or "$defs" key
        assert "properties" in data or "$defs" in data

    def test_json_output_flag_produces_same_schema(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["get", "deployment", "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "properties" in data or "$defs" in data

    def test_text_output_shows_summary(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["get", "deployment", "--output", "text"])
        assert result.exit_code == 0
        assert "deployment" in result.output
        assert "Kind:" in result.output

    def test_all_valid_kinds_return_zero(self):
        runner = CliRunner()
        for kind in PlatformKind:
            result = runner.invoke(schema_group, ["get", kind.value])
            assert result.exit_code == 0, f"Failed for kind '{kind.value}': {result.output}"

    def test_unknown_kind_exits_2(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["get", "notakind"])
        assert result.exit_code == 2

    def test_unknown_kind_error_lists_valid_kinds(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["get", "notakind"])
        assert "deployment" in result.output

    def test_kind_lookup_is_case_insensitive(self):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["get", "DEPLOYMENT"])
        assert result.exit_code == 0


class TestSchemaWire:
    def test_dry_run_exits_zero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["wire", "--work-path", str(tmp_path), "--dry-run"])
        assert result.exit_code == 0

    def test_dry_run_shows_yaml_schemas(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["wire", "--work-path", str(tmp_path), "--dry-run"])
        assert "yaml.schemas" in result.output

    def test_dry_run_does_not_write_files(self, tmp_path):
        runner = CliRunner()
        runner.invoke(schema_group, ["wire", "--work-path", str(tmp_path), "--dry-run"])
        assert not (tmp_path / ".vscode" / "settings.json").exists()

    def test_wire_creates_settings_json(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(schema_group, ["wire", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        settings = tmp_path / ".vscode" / "settings.json"
        assert settings.exists()

    def test_wire_settings_contains_yaml_schemas(self, tmp_path):
        runner = CliRunner()
        runner.invoke(schema_group, ["wire", "--work-path", str(tmp_path)])
        settings = tmp_path / ".vscode" / "settings.json"
        data = json.loads(settings.read_text())
        assert "yaml.schemas" in data
        assert len(data["yaml.schemas"]) > 0

    def test_wire_exports_schema_files(self, tmp_path):
        runner = CliRunner()
        runner.invoke(schema_group, ["wire", "--work-path", str(tmp_path)])
        schemas_dir = tmp_path / ".strata" / "schemas"
        assert schemas_dir.exists()
        schema_files = list(schemas_dir.glob("*.json"))
        assert len(schema_files) > 0

    def test_wire_preserves_existing_settings(self, tmp_path):
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        existing = {"editor.fontSize": 14, "editor.tabSize": 2}
        (vscode_dir / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

        runner = CliRunner()
        runner.invoke(schema_group, ["wire", "--work-path", str(tmp_path)])

        data = json.loads((vscode_dir / "settings.json").read_text())
        assert data["editor.fontSize"] == 14
        assert data["editor.tabSize"] == 2
        assert "yaml.schemas" in data

    def test_wire_overwrites_existing_yaml_schemas(self, tmp_path):
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        existing = {"yaml.schemas": {"old-schema.json": "**/*.yaml"}}
        (vscode_dir / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

        runner = CliRunner()
        runner.invoke(schema_group, ["wire", "--work-path", str(tmp_path)])

        data = json.loads((vscode_dir / "settings.json").read_text())
        assert "old-schema.json" not in data["yaml.schemas"]

    def test_wire_invalid_existing_json_exits_nonzero(self, tmp_path):
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        (vscode_dir / "settings.json").write_text("{invalid json", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(schema_group, ["wire", "--work-path", str(tmp_path)])
        assert result.exit_code != 0
