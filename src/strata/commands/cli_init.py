"""Click CLI wiring for the top-level init command."""

import os
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

# ---------------------------------------------------------------------------
# Guided wizard helpers
# ---------------------------------------------------------------------------

_STACK_CHOICES = [
    ("Kubernetes workloads (Terraform + Helm)", "kubernetes"),
    ("Docker Compose / Swarm services", "compose"),
    ("Minimal — just initialize the workspace", "minimal"),
]

_CLOUD_CHOICES = [
    ("Azure (AKS)", "azure"),
    ("AWS (EKS)", "aws"),
    ("GCP (GKE)", "gcp"),
    ("Other / skip", "other"),
]

_TEMPLATE_MAP = {
    ("kubernetes", "azure"): "aks",
    ("kubernetes", "aws"): "aks",  # no eks template yet — aks is closest
    ("kubernetes", "gcp"): "aks",  # same fallback
    ("kubernetes", "other"): None,
    ("compose", None): "compose",
    ("minimal", None): None,
}


def _run_guided_wizard() -> tuple[str, Optional[str]]:
    """Ask a short series of questions and return ``(name, template)``.

    Returns ``(name, None)`` when no template matches (minimal init).
    Raises ``click.Abort`` if the user cancels at any prompt.
    """
    click.echo("")
    click.echo("  strata — guided workspace setup")
    click.echo("  ─────────────────────────────────────────")
    click.echo("")

    name: str = click.prompt("  Workspace name")

    click.echo("")
    click.echo("  What are you deploying?")
    for i, (label, _) in enumerate(_STACK_CHOICES, start=1):
        click.echo(f"    [{i}] {label}")
    stack_idx = click.prompt("  ", type=click.IntRange(1, len(_STACK_CHOICES)), default=1, prompt_suffix="")
    stack_key = _STACK_CHOICES[stack_idx - 1][1]

    cloud_key: Optional[str] = None
    if stack_key == "kubernetes":
        click.echo("")
        click.echo("  Cloud provider?")
        for i, (label, _) in enumerate(_CLOUD_CHOICES, start=1):
            click.echo(f"    [{i}] {label}")
        cloud_idx = click.prompt("  ", type=click.IntRange(1, len(_CLOUD_CHOICES)), default=1, prompt_suffix="")
        cloud_key = _CLOUD_CHOICES[cloud_idx - 1][1]

    template = _TEMPLATE_MAP.get((stack_key, cloud_key))

    click.echo("")
    return name, template


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
@click.option(
    "--guided",
    "guided",
    is_flag=True,
    default=False,
    help="Ask a short series of questions to select and configure the right template.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def init_command(
    name: Optional[str] = None,
    template: Optional[str] = None,
    list_templates: bool = False,
    guided: bool = False,
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

    if guided:
        # Non-interactive environments: skip wizard, require explicit args
        if os.environ.get("CI"):
            click.echo(
                "⚠  --guided requires an interactive terminal. "
                "Use --name and --template for non-interactive environments.",
                err=True,
            )
            raise click.exceptions.Exit(2)
        try:
            name, template = _run_guided_wizard()
        except (click.Abort, KeyboardInterrupt):
            click.echo("\nCancelled.")
            raise click.exceptions.Exit(0) from None
    elif name is None:
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
