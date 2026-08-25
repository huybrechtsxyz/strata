"""Click CLI wiring for the ``diagram`` command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.diagrams.list_diagram_command import ListDiagramCommand
from strata.commands.diagrams.resolve_diagram_command import ResolveDiagramCommand
from strata.commands.diagrams.show_diagram_command import ShowDiagramCommand


@click.group(name="diagram", help="Render workspace diagrams as Mermaid.")
def diagram_group() -> None:
    """Diagram command group."""


@diagram_group.command(name="show")
@click.option(
    "--file",
    "-f",
    required=True,
    metavar="NAME_OR_PATH",
    help=(
        "Diagram definition to render: a built-in name (e.g. 'topology'), a name in "
        ".strata/diagrams/, or a path to a 'kind: diagram' YAML file."
    ),
)
@click.option(
    "--entry",
    "-e",
    default=None,
    metavar="PATH",
    help="Entry point file (deployment or workspace YAML) for graph sources. If omitted, discovers all deployments.",
)
@click.option(
    "--save",
    "-s",
    default=None,
    metavar="PATH",
    is_flag=False,
    flag_value="diagram.mmd",
    help="Write the rendered Mermaid to a file (default: diagram.mmd). Written in addition to console output.",
)
@click.option(
    "--print-template",
    is_flag=True,
    default=False,
    help="Emit the Jinja template instead of rendering it — a starting point for customisation.",
)
@click.option(
    "--format",
    "format_",
    type=click.Choice(["mmd", "svg", "png"], case_sensitive=False),
    default="mmd",
    help=(
        "Output format: 'mmd' (default, Mermaid source text) or 'svg'/'png' (rendered image, via "
        "Kroki — https://kroki.io by default, no account needed; set STRATA_KROKI_ADDRESS to "
        "self-host). 'svg'/'png' always writes to a file (see --save)."
    ),
)
@click.option(
    "--no-validate",
    is_flag=True,
    default=False,
    help="Skip validation of the documents being graphed. Faster for large workspaces.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def diagram_show(
    file: str,
    entry: Optional[str] = None,
    save: Optional[str] = None,
    print_template: bool = False,
    format_: str = "mmd",
    no_validate: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Render a diagram definition to Mermaid source.

    Built-in definitions are shipped 'kind: diagram' YAML files, so a name and a
    path take the same code path — copy a built-in into .strata/diagrams/ and
    edit it like any other definition.

        strata diagram show -f topology
        strata diagram show -f refs --entry deploy/deploy-prd.yaml
        strata diagram show -f .strata/diagrams/prd.yaml --output json
        strata diagram show -f topology --print-template
        strata diagram show -f topology --save topology.mmd
        strata diagram show -f topology --format svg --save topology.svg
    """
    command = ShowDiagramCommand(
        file=file,
        entry=entry,
        save=save,
        print_template=print_template,
        format=format_,
        no_validate=no_validate,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@diagram_group.command(name="list")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def diagram_list(
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """List available diagram definitions.

    Shows shipped built-ins and any definitions in .strata/diagrams/. A workspace
    definition shadows a built-in of the same name.

        strata diagram list
        strata diagram list --output json
    """
    command = ListDiagramCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@diagram_group.command(name="resolve")
@click.argument("uri")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def diagram_resolve(
    uri: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Resolve a strata:// URI to the file and line it names.

    Diagram nodes carry a strata:// URI as a Mermaid 'click' directive. The URI is
    structural rather than positional — it encodes no line number — so this command
    is what turns one into a concrete location, on demand and headless.

        strata diagram resolve strata://file/deploy/deploy-prd.yaml
        strata diagram resolve strata://workspace/platform/resource/app_server
        strata diagram resolve strata://environment/env-prd/secret/DB_PASSWORD --output json

    Console output is 'path:line' (or just 'path' for a file reference).
    """
    command = ResolveDiagramCommand(
        uri=uri,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
