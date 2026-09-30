#!/usr/bin/env python3
"""`strata values` — inspect and manage deployment values (variables,
secrets, feature flags). See docs/design/values-secrets-command.md.

No `-f FILE` on any deployment-scoped subcommand — same reasoning as
`validate_command`: v2 addresses documents by `(kind, name)`, so the
argument is the deployment's name, resolved against the solution found by
walking up from `--path` (default: cwd).

`values get` reveals secrets in full. `values list --declared-only` (Phase
2 of the design doc's Implementation Plan) never touches a store at all —
live resolution (masking secrets, the default `values list` mode) is
Phase 4, not yet built; `values list` without `--declared-only` raises a
clear `UsageError` rather than silently doing something incomplete.
"""

import json
import shlex
from dataclasses import asdict
from pathlib import Path

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import VALUE_TYPE_FILTERS, DeclaredValueRow, list_values, resolve_values
from strata.utils.errors import UsageError
from strata.utils.secret_generator import FORMATS, generate_secret, mask_secret

_FORMATS = ("table", "raw", "env", "export")
#: Formats where `--length` is ignored — omitted from `values generate`'s JSON payload.
_UUID_FORMATS = frozenset({"uuid4", "uuid7"})


@click.group("values")
def values_command() -> None:
    """Inspect deployment values (variables, secrets, feature flags)."""


@values_command.command("list")
@click.argument("deployment")
@click.option(
    "--path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@click.option(
    "--type",
    "type_filter",
    type=click.Choice(VALUE_TYPE_FILTERS),
    default=None,
    help="Restrict to one kind. Default: variables, secrets, and features all together.",
)
@click.option(
    "--declared-only",
    is_flag=True,
    default=False,
    help="List declarations only — no store access, no auth needed. Currently required: "
    "live resolution (the default mode once built) is not implemented yet.",
)
@output_option
@quiet_option
@verbose_option
def values_list(
    deployment: str,
    path: Path | None,
    type_filter: str | None,
    declared_only: bool,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """List every variable/secret/feature reachable from DEPLOYMENT.

    \b
    Exit codes:
      0  listed successfully
      2  bad arguments, DEPLOYMENT does not exist, not inside a solution,
         or --declared-only was omitted (live resolution is not built yet)
      1  system failure
    """
    with command_run("values list", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
        )

        rows, diagnostics = list_values(context, deployment, type_filter=type_filter, declared_only=declared_only)
        run.step(f"listed {len(rows)} declared value(s)")

        if output == "console":
            _print_declared_list(rows)

        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "deployment": deployment,
                "declared_only": declared_only,
                "values": [asdict(row) for row in rows],
            }
        run.ok = diagnostics.ok


def _print_declared_list(rows: list[DeclaredValueRow]) -> None:
    """Render `values list --declared-only` rows: key, kind, store, and
    (secrets only) a `generate`/`rotate` spec summary."""
    if not rows:
        click.echo("  (no variables, secrets, or features declared)")
        return

    key_w = max(len(row.key) for row in rows)
    kind_w = max(len(row.kind) for row in rows)
    store_w = max(len(row.store) for row in rows)
    for row in rows:
        parts = [f"  {row.key.ljust(key_w)}  {row.kind.ljust(kind_w)}  {row.store.ljust(store_w)}"]
        if row.generate:
            parts.append(f"generate:{row.generate}")
        if row.rotate:
            parts.append(f"rotate:{row.rotate}")
        click.echo("  ".join(parts))


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

        context = open_solution(resolve_work_path(path)).require_valid()
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


@values_command.command("generate")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(sorted(FORMATS), case_sensitive=False),
    default="urlsafe",
    show_default=True,
    help="Generator: urlsafe/hex/base64 (byte count), alphanumeric/password/numeric "
    "(character count), uuid4/uuid7 (--length ignored).",
)
@click.option(
    "--length",
    type=click.IntRange(min=1),
    default=32,
    show_default=True,
    help="Byte count for urlsafe/hex/base64, character count for alphanumeric/password/"
    "numeric. Ignored for uuid4/uuid7.",
)
@output_option
def values_generate(fmt: str, length: int, output: str) -> None:
    """Generate a cryptographically secure secret and print it.

    A pure utility — no DEPLOYMENT, no solution/workspace needed. Prints the
    bare value only (unless --output json), so it stays pipeable:

    \b
        strata values set web-prod --key DB_PASSWORD \\
            --value "$(strata values generate --format password --length 24)"
    """
    try:
        value = generate_secret(fmt, length)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    if output == "json":
        payload: dict[str, object] = {"secret": value, "format": fmt}
        if fmt not in _UUID_FORMATS:
            payload["length"] = length
        click.echo(json.dumps(payload))
    else:
        click.echo(value)


@values_command.command("mask")
@click.argument("value")
@click.option(
    "--show",
    type=click.IntRange(min=0),
    default=4,
    show_default=True,
    help="Number of leading characters to keep visible.",
)
@click.option(
    "--char",
    default="*",
    show_default=True,
    help="Replacement character for the masked portion.",
)
@output_option
def values_mask(value: str, show: int, char: str, output: str) -> None:
    """Mask VALUE, keeping the first --show characters and replacing the rest with --char.

    A pure utility — no DEPLOYMENT, no solution/workspace needed. Prints the
    bare masked value only (unless --output json).
    """
    if len(char) != 1:
        raise click.UsageError("--char must be exactly one character.")

    masked = mask_secret(value, show=show, char=char)

    if output == "json":
        click.echo(json.dumps({"masked": masked, "show": show, "char": char}))
    else:
        click.echo(masked)
