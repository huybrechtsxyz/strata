#!/usr/bin/env python3
"""Command-line interface for strata.

Commands:
    validate   : Check every document in the solution.
    graph      : Show every cross-document reference in the solution.
    values     : Resolve deployment values (variables, secrets, feature flags).
    build      : Render a deployment's workspace into on-disk artifacts.
    deploy     : Execute a deployment's workspace provisioners (plan/apply).
    audit      : Read back audit trail records from a configured durable sink.
    sln        : Scaffold or refresh a solution's strata-owned files (init/update).
    path       : Resolve a document's (kind, name) to its real file path.
    version    : Show the strata version, or manage kind: version documents (new/update/set).
    promote    : Inspect or mutate a workspace's ring/order/wave rollout (status/apply/view).

Exit codes are declared once in `strata.commands.exit_codes` — see there for
what each means.
"""

import click

from strata.commands.audit_command import audit_command
from strata.commands.build_command import build_command
from strata.commands.deploy_command import deploy_command
from strata.commands.exit_codes import EXIT_SUCCESS, EXIT_USAGE  # noqa: F401  (re-exported for callers)
from strata.commands.graph_command import graph_command
from strata.commands.path_command import path_command
from strata.commands.promote_command import promote_command
from strata.commands.sln_command import sln_command
from strata.commands.validate_command import validate_command
from strata.commands.values_command import values_command
from strata.commands.version_command import version_command
from strata.utils.version import get_version


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=get_version(), prog_name="strata")
def cli() -> None:
    """strata — infrastructure as code platform."""


cli.add_command(validate_command)
cli.add_command(graph_command)
cli.add_command(values_command)
cli.add_command(build_command)
cli.add_command(deploy_command)
cli.add_command(audit_command)
cli.add_command(sln_command)
cli.add_command(path_command)
cli.add_command(version_command)
cli.add_command(promote_command)


def main() -> None:
    """Console script entry point."""
    cli()
