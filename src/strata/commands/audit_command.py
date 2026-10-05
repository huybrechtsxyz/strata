#!/usr/bin/env python3
"""`strata audit status` — read back the latest audit record per deployment
from the solution's configured durable `git` sink (docs/design/
audit-commands.md).

Phase 4 of that doc's Implementation Plan — the command layer over
`controllers/audit_read.py`'s `audit_status()`. Only `status` ships in this
pass; `changes` (range listing, filters) is explicitly deferred — see that
doc's "Deferred — not open questions, decided scope cuts for this phase".

No `try`/`except` around `audit_status()` on purpose: both `UsageError`
(no/multiple readable sinks — the "tell the user first" check, which runs
before any git/network operation) and `AuditReadError` (the sink's remote
couldn't be synced) are `StrataError` subclasses, and `command_run()`'s own
context manager (`commands/run.py`) already catches every `StrataError` and
maps it to the right exit code.
"""

from pathlib import Path
from typing import Any

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.audit_read import DeploymentStatusRecord, audit_status
from strata.controllers.solution_context import open_solution


@click.group("audit")
def audit_command() -> None:
    """Read back audit trail records from a configured durable sink."""


@audit_command.command("status")
@click.argument("deployment", required=False)
@click.option(
    "--path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@output_option
@quiet_option
@verbose_option
def status_command(deployment: str | None, path: Path | None, output: str, quiet: bool, verbose: bool) -> None:
    """Show the latest audit record per deployment (or just DEPLOYMENT).

    Reads from the solution's configured 'git' audit sink
    (spec.audit.sinks[].git) — never local files, since most real
    deployments run in ephemeral CI (docs/design/audit-trail.md's "Layer 3
    deferred"). Fails fast, before any network access, if no readable sink
    is configured, or if more than one is (only one is supported so far).

    DEPLOYMENT with no records yet is not an error — the sink is reachable,
    there is just nothing recorded for it.

    \b
    Exit codes:
      0  the sink was read successfully, even if DEPLOYMENT has no records yet
      2  bad arguments, not inside a solution, or no/multiple readable audit sinks configured
      3  the solution itself is invalid
      1  system failure — the sink's remote could not be reached
    """
    with command_run("audit status", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution

        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, deployment=deployment)

        records, diagnostics = audit_status(context, deployment)

        if output == "console":
            _print_status_table(records)

        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"deployments": [_to_json(record) for record in records]}
        run.ok = True


def _print_status_table(records: list[DeploymentStatusRecord]) -> None:
    """Render one line per deployment: name, status, version, deployed_by,
    completed_at, and whether a change_reference is present.

    Mirrors `values_command.py`'s `_print_declared_list()` convention
    (dynamic per-column width via `ljust()`, a `(no ...)` placeholder for an
    empty result) rather than inventing a second table layout.
    """
    if not records:
        click.echo("  (no audit records found)")
        return

    headers = ("DEPLOYMENT", "STATUS", "VERSION", "DEPLOYED BY", "COMPLETED AT")
    deployment_w = max(len(headers[0]), max(len(r.deployment) for r in records))
    status_w = max(len(headers[1]), max(len(r.status) for r in records))
    version_w = max(len(headers[2]), max(len(r.version) for r in records))
    deployed_by_w = max(len(headers[3]), max(len(r.deployed_by) for r in records))
    completed_at_w = max(len(headers[4]), max(len(r.completed_at) for r in records))

    click.echo(
        f"  {headers[0].ljust(deployment_w)}  {headers[1].ljust(status_w)}  {headers[2].ljust(version_w)}  "
        f"{headers[3].ljust(deployed_by_w)}  {headers[4].ljust(completed_at_w)}  CHANGE REF"
    )
    for record in records:
        change_ref = (
            f"{record.change_reference.system}:{record.change_reference.id}" if record.change_reference else "-"
        )
        click.echo(
            f"  {record.deployment.ljust(deployment_w)}  {record.status.ljust(status_w)}  "
            f"{record.version.ljust(version_w)}  {record.deployed_by.ljust(deployed_by_w)}  "
            f"{record.completed_at.ljust(completed_at_w)}  {change_ref}"
        )


def _to_json(record: DeploymentStatusRecord) -> dict[str, Any]:
    """`DeploymentStatusRecord` -> a plain, JSON-serialisable mapping.

    Not `dataclasses.asdict()` (`graph_command.py`'s convention for its own,
    all-`str`-field `GraphEdge`): `change_reference` is a pydantic model,
    not a nested dataclass, so `asdict()` would leave it as a live object
    `json.dumps()` cannot serialise rather than recursing into it.
    """
    return {
        "deployment": record.deployment,
        "status": record.status,
        "version": record.version,
        "deployed_by": record.deployed_by,
        "completed_at": record.completed_at,
        "change_reference": record.change_reference.model_dump(exclude_none=True) if record.change_reference else None,
    }
