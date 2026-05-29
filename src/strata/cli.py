"""Command-line interface for Strata.

Commands:
    help                : Show help topics and workflow guidance.
    sln                 : Manage solution workspace lifecycle (init, clean, status, export).
    config              : Manage workspace defaults (cli.yaml).
    log                 : View log entries and manage logging config.
    repo                : Manage repositories in the solution.
    profile             : Manage profiles in the solution.
    ref                 : Manage file references (env, config, data, secret types).
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

from strata.commands.cli_builders import build as build_group
from strata.commands.cli_config import config_group
from strata.commands.cli_deploy import deploy as deploy_group
from strata.commands.cli_diff import diff_command
from strata.commands.cli_help import help_command
from strata.commands.cli_log import log_group
from strata.commands.cli_new import new_command
from strata.commands.cli_profile import profile_group
from strata.commands.cli_ref import ref_group
from strata.commands.cli_repo import repo_group
from strata.commands.cli_schema import schema_group
from strata.commands.cli_sln import sln_group
from strata.commands.cli_tools import tools_group
from strata.commands.cli_validate import validate_command
from strata.commands.cli_values import values_group
from strata.commands.cli_vars import vars_group
from strata.commands.cli_version import version_command
from strata.logger import configure_logging, get_logger, shutdown_logging
from strata.utils import system
from strata.utils.config import SOLUTION_CONFIG_FILE, SOLUTION_DIR
from strata.utils.integration_loader import load_workspace_integrations
from strata.utils.system import resolve_work_path

logger = get_logger(__name__)

_CONFIG_FILE = SOLUTION_CONFIG_FILE
_DEFAULT_MAP_KEYS = ("output", "verbose", "quiet", "work_path", "file")


def _load_workspace_defaults(work_path: Path) -> dict:
    """
    Load persistent CLI defaults from ``<work_path>/.strata/cli.yaml``.

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
    the correct ``.strata/cli.yaml`` regardless of where ``--work-path`` appears
    in the command line.

    Resolution order (first match wins):
    1. ``--work-path <value>`` or ``--work-path=<value>`` anywhere in sys.argv
    2. ``STRATA_WORK_PATH`` environment variable
    3. Walk up from CWD (``resolve_work_path`` default)
    """
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg in ("--work-path",) and i + 1 < len(args):
            return resolve_work_path(args[i + 1])
        if arg.startswith("--work-path="):
            return resolve_work_path(arg.split("=", 1)[1])
    return resolve_work_path(os.environ.get("STRATA_WORK_PATH"))


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
    help=("Strata CLI.\n\nAutomates workspace preparation, configuration, and deployment for the Strata platform.\n\n"),
    context_settings={
        "help_option_names": ["-h", "--help"],
        "auto_envvar_prefix": "STRATA",
    },
)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Strata CLI entry point."""
    # Force UTF-8 on Windows so emoji and box-drawing characters survive
    # piping/redirection (e.g. `strata validate ... | Select-String`).
    # reconfigure() is a no-op on non-Windows or when stdout is already UTF-8.
    # Must run before any output — including Click's own help text.
    if sys.platform == "win32":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    # Fallback to WARNING level console logging
    logging_config = system.get_pkg_logging_path()
    if logging_config.exists():
        configure_logging(config_path=str(logging_config))
    else:
        configure_logging(level="WARNING", enable_console=True)

    # Load workspace defaults from cli.yaml and apply as Click default_map.
    # Resolution order: explicit flag > STRATA_* env var > cli.yaml > built-in default.
    work_path = _resolve_work_path_early()
    workspace_defaults = _load_workspace_defaults(work_path)
    if workspace_defaults:
        ctx.ensure_object(dict)
        ctx.default_map = _build_default_map(ctx.command, workspace_defaults)
        logger.debug("Workspace values loaded", values=workspace_defaults)

    # Load workspace-local integration drop-ins (.strata/integrations/*.py)
    try:
        load_workspace_integrations(work_path)
    except Exception as exc:
        logger.debug("Workspace integration loader error (non-fatal)", error=str(exc))


#
# REGISTER COMMAND GROUPS
# Register command groups so they're available when module is imported
#

main.add_command(version_command, name="version")
main.add_command(help_command, name="help")
main.add_command(sln_group, name="sln")
main.add_command(config_group, name="config")
main.add_command(vars_group, name="vars")
main.add_command(new_command, name="new")
main.add_command(log_group, name="log")
main.add_command(repo_group, name="repo")
main.add_command(profile_group, name="profile")
main.add_command(ref_group, name="ref")
main.add_command(validate_command, name="validate")
main.add_command(schema_group, name="schema")
main.add_command(build_group, name="build")
main.add_command(deploy_group, name="deploy")
main.add_command(diff_command, name="diff")
main.add_command(values_group, name="values")
main.add_command(tools_group, name="tools")
# ENTRY POINT
#

if __name__ == "__main__":
    try:
        main()
    except click.UsageError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo(click.Context(main).get_help())
        exit(2)
    except click.ClickException as e:
        e.show()
        exit(e.exit_code)
    except Exception as e:
        from strata.exceptions import PlatformError
        from strata.utils.config import SUPPORT_URL
        from strata.utils.version import get_version

        if isinstance(e, PlatformError):
            click.echo(f"❌ {e}", err=True)
        else:
            click.echo(
                f"❌ Unexpected error: {e}\n"
                f"   Command: {' '.join(sys.argv)}\n"
                f"   Version: {get_version()}\n"
                f"   Please report at: {SUPPORT_URL}",
                err=True,
            )
        exit(1)
    finally:
        # Ensure logs are flushed before exit
        shutdown_logging()
