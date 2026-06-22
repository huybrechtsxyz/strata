"""Click CLI wiring for the validate command."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_file,
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.validate.run_validate_command import ValidateCommand


@click.command(name="validate")
@click_file
@click.option(
    "--path",
    "-p",
    default=None,
    metavar="GLOB",
    help=(
        "Glob pattern to select multiple deployment manifests for cross-manifest overlap validation. "
        "Resolved relative to the workspace root against the active profile's configfile_paths. "
        "Requires an initialized workspace with an active profile. "
        "Example: 'deployments/**' or 'deployments/acme-*'"
    ),
)
@click.option(
    "--deep",
    is_flag=True,
    default=False,
    help=(
        "Enable Phase 2 (cross-reference) validation against the active profile's "
        "configuration sources. Requires an initialized workspace with an active profile. "
        "Fails with exit code 1 if the configuration cannot be loaded."
    ),
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def validate_command(
    file: Optional[str] = None,
    path: Optional[str] = None,
    deep: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Validate a platform YAML file against its kind-specific schema.

    Single-file validation (requires -f / --file):

        strata validate -f config/deployment.yaml

    Cross-manifest overlap validation (requires --path glob):

        strata validate --path "deployments/**"
        strata validate --path "deployments/acme-*"
    """
    if not file and not path:
        raise click.UsageError("Specify a single file with '-f' / '--file', or a glob with '--path' / '-p'.")
    command = ValidateCommand(
        file=file,
        path=path,
        deep=deep,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
