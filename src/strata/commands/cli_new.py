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
@click.argument("name", required=False, default=None)
@click.option(
    "--template",
    "template",
    default=None,
    metavar="TEMPLATE",
    help="Template name (e.g. namespace, provider, workspace).",
)
@click.option(
    "--output-file",
    "output_file",
    default=None,
    metavar="FILE",
    help="Output file path or directory.",
)
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
@click.option(
    "--scaffold-deps",
    "scaffold_deps",
    is_flag=True,
    default=False,
    help="After creation, detect and scaffold any missing referenced files.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def new_command(
    name: Optional[str],
    template: Optional[str],
    output_file: Optional[str],
    overwrite: bool,
    set_values: Tuple[str, ...],
    list_templates: bool,
    run_validate: bool = False,
    scaffold_deps: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Create a new platform configuration file from a template.

    NAME is written into meta.name and used in the output filename.

    Use --template to select which template to use (e.g. namespace, provider,
    workspace) and --list to show all available templates.
    """
    if not list_templates:
        if name is None:
            click.echo("Error: Missing argument 'NAME'.", err=True)
            raise click.exceptions.Exit(2)

        if template is None:
            click.echo("Error: Missing option '--template'.", err=True)
            raise click.exceptions.Exit(2)

    command = NewCommand(
        template=template,
        name=name,
        list_templates=list_templates,
        path=output_file,
        overwrite=overwrite,
        set_values=set_values,
        run_validate=run_validate,
        scaffold_deps=scaffold_deps,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
