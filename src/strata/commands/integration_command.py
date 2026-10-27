#!/usr/bin/env python3
"""`strata integration check NAME` and `strata integration list` (docs/work/integration-readiness.md).

Both report local readiness (`integration_readiness`, the same check deploy preflight runs: enabled,
tool on PATH, version). `check` then verifies credentials and reachability for a type implementing
`ConnectionCheckIntegration`, changing nothing; credentials referenced by `spec.authentication`
resolve through `--deployment`, as `deploy run` resolves them. `list` stays offline and, with
`--deployment`, shows what that deployment binds.
"""

from dataclasses import asdict
from pathlib import Path
from typing import cast

import click

from strata.commands.exit_codes import EXIT_FAILURE
from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.integration_readiness import (
    IntegrationReadiness,
    IntegrationUse,
    assess_named,
    list_integrations,
)
from strata.controllers.security_gate_controller import bind_resolved_credentials
from strata.controllers.solution_context import SolutionContext, open_solution
from strata.controllers.value_controller import resolve_deployment
from strata.integrations.capabilities import ConnectionCheckIntegration, CredentialedIntegration
from strata.integrations.errors import IntegrationCredentialError, IntegrationError
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError


@click.group("integration")
def integration_command() -> None:
    """Inspect Integration documents."""


@integration_command.command("check")
@click.argument("name")
@click.option(
    "--deployment",
    default=None,
    metavar="NAME",
    help="Resolve the document's authentication key references through this deployment. Required when the "
    "document has an authentication block.",
)
@click.option(
    "--pin",
    default=None,
    metavar="VERSION",
    help="With --deployment: resolve it with this kind: version document, as 'deploy run --pin' does.",
)
@click.option(
    "--path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@output_option
@quiet_option
@verbose_option
def check_command(
    name: str,
    deployment: str | None,
    pin: str | None,
    path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Check integration NAME: local readiness, then its connection when the type has a check.

    NAME is an Integration document, or an integration type (checked as deploy would bind it).
    Local readiness: enabled, tool on PATH, installed version against spec.version. The connection
    check verifies credentials and reachability, changing nothing.

    \b
    Exit codes:
      0  ready (and connected, when the type has a connection check)
      1  the platform could not be reached
      2  NAME is neither declared nor a known type, several documents
         have that type, --deployment needed, or bad arguments
      3  not ready locally (disabled, not on PATH, wrong version,
         misconfigured), or credentials missing, rejected or unresolved
    """
    with command_run("integration check", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            integration=name,
            deployment=deployment or "-",
        )

        subject = assess_named(context.controller.index, name)
        readiness = subject.readiness
        run.step(f"checking {readiness.type} '{readiness.name}'")
        diagnostics = Diagnostics()
        diagnostics.info(_describe(readiness), code="integration_ready")
        for problem in readiness.problems:
            diagnostics.error(problem.message, code=problem.code)

        lines: list[str] = []
        if readiness.ok and readiness.connection_check:
            integration = cast(ConnectionCheckIntegration, subject.integration)
            lines, failure_code = _check_connection(context, integration, deployment, pin, diagnostics)
            if failure_code is not None:
                run.failure_code = failure_code
        elif readiness.ok and readiness.local_tool:
            diagnostics.info(
                f"type '{readiness.type}' has no connection check; local checks only.",
                code="integration_no_connection_check",
            )
        elif readiness.ok and not readiness.credential_holder:
            diagnostics.info(
                f"type '{readiness.type}' is networked and has no connection check; nothing to verify.",
                code="integration_no_connection_check",
            )

        run.report(diagnostics, root=context.root)
        run.ok = diagnostics.ok
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "integration": readiness.name,
                "type": readiness.type,
                "declared": readiness.declared,
                "readiness": _readiness_json(readiness),
                "checks": lines,
            }


@integration_command.command("list")
@click.option(
    "--deployment",
    default=None,
    metavar="NAME",
    help="Also list what this deployment binds (step provisioners, value stores, output store, gate "
    "policies) and fail when one of those is not ready.",
)
@click.option(
    "--pin",
    default=None,
    metavar="VERSION",
    help="With --deployment: resolve it with this kind: version document, as 'deploy run --pin' does.",
)
@click.option(
    "--path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@output_option
@quiet_option
@verbose_option
def list_command(
    deployment: str | None, pin: str | None, path: Path | None, output: str, quiet: bool, verbose: bool
) -> None:
    """List every Integration with its local readiness; with --deployment, what that deployment uses.

    Reads each local tool's version (runs it once); never resolves credentials or contacts a
    remote service - use 'strata integration check NAME' for that.

    \b
    Exit codes:
      0  listed (without --deployment, problems are warnings)
      2  unknown deployment, several documents of one auto-bound type,
         or bad arguments
      3  with --deployment: an integration it uses is not ready
    """
    with command_run("integration list", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment or "-",
        )
        rows = list_integrations(context, deployment, version_pin=pin)
        run.step(f"{len(rows)} integration(s)")
        diagnostics = Diagnostics()
        for row in rows:
            if not row.used_for and not row.readiness.enabled:
                continue
            report = diagnostics.error if row.used_for else diagnostics.warning
            for problem in row.readiness.problems:
                report(problem.message, code=problem.code)
        if output == "console":
            _print_rows(rows, with_usage=deployment is not None)
        run.report(diagnostics, root=context.root)
        run.ok = diagnostics.ok
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"integrations": [_row_json(row) for row in rows]}


def _print_rows(rows: list[IntegrationUse], *, with_usage: bool) -> None:
    if not rows:
        click.echo("  (no integrations)")
        return
    headers = ("NAME", "TYPE", "STATUS", "TOOL", "VERSION", "CHECK") + (("USED FOR",) if with_usage else ())
    table = [
        (
            row.readiness.name if row.readiness.declared else f"{row.readiness.name} (type default)",
            row.readiness.type,
            _status(row.readiness),
            _tool(row.readiness),
            _version(row.readiness),
            "connection" if row.readiness.connection_check else "-",
        )
        + (("; ".join(row.used_for) or "-",) if with_usage else ())
        for row in rows
    ]
    widths = [max(len(headers[i]), *(len(line[i]) for line in table)) for i in range(len(headers) - 1)]
    for line in [headers, *table]:
        click.echo(
            "  " + "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=False)) + "  " + line[-1]
        )


def _status(readiness: IntegrationReadiness) -> str:
    if readiness.credential_holder:
        return "credentials"
    if not readiness.enabled:
        return "disabled"
    return "ready" if readiness.ok else "not ready"


def _tool(readiness: IntegrationReadiness) -> str:
    if not readiness.local_tool:
        return "-"
    return f"{readiness.command}" + ("" if readiness.available else " (missing)")


def _version(readiness: IntegrationReadiness) -> str:
    installed = readiness.installed_version or "-"
    return f"{installed} ({readiness.required_version})" if readiness.required_version else installed


def _readiness_json(readiness: IntegrationReadiness) -> dict[str, object]:
    return {
        **{key: value for key, value in asdict(readiness).items() if key != "problems"},
        "ok": readiness.ok,
        "credential_holder": readiness.credential_holder,
        "problems": [asdict(problem) for problem in readiness.problems],
    }


def _row_json(row: IntegrationUse) -> dict[str, object]:
    return {**_readiness_json(row.readiness), "used_for": list(row.used_for)}


def _check_connection(
    context: SolutionContext,
    integration: ConnectionCheckIntegration,
    deployment: str | None,
    pin: str | None,
    diagnostics: Diagnostics,
) -> tuple[list[str], int | None]:
    """Run the connection check; failures become diagnostics (R6). Returns the lines and an exit code override.

    Raises:
        UsageError: `--deployment` is needed, or names an unknown deployment.
    """
    config = integration.config
    if config is not None and config.spec.authentication is not None and deployment is None:
        raise UsageError(
            f"Integration '{integration.name}' declares authentication; pass --deployment NAME to resolve its "
            "credentials."
        )
    if deployment is not None:
        resolve_deployment(context, deployment, version_pin=pin)  # a typo fails even when no key needs it
        credentials = bind_resolved_credentials(context, integration, None, deployment, version_pin=pin)
        if not credentials.ok:
            diagnostics.extend(credentials)
            return [], None
    elif isinstance(integration, CredentialedIntegration):
        integration.bind_credentials({})
    try:
        lines = integration.check_connection()
    except IntegrationCredentialError as exc:
        diagnostics.error(str(exc), code="integration_credentials_rejected")
        return [], None
    except IntegrationError as exc:
        diagnostics.error(str(exc), code="integration_unreachable")
        return [], EXIT_FAILURE
    for line in lines:
        diagnostics.info(line, code="integration_check_ok")
    return lines, None


def _describe(readiness: IntegrationReadiness) -> str:
    source = "" if readiness.declared else " (no enabled document; type defaults)"
    parts = [f"{readiness.type} '{readiness.name}'{source}"]
    if readiness.credential_holder:
        parts.append("credential holder (no integration class; its authentication is read directly)")
    elif not readiness.registered:
        parts.append("no integration class")
    elif readiness.local_tool:
        parts.append(f"'{readiness.command}' {'on PATH' if readiness.available else 'not on PATH'}")
        if readiness.installed_version is not None:
            parts.append(f"version {readiness.installed_version}")
        if readiness.required_version is not None:
            parts.append(f"requires '{readiness.required_version}'")
    else:
        parts.append("networked (no local tool)")
    if not readiness.enabled:
        parts.append("disabled")
    return ", ".join(parts) + "."
