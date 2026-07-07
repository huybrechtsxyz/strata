"""Click CLI wiring for the ``secret`` command group."""

from __future__ import annotations

import json
from typing import Optional

import click

from strata.commands.cli_common import (
    click_file,
    click_output_format,
    click_work_path,
    handle_command_exit,
)
from strata.commands.secret.generate_secret_command import generate_secret
from strata.commands.secret.get_secret_command import GetSecretCommand
from strata.commands.secret.list_secret_command import ListSecretCommand
from strata.commands.secret.mask_secret_command import mask_secret
from strata.commands.secret.put_secret_command import PutSecretCommand
from strata.commands.secret.rotate_secret_command import RotateSecretCommand
from strata.commands.secret.status_secret_command import StatusSecretCommand

_FORMAT_HELP = (
    "Output encoding.  "
    "urlsafe = base64-URL (default, safe for passwords/tokens);  "
    "hex = lowercase hex string;  "
    "alphanumeric = letters + digits only;  "
    "password = letters + digits + symbols, guaranteed policy mix;  "
    "numeric = digits only (PINs, OTPs);  "
    "base64 = standard base64 (Kubernetes secrets, Docker auth);  "
    "uuid4 = random UUID v4;  "
    "uuid7 = time-ordered UUID v7 (--length ignored for UUID formats)."
)

_UUID_FORMATS = {"uuid4", "uuid7"}


@click.group(name="secret", help="Generate and manage secret values.")
def secret_group() -> None:
    """Secret command group."""


@secret_group.command(name="generate", help="Generate a cryptographically secure secret value.")
@click_output_format
@click.option(
    "--format",
    "fmt",
    type=click.Choice(
        ["urlsafe", "hex", "alphanumeric", "password", "numeric", "base64", "uuid4", "uuid7"], case_sensitive=False
    ),
    default="urlsafe",
    show_default=True,
    help=_FORMAT_HELP,
)
@click.option(
    "--length",
    type=click.IntRange(min=1),
    default=32,
    show_default=True,
    help=(
        "For urlsafe/hex: number of random bytes to source "
        "(output will be longer after encoding).  "
        "For alphanumeric/password/numeric: exact number of output characters.  "
        "For base64: number of random bytes before encoding.  "
        "Ignored for uuid4/uuid7."
    ),
)
def generate_secret_command(
    output: Optional[str],
    fmt: str,
    length: int,
) -> None:
    """Generate a cryptographically secure secret and print it to stdout."""
    try:
        value = generate_secret(fmt, length)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    if output == "json":
        data: dict = {"secret": value, "format": fmt}
        if fmt not in _UUID_FORMATS:
            data["length"] = length
        click.echo(json.dumps(data))
        return

    # text or console — just the bare value so it can be piped directly
    click.echo(value)


@secret_group.command(name="mask", help="Mask a secret value for safe display in logs or output.")
@click_output_format
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
def mask_secret_command(
    output: Optional[str],
    value: str,
    show: int,
    char: str,
) -> None:
    """Mask VALUE, keeping the first --show characters and replacing the rest with --char."""
    if len(char) != 1:
        raise click.UsageError("--char must be exactly one character.")

    masked = mask_secret(value, show=show, char=char)

    if output == "json":
        click.echo(json.dumps({"masked": masked, "show": show, "char": char}))
        return

    click.echo(masked)


# ---------- Deployment-aware commands (require --file / -f) ----------


@secret_group.command(name="list", help="List all secrets defined in the deployment environment.")
@click_output_format
@click_work_path
@click_file
@click.pass_context
def list_secret_command(ctx: click.Context, output: Optional[str], work_path: Optional[str], file: Optional[str]) -> None:
    """List secrets declared in the deployment YAML (no store access)."""
    cmd = ListSecretCommand(work_path=work_path or ctx.obj.get("work_path"), output=output, file=file)
    success = cmd.execute()
    handle_command_exit(cmd, success)


@secret_group.command(name="get", help="Read a secret value from its configured store.")
@click_output_format
@click_work_path
@click_file
@click.argument("key")
@click.option("--unmask", is_flag=True, default=False, help="Show the full secret value (default: masked).")
@click.pass_context
def get_secret_cmd(
    ctx: click.Context,
    output: Optional[str],
    work_path: Optional[str],
    file: Optional[str],
    key: str,
    unmask: bool,
) -> None:
    """Read KEY from the configured store backend."""
    cmd = GetSecretCommand(
        work_path=work_path or ctx.obj.get("work_path"), output=output, file=file, key=key, unmask=unmask
    )
    success = cmd.execute()
    handle_command_exit(cmd, success)


@secret_group.command(name="status", help="Check rotation health for secrets with a rotate policy.")
@click_output_format
@click_work_path
@click_file
@click.pass_context
def status_secret_command(ctx: click.Context, output: Optional[str], work_path: Optional[str], file: Optional[str]) -> None:
    """Report age and rotation status for all secrets that have a rotate: spec."""
    cmd = StatusSecretCommand(work_path=work_path or ctx.obj.get("work_path"), output=output, file=file)
    success = cmd.execute()
    handle_command_exit(cmd, success)


@secret_group.command(name="put", help="Write a secret to the configured store (create-if-not-exists).")
@click_output_format
@click_work_path
@click_file
@click.argument("key")
@click.option("--value", default=None, help="Explicit secret value to write.")
@click.option("--generate", "do_generate", is_flag=True, default=False, help="Generate a value using the YAML generate spec.")
@click.pass_context
def put_secret_command(
    ctx: click.Context,
    output: Optional[str],
    work_path: Optional[str],
    file: Optional[str],
    key: str,
    value: Optional[str],
    do_generate: bool,
) -> None:
    """Write KEY to the configured store backend."""
    cmd = PutSecretCommand(
        work_path=work_path or ctx.obj.get("work_path"),
        output=output,
        file=file,
        key=key,
        value=value,
        generate=do_generate,
    )
    success = cmd.execute()
    handle_command_exit(cmd, success)


@secret_group.command(name="rotate", help="Rotate a secret by generating a new value and overwriting the store.")
@click_output_format
@click_work_path
@click_file
@click.argument("key")
@click.option("--force", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_context
def rotate_secret_command(
    ctx: click.Context,
    output: Optional[str],
    work_path: Optional[str],
    file: Optional[str],
    key: str,
    force: bool,
) -> None:
    """Rotate KEY using its generate spec and overwrite the value in the store."""
    cmd = RotateSecretCommand(
        work_path=work_path or ctx.obj.get("work_path"), output=output, file=file, key=key, force=force
    )
    success = cmd.execute()
    handle_command_exit(cmd, success)
