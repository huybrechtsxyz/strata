#!/usr/bin/env python3
"""Command-line interface for strata.

Commands:
    version : Show the strata version.

Exit codes are declared once in `strata.commands.exit_codes` — see there for
what each means.
"""

import click

from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE  # noqa: F401  (re-exported for callers)
from strata.utils.version import get_version


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
