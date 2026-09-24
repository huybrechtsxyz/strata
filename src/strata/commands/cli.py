#!/usr/bin/env python3
"""Command-line interface for strata.

Commands:
    validate   : Check every document in the solution.
    values     : Resolve deployment values (variables, secrets, feature flags).
    build      : Render a deployment's workspace into on-disk artifacts.
    version    : Show the strata version.

Exit codes are declared once in `strata.commands.exit_codes` — see there for
what each means.
"""

import click

from strata.commands.build_command import build_command
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE  # noqa: F401  (re-exported for callers)
from strata.commands.validate_command import validate_command
from strata.commands.values_command import values_command
from strata.utils.version import get_version


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=get_version(), prog_name="strata")
def cli() -> None:
    """strata — infrastructure as code platform."""


cli.add_command(validate_command)
cli.add_command(values_command)
cli.add_command(build_command)


@cli.command("version")
def version_command() -> None:
    """Show the strata version."""
    click.echo(get_version())


def main() -> None:
    """Console script entry point."""
    cli()
