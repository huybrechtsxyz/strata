#!/usr/bin/env python3
"""Command-line interface for strata.

Commands:
    version : Show the strata version.

Exit codes (v1-compatible, so existing pipelines keep working):
    0 : Success
    1 : System/execution failure
    2 : Usage error — invalid CLI arguments (click's standard)
    3 : Validation failure — input processed but invalid
"""

import click

from strata.utils.version import get_version

#: Process exit codes. Declared once so commands cannot invent their own.
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_VALIDATION = 3


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=get_version(), prog_name="strata")
def cli() -> None:
    """strata — infrastructure as code platform."""


@cli.command("version")
def version_command() -> None:
    """Show the strata version."""
    click.echo(get_version())


def main() -> None:
    """Console script entry point."""
    cli()
