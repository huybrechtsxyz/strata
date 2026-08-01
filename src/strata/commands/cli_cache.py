"""Click CLI wiring for the ``cache`` command group (ADR-0026)."""

from typing import Optional

import click

from strata.commands.cache.clear_cache_command import ClearCacheCommand
from strata.commands.cache.export_cache_command import ExportCacheCommand
from strata.commands.cache.status_cache_command import StatusCacheCommand
from strata.commands.cache.warm_cache_command import WarmCacheCommand
from strata.commands.cli_common import (
    click_file,
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)


@click.group(name="cache", help="Manage the resolved-model cache (ADR-0026).")
def cache_group():
    """Cache command group."""
    pass


@cache_group.command(name="warm", help="Resolve one (or all) deployments and store the result in the cache.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
@click_file
@click.option("--all", "warm_all", is_flag=True, default=False, help="Warm every deployment registered in the solution.")
def cache_warm(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
    file: Optional[str] = None,
    warm_all: bool = False,
) -> None:
    command = WarmCacheCommand(
        deployment_file=file,
        warm_all=warm_all,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@cache_group.command(name="status", help="Show cache freshness for one deployment, or list every cached entry.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
@click_file
def cache_status(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
    file: Optional[str] = None,
) -> None:
    command = StatusCacheCommand(
        deployment_file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@cache_group.command(name="clear", help="Remove every entry from the cache.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def cache_clear(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    command = ClearCacheCommand(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)


@cache_group.command(name="export", help="Write the full cache (decompressed) to a JSON file for debugging.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
@click.option("--path", "output_path", default="cache-export.json", metavar="FILE", help="Output JSON file path.")
def cache_export(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
    output_path: str = "cache-export.json",
) -> None:
    command = ExportCacheCommand(output_path=output_path, work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)
