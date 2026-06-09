"""Click CLI wiring for the values command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.deploy.get_values_deploy_command import GetValuesDeployCommand
from strata.commands.deploy.list_values_deploy_command import ListValuesDeployCommand
from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand


@click.group(name="values", help="Inspect and manage deployment values (variables, secrets, feature flags).")
def values_group():
    """Values command group."""
    pass


@values_group.command(name="list")
@click.option(
    "--file",
    "-f",
    required=True,
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Path to the deployment YAML file. [env: STRATA_FILE]",
)
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Use the environment from this specific deployment stage (default: first stage).",
)
@click.option(
    "--type",
    "type_filter",
    default=None,
    type=click.Choice(["variables", "secrets", "features"]),
    metavar="TYPE",
    help="Show only this value type. Default: all.",
)
@click.option(
    "--show-store",
    "show_store",
    is_flag=True,
    default=False,
    help="Include the store reference (env var name, key path, etc.) in the output.",
)
@click.option(
    "--unresolved",
    "unresolved_only",
    is_flag=True,
    default=False,
    help="Show only entries that failed to resolve.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def values_list(
    file: str,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    type_filter: Optional[str] = None,
    show_store: bool = False,
    unresolved_only: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """List all variables, secrets, and feature flags for a deployment.

    Secrets are masked (first 3 chars + *****).
    Use ``strata values get`` to reveal a full value.
    """
    command = ListValuesDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        type_filter=type_filter,
        show_store=show_store,
        unresolved_only=unresolved_only,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@values_group.command(name="get")
@click.option(
    "--file",
    "-f",
    required=True,
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Path to the deployment YAML file. [env: STRATA_FILE]",
)
@click_work_path
@click.argument("keys", nargs=-1, required=True, metavar="KEY...")
@click_output_format
@click_output_verbose
@click_output_quiet
def values_get(
    file: str,
    keys: tuple,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Retrieve the full resolved value for one or more keys.

    Secrets are revealed in plain text — use with care.
    Provide one or more KEY arguments.
    """
    command = GetValuesDeployCommand(
        file=file,
        keys=list(keys),
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@values_group.command(name="set")
@click.option(
    "--file",
    "-f",
    required=True,
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Path to the deployment YAML file. [env: STRATA_FILE]",
)
@click_work_path
@click.option(
    "--key",
    "-k",
    required=True,
    metavar="KEY",
    help="The value key to update.",
)
@click.option(
    "--value",
    "-v",
    default=None,
    metavar="VALUE",
    help="The new value to set (mutually exclusive with --from-file and --stdin).",
)
@click.option(
    "--from-file",
    default=None,
    metavar="PATH",
    help="Read value from a file (supports multiline: certs, keys, etc.).",
)
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    default=False,
    help="Read value from stdin pipe.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def values_set(
    file: str,
    key: str,
    value: Optional[str] = None,
    from_file: Optional[str] = None,
    from_stdin: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Write a value to its configured store backend.

    For integration-backed stores (azure-keyvault, vault, consul, etc.),
    writes directly via the integration. For 'constant' and 'environment'
    stores, prints instructions on where/how to set the value.

    Multiline values (SSH keys, certificates) use --from-file or --stdin:

        strata values set -f deploy.yaml -k TLS_CERT --from-file cert.pem
        cat key.pem | strata values set -f deploy.yaml -k SSH_KEY --stdin
    """
    command = SetValuesDeployCommand(
        file=file,
        key=key,
        value=value,
        from_file=from_file,
        from_stdin=from_stdin,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@values_group.command(name="resolve")
@click.option(
    "--file",
    "-f",
    required=True,
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Path to the deployment YAML file. [env: STRATA_FILE]",
)
@click_work_path
@click.option(
    "--key",
    "-k",
    default=None,
    metavar="KEY",
    help="Diagnose a single key only (default: all).",
)
@click.option(
    "--probe",
    is_flag=True,
    default=False,
    help="Also attempt actual resolution against store backends (without revealing values).",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def values_resolve(
    file: str,
    key: Optional[str] = None,
    probe: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Diagnose value resolution paths without revealing values.

    Walks the resolution chain for each key and reports whether each step
    would succeed: store type, integration registration, availability, and
    optionally (with --probe) actual backend reachability.

        strata values resolve -f deploy.yaml
        strata values resolve -f deploy.yaml -k DB_PASSWORD
        strata values resolve -f deploy.yaml --probe
    """
    command = ResolveValuesDeployCommand(
        file=file,
        key=key,
        probe=probe,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
