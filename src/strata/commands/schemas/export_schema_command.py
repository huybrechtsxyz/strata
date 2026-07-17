"""Command to export JSON schemas for all platform kinds."""

import json
from pathlib import Path
from typing import List, Optional

import click

from strata.commands.schemas.schema_base_command import SchemaBaseCommand
from strata.commands.schemas.schema_common import KIND_TO_MODEL


class ExportSchemaCommand(SchemaBaseCommand):
    """Export JSON schemas for all platform document kinds."""

    OPERATION = "schema_export"

    def __init__(
        self,
        output_dir: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._output_path = Path(output_dir)

    def _execute(self) -> bool:
        self._output_path.mkdir(parents=True, exist_ok=True)

        written: List[str] = []
        export_errors: List[str] = []
        for kind, model_cls in KIND_TO_MODEL.items():
            schema_file = self._output_path / f"{kind.value}.json"
            try:
                schema_file.write_text(json.dumps(model_cls.model_json_schema(), indent=2), encoding="utf-8")  # type: ignore[attr-defined]
                written.append(str(schema_file))
            except Exception as exc:
                export_errors.append(f"{kind.value}: {exc}")

        self._output_data = {
            "output_dir": str(self._output_path),
            "written": written,
            "written_count": len(written),
            "errors": export_errors,
        }
        self._errors.extend(export_errors)
        return len(export_errors) == 0

    def _after_execute(self) -> bool:
        if self._is_console_output():
            written = self._output_data.get("written", [])
            export_errors = self._output_data.get("errors", [])
            click.echo("")
            for path in written:
                click.echo(f"  Wrote: {path}")
            if export_errors:
                click.echo("")
                for entry in export_errors:
                    click.echo(f"  ERROR: {entry}", err=True)
            click.echo(
                f"\n  {self._output_data.get('written_count', 0)} schema(s) exported to {self._output_data.get('output_dir')}"
            )
            click.echo("")
        return super()._after_execute()
