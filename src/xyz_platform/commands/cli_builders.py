#!/usr/bin/env python3
"""
===============================================================================
Script Name   : cli_builders.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Click CLI wiring for build command group.
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
from xyz_platform.commands.builders.clean_build_command import CleanBuildCommand
from xyz_platform.commands.builders.run_build_command import RunBuildCommand


@click.group(name="build", help="Build platform and Terraform artifacts.")
def build():
    """Build command group."""
    pass


@build.command(name="run", help="Run platform + terraform build pipeline.")
@click_file
@click_work_path
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate and plan the build without writing any output files.",
)
@click_no_hooks
@click_output_format
@click_output_verbose
@click_output_quiet
def build_run(
    file: str = None,
    work_path: str = None,
    dry_run: bool = False,
    no_hooks: bool = False,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """Execute the build pipeline."""
    command = RunBuildCommand(
        file=file,
        work_path=work_path,
        dry_run=dry_run,
        no_hooks=no_hooks,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@build.command(name="clean", help="Clean deployment build artifacts.")
@click_file
@click_work_path
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show which path would be cleaned without deleting files.",
)
@click_no_hooks
@click_output_format
@click_output_verbose
@click_output_quiet
def build_clean(
    file: str = None,
    work_path: str = None,
    dry_run: bool = False,
    no_hooks: bool = False,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """Clean build artifacts for the selected deployment."""
    command = CleanBuildCommand(
        file=file,
        work_path=work_path,
        dry_run=dry_run,
        no_hooks=no_hooks,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
