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


@click.command(name="help", help="Show help information for XYZ Platform CLI.")
@click.argument("topic_name", type=str, required=False)
@click.pass_context
def help_command(ctx, topic_name: str = None):
    """Show help for the topic."""
    # ctx.parent.command is the main group
    # ctx.find_root().command is also the root command
    # command: TopicHelpCommand = TopicHelpCommand(
    #     topic=topic_name,
    #     cli_context=ctx,
    # )
    # command.execute()
    click.echo("XYZ Platform CLI - Help")
    click.echo("=======================")
    click.echo(f"Help for topic: {topic_name}")
