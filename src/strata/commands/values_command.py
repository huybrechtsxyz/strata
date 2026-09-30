#!/usr/bin/env python3
"""`strata values` — inspect and manage deployment values (variables,
secrets, feature flags). See docs/design/values-secrets-command.md.

No `-f FILE` on any deployment-scoped subcommand — same reasoning as
`validate_command`: v2 addresses documents by `(kind, name)`, so the
argument is the deployment's name, resolved against the solution found by
walking up from `--path` (default: cwd).

`values get` reveals secrets in full. `values list` resolves live by
default, masking secrets (`values list --declared-only` skips all store
I/O instead — Phase 2 of the design doc's Implementation Plan).
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
from strata.controllers.value_controller import (
    VALUE_TYPE_FILTERS,
    DeclaredValueRow,
    ResolveRow,
    SecretStatusRow,
    ValueListRow,
    list_values,
    resolve_diagnostic,
    resolve_values,
    rotate_secret,
    secret_status,
    set_value,
)
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
    help="List declarations only — no store access, no auth needed, no value resolved. "
    "Default (omitted): live resolution, masking secrets.",
)
@click.option(
    "--show-store",
    is_flag=True,
    default=False,
    help="Console only: add the store type column. Live mode only — declared-only rows "
    "always show the store (there is nothing else substantive to show).",
)
@click.option(
    "--unresolved",
    "unresolved_only",
    is_flag=True,
    default=False,
    help="Show only keys that failed to resolve. Live mode only.",
)
@output_option
@quiet_option
@verbose_option
def values_list(
    deployment: str,
    path: Path | None,
    type_filter: str | None,
    declared_only: bool,
    show_store: bool,
    unresolved_only: bool,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """List every variable/secret/feature reachable from DEPLOYMENT.

    Live (default): resolves every key, masking secrets — variables/
    features shown in full. `--declared-only`: no store access at all,
    just the declarations (key/store/value-ref/generate-or-rotate summary).

    \b
    Exit codes:
      0  listed successfully (declared-only never fails on a value)
      2  bad arguments, or DEPLOYMENT does not exist, or not inside a solution
      3  live mode: one or more keys failed to resolve
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

        rows, diagnostics = list_values(
            context,
            deployment,
            type_filter=type_filter,
            show_store=show_store,
            unresolved_only=unresolved_only,
            declared_only=declared_only,
        )
        run.step(f"listed {len(rows)} value(s)")

        if output == "console":
            if declared_only:
                _print_declared_list(rows)  # type: ignore[arg-type]
            else:
                _print_value_list(rows, show_store=show_store)  # type: ignore[arg-type]

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


def _print_value_list(rows: list[ValueListRow], *, show_store: bool) -> None:
    """Render `values list` (live) rows: key, kind, resolved value (masked
    for secrets), optionally the store (`--show-store`), and the failure
    reason for anything that didn't resolve."""
    if not rows:
        click.echo("  (no variables, secrets, or features declared)")
        return

    key_w = max(len(row.key) for row in rows)
    kind_w = max(len(row.kind) for row in rows)
    store_w = max(len(row.store) for row in rows) if show_store else 0
    for row in rows:
        parts = [f"  {row.key.ljust(key_w)}  {row.kind.ljust(kind_w)}"]
        if show_store:
            parts.append(row.store.ljust(store_w))
        parts.append(row.value if row.ok and row.value is not None else f"(unresolved — {row.reason})")
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


@values_command.command("resolve")
@click.argument("deployment")
@click.option(
    "--path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@click.option(
    "--key",
    default=None,
    metavar="KEY",
    help="Diagnose this one declared key only. Default: every variable/secret/feature reachable from DEPLOYMENT.",
)
@click.option(
    "--probe",
    is_flag=True,
    default=False,
    help="Also attempt a real resolve() call per integration-backed key (without "
    "revealing the value) — confirms actual backend reachability/authentication, "
    "not just that a resolver is registered.",
)
@output_option
@quiet_option
@verbose_option
def values_resolve(
    deployment: str,
    path: Path | None,
    key: str | None,
    probe: bool,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Diagnose whether declared keys for DEPLOYMENT would resolve, without revealing any value.

    \b
    Exit codes:
      0  every checked key would resolve
      2  bad arguments, DEPLOYMENT does not exist, or not inside a solution
      3  --key names a key that isn't declared, or one or more keys would fail to resolve
      1  system failure
    """
    with command_run("values resolve", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
        )

        rows, diagnostics = resolve_diagnostic(context, deployment, key=key, probe=probe)
        run.step(f"checked {len(rows)} key(s)" + (" with --probe" if probe else ""))

        if output == "console":
            _print_resolve_rows(rows)

        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "deployment": deployment,
                "mode": "probe" if probe else "path",
                "results": [asdict(row) for row in rows],
            }
        run.ok = diagnostics.ok


def _print_resolve_rows(rows: list[ResolveRow]) -> None:
    """Render `values resolve` rows: key, kind, store, registration status,
    and the final would-resolve verdict."""
    if not rows:
        click.echo("  (no keys to check)")
        return

    key_w = max(len(row.key) for row in rows)
    kind_w = max(len(row.kind) for row in rows)
    store_w = max(len(row.store) for row in rows)
    for row in rows:
        registered = "registered" if row.registered else "not registered"
        parts = [f"  {row.key.ljust(key_w)}  {row.kind.ljust(kind_w)}  {row.store.ljust(store_w)}  {registered}"]
        if row.probed:
            parts.append("probed")
        parts.append("✓  ok" if row.ok else f"✗  {row.reason}")
        click.echo("  ".join(parts))


@values_command.command("set")
@click.argument("deployment")
@click.option(
    "--key",
    "-k",
    required=True,
    metavar="KEY",
    help="The declared variable/secret/feature key to write.",
)
@click.option(
    "--value",
    default=None,
    metavar="VALUE",
    help="Explicit value to write. Mutually exclusive with --from-file/--stdin/--generate.",
)
@click.option(
    "--from-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Read the value from a file (as-is — multiline-safe for certs/keys).",
)
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    default=False,
    help="Read the value from stdin.",
)
@click.option(
    "--generate",
    "do_generate",
    is_flag=True,
    default=False,
    help="Generate a value using KEY's own generate: spec (secrets only).",
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
def values_set(
    deployment: str,
    key: str,
    value: str | None,
    from_file: Path | None,
    from_stdin: bool,
    do_generate: bool,
    path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Write KEY's value to its configured store backend for DEPLOYMENT.

    Exactly one of --value/--from-file/--stdin/--generate is required.
    `constant`/`environment`/`github`/`store: artifact` keys have no store
    integration to write to at all — rejected, naming where to edit them
    directly instead.

    \b
    Exit codes:
      0  written
      2  bad arguments (ambiguous/missing value source, unknown key, a
         built-in store, --generate on a non-secret or with no generate:
         spec), DEPLOYMENT does not exist, or not inside a solution
      3  the store write itself failed
      1  system failure
    """
    with command_run("values set", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
        )

        result, diagnostics = set_value(
            context,
            deployment,
            key,
            value=value,
            from_file=str(from_file) if from_file is not None else None,
            from_stdin=from_stdin,
            generate=do_generate,
        )

        if output == "console" and result is not None:
            note = "generated and " if result.generated else ""
            click.echo(f"\n  ✅  {result.kind.capitalize()} '{result.key}' {note}written to {result.store}.\n")

        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "deployment": deployment,
                "key": key,
                "written": result is not None,
                "kind": result.kind if result is not None else None,
                "store": result.store if result is not None else None,
                "generated": result.generated if result is not None else None,
            }
        run.ok = diagnostics.ok


@values_command.command("rotate")
@click.argument("deployment")
@click.argument("key")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt.",
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
def values_rotate(
    deployment: str,
    key: str,
    force: bool,
    path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Rotate secret KEY for DEPLOYMENT: generate a new value from its own
    generate: spec and overwrite it in the store.

    Secrets only, and only when KEY has a declared `generate:` spec — use
    `values set --value`/`--from-file`/`--stdin` to set a secret manually
    instead.

    \b
    Exit codes:
      0  rotated
      2  bad arguments, DEPLOYMENT/KEY does not exist, KEY is not a secret,
         KEY has no generate: spec, or not inside a solution
      3  the store write itself failed
      1  system failure
    """
    with command_run("values rotate", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
        )

        if not force:
            # err=True keeps the prompt off stdout, which --output json must keep pure.
            click.confirm(
                f"Rotate secret '{key}' for deployment '{deployment}'? This overwrites the current value.",
                abort=True,
                err=True,
            )

        result, diagnostics = rotate_secret(context, deployment, key, force=force)

        if output == "console" and result is not None:
            click.echo(f"\n  ✅  Secret '{result.key}' rotated in {result.store}.\n")

        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "deployment": deployment,
                "key": key,
                "rotated": result is not None,
                "store": result.store if result is not None else None,
            }
        run.ok = diagnostics.ok


@values_command.command("status")
@click.argument("deployment")
@click.option(
    "--path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@output_option
@quiet_option
@verbose_option
def values_status(
    deployment: str,
    path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Report rotation health for every secret with a rotate: spec, for DEPLOYMENT.

    Read-only — never rotates anything (use `values rotate` for that).
    Secrets with no `rotate:` spec are not reported at all.

    \b
    Exit codes:
      0  nothing overdue (including when no secret declares a rotate: spec)
      2  bad arguments, DEPLOYMENT does not exist, or not inside a solution
      3  one or more secrets are overdue for rotation
      1  system failure
    """
    with command_run("values status", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
        )

        rows, diagnostics = secret_status(context, deployment)
        run.step(f"checked {len(rows)} secret(s) with a rotation policy")

        if output == "console":
            _print_status_rows(rows)

        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "deployment": deployment,
                "secrets": [asdict(row) for row in rows],
                "overdue": sum(1 for row in rows if row.status == "overdue"),
            }
        run.ok = not any(row.status == "overdue" for row in rows)


def _print_status_rows(rows: list[SecretStatusRow]) -> None:
    """Render `values status` rows: key, rotation policy, and age vs.
    `max_age` (or why age couldn't be determined)."""
    if not rows:
        click.echo("  (no secrets with a rotation policy declared)")
        return

    key_w = max(len(row.key) for row in rows)
    for row in rows:
        if row.status == "overdue":
            tag = f"⚠  OVERDUE ({row.age_days}d / {row.max_age}d)"
        elif row.status == "ok":
            tag = f"✓  ok ({row.age_days}d / {row.max_age}d, {row.days_remaining}d left)"
        else:
            tag = f"?  {row.status}"
        click.echo(f"  {row.key.ljust(key_w)}  [{row.policy}]  {tag}")


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
