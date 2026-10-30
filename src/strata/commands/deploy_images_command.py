#!/usr/bin/env python3
"""`strata deploy images DEPLOYMENT` - the container images a deployment will run, after version pins.

docs/work/effective-images.md. Reads the solution only: no build, no store, no network, no secret.
It calls `require_valid()` like the other `deploy` subcommands, because a dangling environment or
module reference would silently drop rows from the list.
"""

from dataclasses import asdict
from pathlib import Path

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.effective_images import effective_images
from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import resolve_deployment_workspace
from strata.integrations.resolved_context import EffectiveImage
from strata.utils.diagnostics import Diagnostics


@click.command("images")
@click.argument("deployment")
@click.option(
    "--path",
    "start_path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@click.option(
    "--pin",
    default=None,
    metavar="VERSION",
    help="Override spec.version with this kind: version document for this invocation only. "
    "Pass the same value to 'build run --pin' and 'deploy run --pin'.",
)
@output_option
@quiet_option
@verbose_option
def deploy_images_command(
    deployment: str, start_path: Path | None, pin: str | None, output: str, quiet: bool, verbose: bool
) -> None:
    """List the container images DEPLOYMENT will run, with the tag after any version pin.

    Covers artifacts (a `store: artifact` variable or a module service's `artifact`) and module
    services' literal images. For each: the image name, the effective tag, where the tag came
    from (a version document, `--pin`, the artifact's own `image_tag`, or nowhere) and what uses it.
    Artifacts nothing references are not listed. A row with no tag is a warning, not a failure.

    Nothing is built, resolved against a store or sent anywhere, so it is safe to run before
    `build run`: a pipeline can use it to copy the images to a registry first. With -o json,
    `data.images[]` carries origin, name, artifact, module, via, image_name, image_tag, image_ref,
    registry, configuration, tag_source, pin_version and pin_flag.

    \b
    Exit codes:
      0  listed
      2  bad arguments, an unknown DEPLOYMENT or --pin, or not inside a solution
      3  the solution is invalid
      1  system failure
    """
    with command_run("deploy images", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path)).require_valid()
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, deployment=deployment)
        resolved, _workspace = resolve_deployment_workspace(context, deployment, version_pin=pin)
        rows = effective_images(context, resolved, pinned_by_flag=pin is not None)
        run.step(f"{len(rows)} image(s)")

        diagnostics = Diagnostics()
        for row in rows:
            if row.image_tag is None:
                diagnostics.warning(
                    f"'{row.name}' has no tag: neither a version pin nor the declaration supplies one.",
                    location=row.name,
                    code="image_without_tag",
                )

        if output == "console":
            _print_rows(rows)
        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "deployment": deployment,
                "version": resolved.spec.version,
                "pin": pin,
                "images": [asdict(row) for row in rows],
            }


def _source(row: EffectiveImage) -> str:
    if row.tag_source == "pin":
        return f"{'--pin' if row.pin_flag else 'pin'} {row.pin_version}"
    return row.tag_source


def _print_rows(rows: list[EffectiveImage]) -> None:
    if not rows:
        click.echo("  (no images)")
        return
    headers = ("NAME", "ORIGIN", "IMAGE", "TAG", "SOURCE", "USED BY")
    table = [
        (row.name, row.origin, row.image_name, row.image_tag or "-", _source(row), ", ".join(row.via) or "-")
        for row in rows
    ]
    widths = [max(len(header), *(len(line[i]) for line in table)) for i, header in enumerate(headers[:-1])]
    for line in [headers, *table]:
        cells = [cell.ljust(width) for cell, width in zip(line[:-1], widths, strict=True)]
        click.echo("  " + "  ".join([*cells, line[-1]]))
