#!/usr/bin/env python3
"""
===============================================================================
Script Name   : cli_validate.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Validate command for the XYZ Platform CLI.
===============================================================================
"""

import click

from xyz_platform.commands.cli_common import (
    click_file,
    click_no_hooks,
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from xyz_platform.commands.validate.run_validate_command import RunValidateCommand


@click.command(name="validate", help="Validate any platform artifact file.")
@click_file
@click_work_path
@click_no_hooks
@click_output_format
@click_output_verbose
@click_output_quiet
def validate(
    file: str = None,
    work_path: str = None,
    no_hooks: bool = False,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """
    Validate any platform artifact file.

    The system automatically detects the file type (configuration, workspace,
    namespace, module, deployment, etc.) and validates it against the
    appropriate schema and cross-reference rules.

    Files are resolved relative to the workspace root (work-path), which is
    where repositories land after 'session fetch'.  Configuration for deep
    (dynamic) validation is loaded from the session state automatically.

    Examples:

    \b
      # Validate a workspace file
      xyz validate --file xyz_config/workspaces/xyz-platform.yaml

    \b
      # Validate a deployment file with JSON output
      xyz validate --file xyz_deploy/xyz-deploy.yaml --output json

    \b
      # Validate in a different workspace root
      xyz validate --file xyz_config/namespaces/xyz-base.yaml --work-path /my/workspace
    """
    command = RunValidateCommand(
        file=file,
        work_path=work_path,
        no_hooks=no_hooks,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
