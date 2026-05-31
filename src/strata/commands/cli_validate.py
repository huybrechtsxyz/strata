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
    deep: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Validate a platform YAML file against its kind-specific schema.

    Specify the file with -f / --file (or set STRATA_FILE):

        strata validate -f config/deployment.yaml
    """
    if not file:
        raise click.UsageError("Missing option '-f' / '--file'. Specify the deployment YAML file path.")
    command = ValidateCommand(
        file=file,
        deep=deep,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
