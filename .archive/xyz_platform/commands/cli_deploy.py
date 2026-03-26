#!/usr/bin/env python3
"""
===============================================================================
Script Name   : cli_deploy.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Click CLI wiring for deploy command group.
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
from xyz_platform.commands.deploy.run_deploy_command import RunDeployCommand


@click.group(name="deploy", help="Deploy platform using provisioners.")
def deploy():
    """Deploy command group."""
    pass


@deploy.command(name="run", help="Run the deploy pipeline for a deployment definition.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit execution to a specific deployment stage by name.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip interactive confirmation prompts and approval gates.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate and plan the deploy without running any provisioners.",
)
@click.option(
    "--destroy",
    is_flag=True,
    default=False,
    help="Destroy provisioned infrastructure (TODO: not yet implemented).",
)
@click_no_hooks
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_run(
    file: str = None,
    work_path: str = None,
    stage: str = None,
    force: bool = False,
    dry_run: bool = False,
    destroy: bool = False,
    no_hooks: bool = False,
    output: str = None,
    verbose: bool = None,
    quiet: bool = None,
):
    """Execute the deploy pipeline."""
    command = RunDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        force=force,
        dry_run=dry_run,
        destroy=destroy,
        no_hooks=no_hooks,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
