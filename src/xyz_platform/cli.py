#!/usr/bin/env python3
"""
===============================================================================
Script Name   : cli.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command-line interface for XYZ Platform.

Commands:
    -h, --help          : Show this help message and exit.
    config              : Manage configuration settings.
    help [TOPIC_NAME]   : Show help for a specific topic or list available topics.
    session             : Manage and view session information.
    tools               : Tools management commands.
    validate            : Validate platform definition file(s).
    version             : Show CLI version.

Exit Codes:
    0                   : Success - operation completed successfully
    1                   : System/execution failure - crashes, missing files, initialization errors
    2                   : Usage error - invalid CLI arguments, missing options (Click standard)
    3                   : Validation failure - file processed but invalid (validate command only)

Usage:
    python cli.py [COMMAND] [OPTIONS]
===============================================================================
"""

from pathlib import Path
import sys
import os
import click

from xyz_platform.commands.cli_help import help_command
from xyz_platform.commands.cli_version import version_command
from xyz_platform.commands.cli_session import session
from xyz_platform.commands.cli_tools import tools
from xyz_platform.logger.logger import configure_logging, get_logger

# from xyz_platform.commands.cli_builder import build
# from xyz_platform.commands.cli_builder import build
# from xyz_platform.commands.cli_deploy import deploy
# from xyz_platform.commands.help.topic_help_command import TopicHelpCommand
# from xyz_platform.commands.cli_config import config
# from xyz_platform.commands.cli_validate import validate
# from xyz_platform.logger import get_logger, configure_logging, shutdown_logging
# from xyz_platform.utils import system

logger = get_logger(__name__)

#
# MAIN CLI GROUP
#


@click.group(
    name="main",
    help=(
        "XYZ Platform CLI.\n\n"
        "Automates workspace preparation, configuration, and deployment for the XYZ Platform.\n\n"
    ),
    context_settings={"help_option_names": ["-h", "--help"]},
)
def main():
    """XYZ Platform CLI entry point."""
    # Fallback to WARNING level console logging
    # INFO/DEBUG logs only shown when commands use --verbose flag
    configure_logging(level="WARNING", enable_console=True)
    logger.debug("Using default WARNING level console logging")
    logger.debug("XYZ Platform CLI initialized")


#
# REGISTER COMMAND GROUPS
# Register command groups so they're available when module is imported
#

main.add_command(help_command)
main.add_command(version_command)
main.add_command(session)
main.add_command(tools)
# main.add_command(config)
# main.add_command(validate)
# main.add_command(build)
# main.add_command(deploy)


#
# ENTRY POINT
#

if __name__ == "__main__":
    try:
        # Force UTF-8 encoding for Windows console to support emoji characters
        if sys.platform == "win32":
            # Set console to UTF-8 mode
            os.environ["PYTHONIOENCODING"] = "utf-8"
            # Reconfigure stdout/stderr for UTF-8
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")

        main()
    except click.UsageError as e:
        # logger.error(f"Usage error: {e}")
        click.echo(f"Error: {e}", err=True)
        click.echo(click.Context(main).get_help())
        exit(2)
    except click.ClickException as e:
        # logger.error(f"CLI error: {e}", exc_info=True)
        click.echo(f"Unexpected Error:", err=True)
        e.show()
        exit(e.exit_code)
    except Exception as e:
        # logger.exception(f"Unexpected error in CLI")
        click.echo(f"Unexpected error: {e}", err=True)
        import traceback

        traceback.print_exc()
        exit(1)
    finally:
        # Ensure logs are flushed before exit
        # shutdown_logging()
        pass
