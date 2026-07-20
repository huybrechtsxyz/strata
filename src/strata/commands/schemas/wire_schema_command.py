"""Command to wire schema files into VS Code YAML settings."""

import json
from typing import Dict, List, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.commands.schemas.schema_common import KIND_TO_GLOBS, KIND_TO_MODEL
from strata.utils.config import SOLUTION_DIR, SOLUTION_SCHEMAS_DIR, get_schemas_dir


class WireSchemaCommand(SchemaBaseCommand):
    """Wire exported schemas into .vscode/settings.json yaml.schemas."""

    OPERATION = "schema_wire"

    def __init__(
        self,
        dry_run: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._dry_run = dry_run

    def _execute(self) -> bool:
        root = self._work_path
        schemas_dir = get_schemas_dir(root)
        vscode_dir = root / ".vscode"
        settings_file = vscode_dir / "settings.json"

        schema_prefix = "${workspaceFolder}/" + SOLUTION_DIR + "/" + SOLUTION_SCHEMAS_DIR
        yaml_schemas: Dict[str, str | List[str]] = {}
        for kind, globs in KIND_TO_GLOBS.items():
            schema_key = f"{schema_prefix}/{kind.value}.json"
            yaml_schemas[schema_key] = globs if len(globs) > 1 else globs[0]

        existing: Dict[str, object] = {}
        if settings_file.exists():
            try:
                existing = json.loads(settings_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                self._errors.append(f".vscode/settings.json is not valid JSON: {exc}")
                return False

        merged = dict(existing)
        merged["yaml.schemas"] = yaml_schemas
        merged_text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"

        output_data: Dict[str, object] = {
            "settings_file": str(settings_file),
            "schemas_dir": str(schemas_dir),
            "yaml_schema_entries": len(yaml_schemas),
            "yaml_schemas": yaml_schemas,
            "dry_run": self._dry_run,
            "export_warnings": [],
        }

        if self._dry_run:
            self._output_data = output_data
            return True

        schemas_dir.mkdir(parents=True, exist_ok=True)
        export_warnings: List[str] = []
        for kind, model_cls in KIND_TO_MODEL.items():
            if kind not in KIND_TO_GLOBS:
                continue
            schema_file = schemas_dir / f"{kind.value}.json"
            try:
                schema_file.write_text(json.dumps(model_cls.model_json_schema(), indent=2), encoding="utf-8")  # type: ignore[attr-defined]
            except Exception as exc:
                export_warnings.append(f"{kind.value}: {exc}")

        vscode_dir.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(merged_text, encoding="utf-8")

        output_data["export_warnings"] = export_warnings
        self._output_data = output_data
        self._messages.extend(export_warnings)
        return True

    def _after_execute(self) -> bool:
        if self._is_console_output():
            if self._dry_run:
                click.echo("")
                click.echo(f"  Would write: {self._output_data.get('settings_file')}")
                click.echo(f"  yaml.schemas entries: {self._output_data.get('yaml_schema_entries')}")
                click.echo("")
                click.echo(json.dumps({"yaml.schemas": self._output_data.get("yaml_schemas", {})}, indent=2))
                click.echo("")
                return super()._after_execute()

            click.echo("")
            click.echo(f"  Schemas exported  -> {self._output_data.get('schemas_dir')}")
            click.echo(f"  Settings updated  -> {self._output_data.get('settings_file')}")
            click.echo(f"  yaml.schemas entries: {self._output_data.get('yaml_schema_entries')}")

            warnings = self._output_data.get("export_warnings", [])
            if isinstance(warnings, list) and warnings:
                click.echo("")
                for warning in warnings:
                    click.echo(f"  WARNING: {warning}", err=True)

            click.echo("")
            click.echo("  Reload VS Code (Ctrl+Shift+P -> Reload Window) for changes to take effect.")
            click.echo("")

        return super()._after_execute()
