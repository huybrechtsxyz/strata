#!/usr/bin/env python3
"""`strata deploy report DEPLOYMENT` - the handover document: step outputs, the secrets each step used and where
each lives, and the images (docs/design/deployment-documentation.md).

Reads the workspace's `output_store` and the solution only. Never reads a secret value: every secret, and every
sensitive output, shows as `********`. `--output` is the command's result (ADR-0056); `--format` is the document,
and `--output-path` where it goes.
"""

import sys
from pathlib import Path

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.deployment_document import DocumentTemplateError, render_document
from strata.controllers.deployment_output_view import status_line
from strata.controllers.deployment_report_view import MASK, DeploymentReport, deployment_report
from strata.controllers.solution_context import open_solution
from strata.utils.errors import UsageError


@click.command("report")
@click.argument("deployment")
@click.option(
    "--format",
    "report_format",
    type=click.Choice(("table", "markdown")),
    default=None,
    help="The document: table (console tables, the default) or markdown (the built-in Markdown, or --template). "
    "Ignored with --output json unless --output-path is set.",
)
@click.option(
    "--template",
    "template_path",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=None,
    help="A Jinja2 template that replaces the built-in Markdown. It receives the --output json data; no field "
    "in it holds a secret value. Implies --format markdown.",
)
@click.option(
    "--output-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the document to this file instead of stdout. Implies --format markdown.",
)
@click.option("--step", "steps", multiple=True, metavar="NAME", help="Only these execution steps. Repeatable.")
@click.option(
    "--path",
    "start_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@output_option
@quiet_option
@verbose_option
def deploy_report_command(
    deployment: str,
    report_format: str | None,
    template_path: Path | None,
    output_path: Path | None,
    steps: tuple[str, ...],
    start_path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Show the handover document for DEPLOYMENT: step outputs, the secrets each step used, and images.

    Secrets are listed by where to find them (vault and name, environment variable, ...), never by value:
    every secret, and every sensitive output, shows as ********. Only secrets a step used are listed, as
    recorded by 'deploy run'. Reads the workspace's output_store; no Terraform state and no store
    credentials are needed. A step deployed by an earlier strata version shows "secrets not recorded",
    with a warning.

    --format markdown renders the built-in Markdown document, or --template. The document goes to stdout,
    with the run's report on stderr, or to --output-path. With -o json and no --output-path, data carries
    deployment, workspace, environments, steps[] (outputs, secrets state, secret_keys), secrets[] (key,
    value, store, location, where, description, declared_in, used_by), images[] and audit (the last
    successful deploy: who, when, which commit and audit record, as deploy run recorded it); with
    --output-path, data names the file written.

    \b
    Exit codes:
      0  shown (warnings included: a secret held in source, an unknown location, an unrecorded step)
      2  bad arguments, an unknown DEPLOYMENT or --step, or a template that does not render
      3  the solution is invalid, or the workspace sets no output_store
      1  system failure (the output store is unreachable, or a record is corrupt)
    """
    document_format = report_format or ("markdown" if template_path or output_path else "table")
    # A Markdown document on stdout must be the only thing there; the run's report then goes to stderr.
    to_stdout = document_format == "markdown" and output == "console" and output_path is None
    with command_run(
        "deploy report", output=output, quiet=quiet, verbose=verbose, stream=sys.stderr if to_stdout else None
    ) as run:
        if document_format == "table" and (template_path or output_path):
            raise UsageError("--template and --output-path render a document: use --format markdown.")
        context = open_solution(resolve_work_path(start_path)).require_valid()
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, deployment=deployment)

        report = deployment_report(context, deployment, list(steps) or None)
        run.step(f"{len(report.steps)} step(s), {len(report.secrets)} secret(s), {len(report.images)} image(s)")

        document: str | None = None
        if document_format == "markdown" and (output == "console" or output_path is not None):
            template_source = template_path.read_text(encoding="utf-8") if template_path else None
            try:
                document = render_document(report, template_source=template_source)
            except DocumentTemplateError as exc:
                raise UsageError(f"The report template does not render: {exc}") from exc

        if output_path is not None and document is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(document, encoding="utf-8")
            run.step(f"wrote {output_path}")
        elif document is not None:
            click.echo(document, nl=False)
        elif output == "console":
            _print_report(report)

        run.report(report.diagnostics, root=context.root)
        run.ok = report.diagnostics.ok
        if isinstance(run.reporter, JsonReporter):
            if output_path is not None:
                run.reporter.data = {
                    "deployment": report.deployment,
                    "written_to": str(output_path),
                    "format": "markdown",
                }
            else:
                run.reporter.data = report.to_data()


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(header), *(len(row[i]) for row in rows)) for i, header in enumerate(headers[:-1])]
    for line in [headers, *rows]:
        cells = [cell.ljust(width) for cell, width in zip(line[:-1], widths, strict=True)]
        click.echo("  " + "  ".join([*cells, line[-1]]).rstrip())


def _print_report(report: DeploymentReport) -> None:
    click.echo(f"{report.deployment}  workspace {report.workspace}, environments {', '.join(report.environments)}")

    click.echo()
    click.echo("Step outputs")
    for step in report.steps:
        outputs = step.outputs
        click.echo(status_line(outputs))
        if outputs.status == "recorded" and outputs.outputs:
            _table(
                ("OUTPUT", "VALUE", "DESCRIPTION"),
                [
                    (row.name, MASK if row.sensitive else (row.value or ""), row.description or "")
                    for row in outputs.outputs
                ],
            )

    click.echo()
    click.echo("Secrets")
    if report.secrets:
        _table(
            ("SECRET", "VALUE", "STORE", "WHERE TO FIND IT", "USED BY", "DESCRIPTION"),
            [
                (row.key, row.value, row.store, row.where, ", ".join(row.used_by), row.description or "")
                for row in report.secrets
            ],
        )
    else:
        click.echo("  (none used)")
    for step in report.steps:
        if step.secrets in ("not_recorded", "mismatch") and step.outputs.status == "recorded":
            click.echo(f"  {step.step}: secrets not recorded")
        elif step.all_secrets:
            click.echo(f"  {step.step}: receives every declared secret")

    click.echo()
    click.echo("Images")
    if report.images:
        _table(
            ("NAME", "IMAGE", "TAG"),
            [(image["name"], str(image["image_name"]), str(image["image_tag"] or "-")) for image in report.images],
        )
    else:
        click.echo("  (no images)")
