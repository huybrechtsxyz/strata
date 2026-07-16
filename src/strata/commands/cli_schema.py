"""Click CLI wiring for the ``schema`` command group."""

from typing import Optional

import click

from strata.commands.cli_common import click_output_format, click_work_path, handle_command_exit
from strata.commands.schemas.export_schema_command import ExportSchemaCommand
from strata.commands.schemas.get_schema_command import GetSchemaCommand
from strata.commands.schemas.list_schema_command import ListSchemaCommand
from strata.commands.schemas.wire_schema_command import WireSchemaCommand
from strata.utils.config import SOLUTION_DIR, SOLUTION_SCHEMAS_DIR


@click.group(name="schema", help="Inspect JSON schemas for platform YAML document kinds.")
def schema_group():
    """Schema command group."""


@schema_group.command(name="list", help="List all supported platform document kinds.")
@click_output_format
def schema_list(output: Optional[str] = None) -> None:
    """List all supported platform document kinds."""
    command = ListSchemaCommand(output=output)
    success = command.execute()
    handle_command_exit(command, success)


@schema_group.command(name="get", help="Emit the JSON Schema for a platform document kind.")
@click.argument("kind")
@click_output_format
def schema_get(kind: str, output: Optional[str] = None) -> None:
    """Emit the JSON Schema for a platform document kind (e.g. deployment, environment)."""
    command = GetSchemaCommand(kind=kind, output=output)
    success = command.execute()
    handle_command_exit(command, success)


@schema_group.command(name="export", help="Export JSON Schemas for all document kinds to files.")
@click.option(
    "--output-dir",
    default=f"{SOLUTION_DIR}/{SOLUTION_SCHEMAS_DIR}",
    show_default=True,
    help="Directory to write schema files. Created if it does not exist.",
)
def schema_export(output_dir: str) -> None:
    """Export JSON Schemas for all platform document kinds to individual files."""
    command = ExportSchemaCommand(output_dir=output_dir)
    success = command.execute()
    handle_command_exit(command, success)


@schema_group.command(name="wire", help="Wire JSON Schemas into .vscode/settings.json for YAML autocomplete.")
@click_work_path
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be written without making changes.",
)
def schema_wire(work_path: Optional[str], dry_run: bool) -> None:
    """Wire JSON schemas into VS Code settings for YAML completion."""
    command = WireSchemaCommand(work_path=work_path, dry_run=dry_run)
    success = command.execute()
    handle_command_exit(command, success)
