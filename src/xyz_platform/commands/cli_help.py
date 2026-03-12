"""
===============================================================================
Script Name   : cli_session.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Click CLI wiring for session command group.
===============================================================================
"""

import click

from xyz_platform.commands.help.help_command import HelpCommand


@click.command(name="help", help="Show help information for XYZ Platform CLI.")
@click.argument(
    "topic_name",
    type=str,
    required=False,
    # help="Name of the help topic to display. If not provided, shows general help.",
)
@click.pass_context
def help_command(ctx, topic_name: str = None):
    """Show help for the topic."""
    # ctx.parent.command is the main group
    # ctx.find_root().command is also the root command
    command: HelpCommand = HelpCommand(
        topic=topic_name,
        cli_context=ctx,
    )
    command.execute()
