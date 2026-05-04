"""Command-line interface for XYZ Platform.

Commands:
    config              : Manage configuration settings.
    help [TOPIC_NAME]   : Show help for a specific topic or list available topics.
    session             : Manage and view session information.
    tools               : Tools management commands.
    validate            : Validate platform definition file(s).
    version             : Show CLI version.

Exit Codes:
    0  : Success
    1  : System/execution failure (crash, missing files, init errors)
    2  : Usage error - invalid CLI arguments (Click standard)
    3  : Validation failure - file processed but invalid
"""

import os
import sys
from pathlib import Path

import click
import yaml

from xyz_platform.commands.cli_config import config_group
from xyz_platform.commands.cli_log import log_group
from xyz_platform.commands.cli_solution import solution_group
from xyz_platform.commands.cli_version import version_command
from xyz_platform.logger import configure_logging, get_logger, shutdown_logging
from xyz_platform.utils import system
from xyz_platform.utils.config import SOLUTION_CONFIG_FILE, SOLUTION_DIR
from xyz_platform.utils.system import resolve_work_path

logger = get_logger(__name__)

_CONFIG_FILE = SOLUTION_CONFIG_FILE
_DEFAULT_MAP_KEYS = ("output", "verbose", "quiet", "work_path")


def _load_workspace_defaults(work_path: Path) -> dict:
    """
    Load persistent CLI defaults from ``<work_path>/.platform/cli.yaml``.

    Returns an empty dict when the file is absent or unreadable, so the
    caller can always safely pass the result to Click's ``default_map``.
    """
    config_path = work_path / SOLUTION_DIR / _CONFIG_FILE
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        values = raw.get("values", {})
        # Only keep keys that map to actual CLI options
        return {k: v for k, v in values.items() if k in _DEFAULT_MAP_KEYS}
    except Exception as exc:
        logger.debug(f"Could not load workspace config defaults: {exc}")
        return {}


def _resolve_work_path_early() -> Path:
    """Resolve work_path before Click finishes parsing subcommand options.

    Click's ``main()`` callback fires before subcommand options are parsed, so
    ``--work-path`` values on leaf commands are not yet available via the context.
    We peek at ``sys.argv`` directly so that workspace defaults are loaded from
    the correct ``.platform/cli.yaml`` regardless of where ``--work-path`` appears
    in the command line.

    Resolution order (first match wins):
    1. ``--work-path <value>`` or ``--work-path=<value>`` anywhere in sys.argv
    2. ``XYZ_WORK_PATH`` environment variable
    3. Walk up from CWD (``resolve_work_path`` default)
    """
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg in ("--work-path",) and i + 1 < len(args):
            return resolve_work_path(args[i + 1])
        if arg.startswith("--work-path="):
            return resolve_work_path(arg.split("=", 1)[1])
    return resolve_work_path(os.environ.get("XYZ_WORK_PATH"))


def _build_default_map(command: click.Command, defaults: dict) -> dict:
    """Recursively build a Click default_map that reaches every leaf command.

    Click only applies ``default_map`` values to the *current* command's
    options.  For nested groups (e.g. ``solution repo add``) the map must be
    nested two levels deep.  This helper walks the full command tree and
    injects ``defaults`` at every level so leaf options are always covered.
    """
    result: dict = {}
    commands = getattr(command, "commands", {})
    for name, sub in commands.items():
        entry: dict = dict(defaults)  # apply defaults at this level too
        sub_commands = getattr(sub, "commands", {})
        if sub_commands:
            entry.update(_build_default_map(sub, defaults))
        result[name] = entry
    return result


#
# MAIN CLI GROUP
#


@click.group(
    name="main",
    help=(
        "XYZ Platform CLI.\n\nAutomates workspace preparation, configuration, and deployment for the XYZ Platform.\n\n"
    ),
    context_settings={
        "help_option_names": ["-h", "--help"],
        "auto_envvar_prefix": "XYZ",
    },
)
@click.pass_context
def main(ctx: click.Context) -> None:
    """XYZ Platform CLI entry point."""
    # Fallback to WARNING level console logging
    logging_config = system.get_pkg_logging_path()
    if logging_config.exists():
        configure_logging(config_path=str(logging_config))
    else:
        configure_logging(level="WARNING", enable_console=True)

    # Load workspace defaults from cli.yaml and apply as Click default_map.
    # Resolution order: explicit flag > XYZ_* env var > cli.yaml > built-in default.
    work_path = _resolve_work_path_early()
    workspace_defaults = _load_workspace_defaults(work_path)
    if workspace_defaults:
        ctx.ensure_object(dict)
        ctx.default_map = _build_default_map(ctx.command, workspace_defaults)
        logger.debug("Workspace values loaded", values=workspace_defaults)


#
# REGISTER COMMAND GROUPS
# Register command groups so they're available when module is imported
#

main.add_command(version_command, name="version")
main.add_command(solution_group, name="solution")
main.add_command(config_group, name="config")
main.add_command(log_group, name="log")

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
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

        main()
    except click.UsageError as e:
        # logger.error(f"Usage error: {e}")
        click.echo(f"Error: {e}", err=True)
        click.echo(click.Context(main).get_help())
        exit(2)
    except click.ClickException as e:
        # logger.error(f"CLI error: {e}", exc_info=True)
        click.echo(f"Unexpected Error: {e}", err=True)
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
        shutdown_logging()
