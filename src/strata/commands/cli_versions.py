"""Click CLI wiring for the ``versions`` command group.

Subcommands
-----------
add     — Create a new version-manifest snapshot (optionally copied from an existing file).
init    — Scaffold a starter version-manifest (kind: version) file for a ring.
lock    — Compute spec.hash over pins and write it back into the version file.
export  — Print the resolved flat pin state from a version file.
apply   — Convert a version-manifest into a version-lock file (old-style inline pins).
refresh — Sync a manifest against discovered targets in the workspace.
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
from strata.commands.versions.add_versions_command import AddVersionsCommand
from strata.commands.versions.apply_versions_command import ApplyVersionsCommand
from strata.commands.versions.export_versions_command import ExportVersionsCommand
from strata.commands.versions.init_versions_command import InitVersionsCommand
from strata.commands.versions.lock_versions_command import LockVersionsCommand
from strata.commands.versions.refresh_versions_command import RefreshVersionsCommand

# ── group ─────────────────────────────────────────────────────────────────────


@click.group(name="versions", help="Manage version manifests and version locks.")
def versions_group() -> None:
    """Versions command group."""


# ── init ──────────────────────────────────────────────────────────────────────


@versions_group.command(name="init", help="Scaffold a starter version-manifest file for a ring.")
@click.option("--ring", "-r", required=True, metavar="NAME", help="Ring name (e.g. dev, prd).")
@click.option(
    "--out",
    "-o",
    default=None,
    metavar="PATH",
    help="Output path. Defaults to versions/<ring>.yaml relative to work-path.",
)
@click.option("--force", is_flag=True, default=False, help="Overwrite if the file already exists.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_init(
    ring: str,
    out: Optional[str],
    force: bool,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Scaffold a starter version-manifest file for a ring."""
    command = InitVersionsCommand(
        ring=ring,
        out=out,
        force=force,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ── export ─────────────────────────────────────────────────────────────────────


@versions_group.command(
    name="export",
    help="Print the resolved flat pin state from a version-manifest or version-lock file.",
)
@click.option(
    "--file",
    "-f",
    required=True,
    metavar="PATH",
    help="Path to a version-manifest (kind: version) or version-lock (kind: version-lock) file.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_export(
    file: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Print the resolved flat pin state."""
    command = ExportVersionsCommand(
        file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ── apply ──────────────────────────────────────────────────────────────────────


@versions_group.command(
    name="apply",
    help="Convert a version-manifest (kind: version) into a version-lock (kind: version-lock) file.",
)
@click.option(
    "--file",
    "-f",
    required=True,
    metavar="PATH",
    help="Path to the version-manifest (kind: version) YAML file.",
)
@click.option(
    "--out",
    "-o",
    default=None,
    metavar="PATH",
    help="Output path for the lock file. Defaults to <stem>.lock.yaml alongside input.",
)
@click.option("--force", is_flag=True, default=False, help="Overwrite existing lock file.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_apply(
    file: str,
    out: Optional[str],
    force: bool,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Convert a version-manifest into a version-lock file."""
    command = ApplyVersionsCommand(
        file=file,
        out=out,
        force=force,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ── refresh ────────────────────────────────────────────────────────────────────


@versions_group.command(
    name="refresh",
    help=(
        "Sync a version-manifest against versionable targets discovered in the workspace.\n\n"
        "Scans for kind:module and kind:workspace YAML files and compares them to the "
        "manifest's current pins.  New targets are added (with their current version as a "
        "seed value).  Targets no longer found are reported; pass --remove-stale to also "
        "delete them from the manifest."
    ),
)
@click.option(
    "--file",
    "-f",
    required=True,
    metavar="PATH",
    help="Path to the version-manifest (kind: version) file to update.",
)
@click.option(
    "--scan",
    "-d",
    default=None,
    metavar="PATH",
    help="Directory to scan for module/workspace YAML files. Defaults to work-path.",
)
@click.option(
    "--remove-stale",
    "remove_stale",
    is_flag=True,
    default=False,
    help="Remove manifest entries whose targets were not found during the scan.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Show what would change without writing the file.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_refresh(
    file: str,
    scan: Optional[str],
    remove_stale: bool,
    dry_run: bool,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Sync a version-manifest against discovered workspace targets."""
    command = RefreshVersionsCommand(
        file=file,
        scan=scan,
        remove_stale=remove_stale,
        dry_run=dry_run,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ── add ──────────────────────────────────────────────────────────────────────


@versions_group.command(
    name="add",
    help=(
        "Create a new version-manifest snapshot file (Layer 1).\n\n"
        "When --from is given, pins are copied from that file as a starting point. "
        "After reviewing the pins, run 'strata versions lock' to compute a tamper-evident hash."
    ),
)
@click.option(
    "--out",
    "-o",
    required=True,
    metavar="PATH",
    help="Output path for the new version file (e.g. versions/customer/v2.1.0.yaml).",
)
@click.option(
    "--ring",
    "-r",
    required=True,
    metavar="NAME",
    help="Ring name this version snapshot covers (e.g. dev, prd).",
)
@click.option(
    "--from",
    "from_file",
    default=None,
    metavar="PATH",
    help="Copy pins from this existing version-manifest file.",
)
@click.option("--force", is_flag=True, default=False, help="Overwrite if the file already exists.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_add(
    out: str,
    ring: str,
    from_file: Optional[str],
    force: bool,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Create a new version-manifest snapshot."""
    command = AddVersionsCommand(
        out=out,
        ring=ring,
        from_file=from_file,
        force=force,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ── lock ──────────────────────────────────────────────────────────────────────


@versions_group.command(
    name="lock",
    help=(
        "Compute a SHA-256 hash over spec.pins and write it to spec.hash in the file (Layer 2).\n\n"
        "Locking makes the file tamper-evident. A locked file that has been modified "
        "will fail integrity checks at deploy time."
    ),
)
@click.option(
    "--file",
    "-f",
    required=True,
    metavar="PATH",
    help="Path to the version-manifest (kind: version) YAML file to lock.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_lock(
    file: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Compute and write spec.hash for a version-manifest."""
    command = LockVersionsCommand(
        file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
