"""Click CLI wiring for the ``new`` command."""

from typing import Optional, Tuple

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.new.run_new_command import NewCommand


@click.command(name="new")
@click.argument("template", required=False, default=None)
@click.argument("name", required=False, default=None)
@click.option("--path", "-p", default=None, help="Output file path or directory.")
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Overwrite if the output file already exists.",
)
@click.option(
    "--set",
    "set_values",
    multiple=True,
    metavar="KEY=VALUE",
    help="Override a template variable (e.g. --set owner=myteam).",
)
@click.option(
    "--list",
    "list_templates",
    is_flag=True,
    default=False,
    is_eager=True,
    help="List available templates and exit.",
)
@click.option(
    "--validate",
    "run_validate",
    is_flag=True,
    default=False,
    help="Validate each generated file immediately after creation.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def new_command(
    template: Optional[str],
    name: Optional[str],
    path: Optional[str],
    overwrite: bool,
    set_values: Tuple[str, ...],
    list_templates: bool,
    run_validate: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Create a new platform configuration file from a template.

    TEMPLATE is the template name (e.g. namespace, provider, workspace).
    NAME is written into meta.name and used in the output filename.

    Use --list to show all available templates.
    """
    if not list_templates:
        if template is None:
            click.echo("Error: Missing argument 'TEMPLATE'.", err=True)
            raise click.exceptions.Exit(2)

        if name is None:
            click.echo("Error: Missing argument 'NAME'.", err=True)
            raise click.exceptions.Exit(2)

    command = NewCommand(
        template=template,
        name=name,
        list_templates=list_templates,
        path=path,
        overwrite=overwrite,
        set_values=set_values,
        run_validate=run_validate,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
