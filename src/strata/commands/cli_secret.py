"""Click CLI wiring for the ``secret`` command group."""

from __future__ import annotations

import json
from typing import Optional

import click

from strata.commands.cli_common import click_output_format
from strata.commands.secret.generate_secret_command import generate_secret
from strata.commands.secret.mask_secret_command import mask_secret

_FORMAT_HELP = (
    "Output encoding.  "
    "urlsafe = base64-URL (default, safe for passwords/tokens);  "
    "hex = lowercase hex string;  "
    "alphanumeric = letters + digits only;  "
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
    type=click.Choice(["urlsafe", "hex", "alphanumeric", "uuid4", "uuid7"], case_sensitive=False),
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
        "For alphanumeric: exact number of output characters.  "
        "Ignored for uuid4/uuid7."
    ),
)
def generate_secret_command(
    output: Optional[str],
    fmt: str,
    length: int,
) -> None:
    """Generate a cryptographically secure secret and print it to stdout."""
    value = generate_secret(fmt, length)

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
