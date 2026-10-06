#!/usr/bin/env python3
"""`strata version` — show the CLI's own version, or manage `kind: version`
documents (`new`/`update`/`set`). See docs/work/version-lifecycle.md
Phases 3-4.

`version` was a single bare command before this (`strata version` printed
the CLI's own version, nothing else) — nesting `new`/`update`/`set` under
it turns it into a Click group, `invoke_without_command=True` so the bare
behaviour survives unchanged: `strata version` with no subcommand still
just prints the version; only `strata version <subcommand>` reaches the
new surface.

`--ring`/`--order`/`--wave` are deliberately not options here yet —
`VersionSpecModel.promotion` (docs/work/promotion.md Phase 1) does not
exist in the schema yet, so there is nothing real for them to set. Add
them once that phase ships, rather than accepting and silently discarding
them now.
"""

from dataclasses import asdict
from pathlib import Path
from typing import cast

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.path_controller import get_path
from strata.controllers.solution_context import open_solution
from strata.controllers.version_controller import ReconcileRow, reconcile_version, scaffold_version, set_version_pin
from strata.models.common_models import PlatformKind
from strata.models.version_model import PIN_CATEGORIES, VersionModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError
from strata.utils.version import get_version

_PATH_HELP = "Where to start looking for the solution. Defaults to the current directory."


@click.group("version", invoke_without_command=True)
@click.pass_context
def version_command(ctx: click.Context) -> None:
    """Show the strata version."""
    if ctx.invoked_subcommand is None:
        click.echo(get_version())


@version_command.command("new")
@click.argument("name")
@click.option("--workspace", required=True, help="Name of the Workspace document this version document applies to.")
@click.option(
    "--from",
    "from_name",
    default=None,
    metavar="VERSION",
    help="Clone this existing version document's pin keys (not values/reasons) as a starting point.",
)
@click.option("--path", "start_path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def version_new(
    name: str,
    workspace: str,
    from_name: str | None,
    start_path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Scaffold a new kind: version document named NAME.

    \b
    Exit codes:
      0  created
      2  bad arguments (NAME already exists, --workspace/--from name an
         unknown document), or not inside a solution
      1  system failure
    """
    with command_run("version new", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)", root=context.root, name=name, workspace=workspace
        )

        destination = scaffold_version(
            context.root,
            context.controller.index,
            solution,
            name,
            workspace=workspace,
            from_name=from_name,
        )
        run.step(f"created {destination.relative_to(context.root)}")

        if output == "console":
            click.echo(f"\n  \u2705  Created {destination.relative_to(context.root)}.\n")

        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"name": name, "workspace": workspace, "path": str(destination)}
        run.ok = True


@version_command.command("update")
@click.argument("name")
@click.option("--path", "start_path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def version_update(name: str, start_path: Path | None, output: str, quiet: bool, verbose: bool) -> None:
    """Report pin-key reconciliation candidates for the kind: version document named NAME.

    Read-only — a proposal, never an auto-apply. Compares NAME's declared
    pin keys against the solution's real inventory of pinnable targets: a
    real target with no pin yet is a candidate to add (seeded with its
    current value, never invented); a pin naming a target that no longer
    exists is a candidate to remove. The operator reviews and edits by hand.

    \b
    Exit codes:
      0  fully reconciled — nothing to add or remove
      2  bad arguments, NAME is not an indexed version document, or not
         inside a solution
      3  one or more add/remove candidates found
      1  system failure
    """
    with command_run("version update", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, name=name)

        entry = context.controller.index.get(PlatformKind.VERSION, name)
        if entry is None:
            raise UsageError(f"No version document named '{name}' is indexed.")

        rows = reconcile_version(context.controller.index, solution, cast(VersionModel, entry.model))
        run.step(f"found {len(rows)} candidate(s)")

        if output == "console":
            _print_rows(rows)

        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"name": name, "candidates": [asdict(row) for row in rows]}
        run.ok = not rows


def _print_rows(rows: list[ReconcileRow]) -> None:
    """Render `version update` rows: a '+' for an add candidate (with its
    seed value), a '-' for a stale pin to remove."""
    if not rows:
        click.echo("  (fully reconciled \u2014 nothing to add or remove)")
        return
    for row in rows:
        if row.action == "add":
            click.echo(f"  +  {row.category}.{row.name}  (seed: {row.seed_value})")
        else:
            click.echo(f"  -  {row.category}.{row.name}  (stale \u2014 target no longer exists)")


@version_command.command("set")
@click.argument("name")
@click.argument("category", type=click.Choice(PIN_CATEGORIES))
@click.argument("target")
@click.argument("value", required=False, default=None)
@click.option(
    "--available", default=None, metavar="VALUE", help="Record what's available upstream, without changing version."
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Override the refusal to change a held/unverified pin's version.",
)
@click.option("--path", "start_path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def version_set(
    name: str,
    category: str,
    target: str,
    value: str | None,
    available: str | None,
    force: bool,
    start_path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Set CATEGORY.TARGET's version and/or --available in the kind: version
    document named NAME.

    A surgical, comment-preserving edit (ruamel.yaml round-trip) — every
    other byte of the file (comments, ordering, unrelated pins) is left
    untouched. Never touches `reason`/`reviewed`. Creates the pin if TARGET
    is not yet declared (VALUE is then required). Refuses to change an
    existing held/unverified pin's version without --force; --available
    alone is always allowed regardless of status.

    \b
    Exit codes:
      0  set
      2  bad arguments (NAME/CATEGORY/TARGET invalid or missing, nothing
         to set, an empty VALUE/--available, a held/unverified pin without
         --force), or not inside a solution
      1  system failure
    """
    with command_run("version set", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            name=name,
            category=category,
            target=target,
        )

        result = get_path(context.controller.index, PlatformKind.VERSION, name)
        if not result.exists:
            raise UsageError(f"No version document named '{name}' is indexed.")

        updated = set_version_pin(
            Path(cast(str, result.path)), category, target, value, available=available, force=force
        )
        pin = next((p for c, n, p in updated.spec.pins.iter_pins() if c == category and n == target), None)
        run.step(f"set {category}.{target}")

        if output == "console" and pin is not None:
            click.echo(
                f"\n  \u2705  {category}.{target} = {pin.version}"
                + (f" (available: {pin.available})" if pin.available else "")
                + "\n"
            )

        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "name": name,
                "category": category,
                "target": target,
                "version": pin.version if pin else None,
                "available": pin.available if pin else None,
                "status": pin.status.value if pin else None,
            }
        run.ok = True
