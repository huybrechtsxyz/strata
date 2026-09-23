#!/usr/bin/env python3
"""`strata values get` — resolve one or more values for a deployment.

No `-f FILE` — same reasoning as `validate_command`: v2 addresses documents
by `(kind, name)`, so the argument is the deployment's name, resolved
against the solution found by walking up from PATH (default: cwd).

Reveals secrets in full — unlike v1's `values list`, which this pass does
not build (see `/memories/repo/v1-consumer-usage.md`'s rebuild order: `get`
proves the resolver architecture; `list`/`set` and caching are follow-ups).
"""

import shlex
from pathlib import Path

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, verbose_option
from strata.commands.run import command_run
from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import resolve_values
from strata.utils.errors import UsageError

_FORMATS = ("table", "raw", "env", "export")


@click.group("values")
def values_command() -> None:
    """Inspect deployment values (variables, secrets, feature flags)."""


@values_command.command("get")
@click.argument("deployment")
@click.argument("keys", nargs=-1, required=True, metavar="KEY...")
@click.option(
    "--path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@click.option(
    "--format",
    "value_format",
    type=click.Choice(_FORMATS),
    default="table",
    show_default=True,
    help="Rendering: table (key/value), raw (bare value, exactly one KEY), env (KEY=value), "
    "export (export KEY='value'). Ignored when --output json.",
)
@output_option
@quiet_option
@verbose_option
def values_get(
    deployment: str,
    keys: tuple[str, ...],
    path: Path | None,
    value_format: str,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Resolve the full value of one or more KEYs for DEPLOYMENT.

    \b
    Exit codes:
      0  every key resolved
      2  bad arguments, or DEPLOYMENT does not exist, or not inside a solution
      3  the solution is invalid, or one or more keys failed to resolve
      1  system failure
    """
    with command_run("values get", output=output, quiet=quiet, verbose=verbose) as run:
        if value_format == "raw" and len(keys) != 1:
            raise UsageError("--format raw requires exactly one KEY.")

        context = open_solution(path).require_valid()
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
        )

        result = resolve_values(context, deployment, list(keys))
        run.step(f"resolved {len(result.values)} of {len(keys)} keys")

        if output == "console":
            _print_console(result.values, keys, value_format)

        run.report(result.diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "deployment": deployment,
                "results": {key: result.values.get(key) for key in keys},
            }
        run.ok = result.diagnostics.ok


def _print_console(values: dict[str, str], keys: tuple[str, ...], value_format: str) -> None:
    """Render resolved values for the console, per `--format`.

    `raw`/`env`/`export` are meant for direct shell/script consumption — never
    emit anything for these when a key failed to resolve, so a script can't
    mistake a partial result for a complete one. `table` always shows every
    requested key, marking failures inline (the actual reason is in the
    diagnostics block `run.report()` prints separately).
    """
    if value_format == "table":
        width = max((len(key) for key in keys), default=3)
        for key in keys:
            rendered = values.get(key, "(unresolved — see below)")
            click.echo(f"  {key.ljust(width)}  {rendered}")
        return

    if any(key not in values for key in keys):
        return

    if value_format == "raw":
        click.echo(values[keys[0]])
    elif value_format == "env":
        for key in keys:
            click.echo(f"{key}={values[key]}")
    elif value_format == "export":
        for key in keys:
            click.echo(f"export {key}={shlex.quote(values[key])}")
