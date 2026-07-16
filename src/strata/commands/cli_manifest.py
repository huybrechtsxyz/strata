"""Click CLI wiring for the deployment manifest command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_work_path,
    handle_command_exit,
)
from strata.commands.manifest.export_manifest_command import ExportManifestCommand
from strata.commands.manifest.list_manifest_command import ListManifestCommand
from strata.commands.manifest.show_manifest_command import ShowManifestCommand


@click.group(name="manifest", help="Query and export deployment manifests.")
def manifest_group() -> None:
    """Deployment manifest query commands."""


@manifest_group.command(name="list", help="List deployment manifests.")
@click.option(
    "--deployment",
    default=None,
    type=str,
    help="Filter by deployment name.",
)
@click.option(
    "--last",
    default=None,
    type=int,
    help="Show only the last N manifests.",
)
@click_work_path
@click_output_format
@click_output_quiet
def manifest_list(
    deployment: Optional[str],
    last: Optional[int],
    work_path: Optional[str],
    output: Optional[str],
    quiet: bool,
) -> None:
    """List deployment manifests from the configured manifest store."""
    command = ListManifestCommand(
        deployment=deployment,
        last=last,
        work_path=work_path,
        output=output,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@manifest_group.command(name="show", help="Show a specific deployment manifest.")
@click.argument("manifest_path", type=click.Path(exists=True))
@click_work_path
@click_output_format
@click_output_quiet
def manifest_show(
    manifest_path: str,
    work_path: Optional[str],
    output: Optional[str],
    quiet: bool,
) -> None:
    """Display the full content of a deployment manifest file."""
    command = ShowManifestCommand(
        manifest_path=manifest_path,
        work_path=work_path,
        output=output,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@manifest_group.command(name="export", help="Export manifests as a compliance evidence package.")
@click.option(
    "--deployment",
    default=None,
    type=str,
    help="Filter by deployment name.",
)
@click.option(
    "--last",
    default=None,
    type=int,
    help="Export only the last N manifests.",
)
@click.option(
    "--include-sbom",
    is_flag=True,
    default=False,
    help="Include referenced SBOM files in the export.",
)
@click.option(
    "--include-platform",
    is_flag=True,
    default=False,
    help="Include platform.json artifacts in the export.",
)
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(),
    help="Output directory for the evidence package.",
)
@click_work_path
@click_output_format
@click_output_quiet
def manifest_export(
    deployment: Optional[str],
    last: Optional[int],
    include_sbom: bool,
    include_platform: bool,
    out_dir: str,
    work_path: Optional[str],
    output: Optional[str],
    quiet: bool,
) -> None:
    """Export deployment manifests as a compliance evidence package."""
    command = ExportManifestCommand(
        out_dir=out_dir,
        deployment=deployment,
        last=last,
        include_sbom=include_sbom,
        include_platform=include_platform,
        work_path=work_path,
        output=output,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
