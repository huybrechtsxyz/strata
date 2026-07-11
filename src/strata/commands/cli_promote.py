"""Click CLI wiring for the ``promote`` command group.

Subcommands
-----------
start    — Initiate or advance a promotion wave (creates branch, writes lock, commits).
rollback — Reverse a promotion using the same strategy.
status   — Show in-flight promotions from local activity logs.
matrix   — Show version matrix across all rings from lock files.
history  — Query completed promotion records.
log      — Show activity log for a specific promotion.
"""

from __future__ import annotations

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.promote.history_promote_command import HistoryPromoteCommand
from strata.commands.promote.log_promote_command import LogPromoteCommand
from strata.commands.promote.matrix_promote_command import MatrixPromoteCommand
from strata.commands.promote.rollback_promote_command import RollbackPromoteCommand
from strata.commands.promote.start_promote_command import StartPromoteCommand
from strata.commands.promote.status_promote_command import StatusPromoteCommand


# ── group ─────────────────────────────────────────────────────────────────────


@click.group(name="promote", help="Manage version promotions across rings.")
def promote_group() -> None:
    """Promote command group."""


# ── start ──────────────────────────────────────────────────────────────────────


@promote_group.command(
    name="start",
    help=(
        "Initiate or advance a promotion wave.\n\n"
        "Creates branch promote/{target}-{version}-{ring}, writes the appropriate "
        "version-lock file(s), commits, and appends to the activity log. "
        "On the final wave also writes a promotion-record audit artifact.\n\n"
        "Exit codes: 0=success, 1=system error, 2=usage error, 3=gate/validation failure."
    ),
)
@click.option("--remote", metavar="NAME", default=None, help="Remote name being promoted (cannot use with: --module).")
@click.option("--module", metavar="NAME", default=None, help="Module name being promoted (cannot use with: --remote).")
@click.option("--version", "-v", required=True, metavar="VERSION", help="Target version (required).")
@click.option("--to", required=True, metavar="RING", help="Target ring name, e.g. prd (required).")
@click.option(
    "--wave",
    metavar="WAVE",
    default=None,
    help="Ring wave (integer, e.g. 1) or deployment wave (name, e.g. canary). Defaults to all environments in ring.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show plan without making changes.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def promote_start(
    remote: Optional[str],
    module: Optional[str],
    version: str,
    to: str,
    wave: Optional[str],
    dry_run: bool,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    cmd = StartPromoteCommand(
        remote=remote,
        module=module,
        version=version,
        to=to,
        wave=wave,
        dry_run=dry_run,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = cmd.execute()
    handle_command_exit(cmd, success)


# ── rollback ──────────────────────────────────────────────────────────────────


@promote_group.command(
    name="rollback",
    help=(
        "Reverse a promotion using the same strategy.\n\n"
        "Resolves previous_version from: activity log → git merge-base → --from-version.\n\n"
        "Exit codes: 0=success, 1=system error, 2=usage error, 3=validation failure."
    ),
)
@click.option("--remote", metavar="NAME", default=None, help="Remote name to roll back (cannot use with: --module).")
@click.option("--module", metavar="NAME", default=None, help="Module name to roll back (cannot use with: --remote).")
@click.option("--to", required=True, metavar="RING", help="Ring to roll back in (required).")
@click.option(
    "--from-version",
    metavar="VERSION",
    default=None,
    help="Explicit previous version (escape hatch for CI / shallow clones).",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show plan without making changes.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def promote_rollback(
    remote: Optional[str],
    module: Optional[str],
    to: str,
    from_version: Optional[str],
    dry_run: bool,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    cmd = RollbackPromoteCommand(
        remote=remote,
        module=module,
        to=to,
        from_version=from_version,
        dry_run=dry_run,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = cmd.execute()
    handle_command_exit(cmd, success)


# ── status ────────────────────────────────────────────────────────────────────


@promote_group.command(
    name="status",
    help=(
        "Show in-flight promotions from the local activity log directory.\n\n"
        "Exit codes: 0=success, 1=system error."
    ),
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def promote_status(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    cmd = StatusPromoteCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = cmd.execute()
    handle_command_exit(cmd, success)


# ── matrix ────────────────────────────────────────────────────────────────────


@promote_group.command(
    name="matrix",
    help=(
        "Show version matrix across all rings.\n\n"
        "Reads versions/<ring>.yaml lock files directly — no fleet traversal needed.\n\n"
        "Exit codes: 0=success, 1=system error."
    ),
)
@click.option("--remote", metavar="NAME", default=None, help="Filter matrix to this remote name.")
@click.option("--module", metavar="NAME", default=None, help="Filter matrix to this module name.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def promote_matrix(
    remote: Optional[str] = None,
    module: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    cmd = MatrixPromoteCommand(
        remote=remote,
        module=module,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = cmd.execute()
    handle_command_exit(cmd, success)


# ── history ───────────────────────────────────────────────────────────────────


@promote_group.command(
    name="history",
    help=(
        "Query completed promotion records.\n\n"
        "Exit codes: 0=success, 1=system error."
    ),
)
@click.option("--ring", metavar="NAME", default=None, help="Filter to this ring.")
@click.option("--remote", metavar="NAME", default=None, help="Filter to this remote name.")
@click.option("--module", metavar="NAME", default=None, help="Filter to this module name.")
@click.option("--last", metavar="INT", default=10, show_default=True, type=int, help="Maximum records to show.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def promote_history(
    ring: Optional[str] = None,
    remote: Optional[str] = None,
    module: Optional[str] = None,
    last: int = 10,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    cmd = HistoryPromoteCommand(
        ring=ring,
        remote=remote,
        module=module,
        last=last,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = cmd.execute()
    handle_command_exit(cmd, success)


# ── log ───────────────────────────────────────────────────────────────────────


@promote_group.command(
    name="log",
    help=(
        "Show activity log for a specific promotion.\n\n"
        "Activity logs are local-only (gitignored) — only available on the machine that ran promote start.\n\n"
        "Exit codes: 0=success, 1=system error, 3=not found."
    ),
)
@click.option("--remote", metavar="NAME", default=None, help="Remote name (cannot use with: --module).")
@click.option("--module", metavar="NAME", default=None, help="Module name (cannot use with: --remote).")
@click.option("--to", required=True, metavar="RING", help="Ring name (required).")
@click.option("--version", "-v", metavar="VERSION", default=None, help="Specific version (default: most recent).")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def promote_log(
    remote: Optional[str] = None,
    module: Optional[str] = None,
    to: str = "",
    version: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    cmd = LogPromoteCommand(
        remote=remote,
        module=module,
        to=to,
        version=version,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = cmd.execute()
    handle_command_exit(cmd, success)
