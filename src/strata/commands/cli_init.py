"""Click CLI wiring for the top-level init command."""

from pathlib import Path
from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.init.init_solution_command import InitSolutionCommand


@click.command(name="init", help="Initialize a new Strata solution workspace.")
@click.option(
    "--name",
    required=False,
    default=None,
    type=str,
    help="Name of the solution workspace.",
)
@click.option(
    "--template",
    "template",
    default=None,
    type=str,
    help=(
        "Scaffold template to apply. Accepts a built-in name (e.g. 'aks') "
        "or a path to a local template folder containing 'scaffold/' and an optional 'template.yaml'."
    ),
)
@click.option(
    "--list",
    "list_templates",
    is_flag=True,
    default=False,
    is_eager=True,
    help="List available scaffold templates and exit.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def init_command(
    name: Optional[str] = None,
    template: Optional[str] = None,
    list_templates: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Initialize a new solution workspace."""
    if list_templates:
        from strata.services.template_resolver import list_scaffold_templates

        wp = Path(work_path) if work_path else None
        templates = list_scaffold_templates(wp)

        if output == "json":
            import json

            click.echo(json.dumps({"success": True, "data": {"templates": templates}, "errors": [], "messages": []}))
        else:
            if templates:
                click.echo("\nAvailable scaffold templates:\n")
                for t in templates:
                    source_tag = " (workspace)" if t["source"] == "workspace" else ""
                    desc = f" — {t['description']}" if t["description"] else ""
                    click.echo(f"  {t['name']}{desc}{source_tag}")
                click.echo("")
                click.echo("Usage: strata sln init --name <NAME> --template <TEMPLATE>")
                click.echo("")
            else:
                click.echo("No scaffold templates found.")
        return

    if name is None:
        click.echo("Error: Missing option '--name'.", err=True)
        raise click.exceptions.Exit(2)

    command = InitSolutionCommand(
        name=name,
        template=template,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
