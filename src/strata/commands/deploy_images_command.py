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
from strata.controllers.effective_images import effective_images, unknown_image_modules
from strata.controllers.image_tag_policy import check_image_tag_policy, unknown_module_message
from strata.controllers.solution_context import open_solution
from strata.controllers.value_controller import resolve_deployment_workspace
from strata.integrations.resolved_context import EffectiveImage, UnknownImageModule
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
    Artifacts nothing references are not listed. A row with no tag is a warning, not a failure, unless
    the workspace sets `image_tag_policy`: then the policy judges every row (no tag, a floating tag such as
    `latest`, a tag pattern) and, with `enforcement: deny`, a breach exits 3.

    Nothing is built, resolved against a store or sent anywhere, so it is safe to run before
    `build run`: a pipeline can use it to copy the images to a registry first. With -o json,
    `data.images[]` carries origin, name, artifact, module, via, image_name, image_tag (the tag part),
    image_digest (the digest part of `tag@sha256:...`), image_ref, registry, configuration, tag_source,
    pin_version, pin_flag and integration (an artifact's `spec.integration`, by name: which credentials
    a copy needs; never resolved, so never a secret), delivered and delivery.

    DELIVERED says whether strata writes the image into what runs. A Helm module's service image is only
    declared: the chart decides which image runs, so it shows "no (helm module: the chart decides)". A Helm
    module that declares no image at all is listed under "Known only to Helm" (`data.unknown_image_modules[]`);
    `image_tag_policy.on_unknown_image` decides whether that warns or fails.

    \b
    Exit codes:
      0  listed
      2  bad arguments, an unknown DEPLOYMENT or --pin, or not inside a solution
      3  the solution is invalid, or image_tag_policy denies an image
      1  system failure
    """
    with command_run("deploy images", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path)).require_valid()
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, deployment=deployment)
        resolved, workspace = resolve_deployment_workspace(context, deployment, version_pin=pin)
        rows = effective_images(context, resolved, pinned_by_flag=pin is not None)
        unknown = unknown_image_modules(context, resolved)
        run.step(f"{len(rows)} image(s)")

        # With an image_tag_policy the policy judges the rows, and a `deny` breach fails the command so the
        # image copy at the start of a pipeline stops first. Without one a missing tag is only a warning.
        diagnostics = Diagnostics()
        if workspace.spec.image_tag_policy is not None:
            diagnostics = check_image_tag_policy(context, resolved, workspace, images=rows, unknown=unknown)
        else:
            for row in rows:
                if row.image_tag is None and row.image_digest is None:
                    diagnostics.warning(
                        f"'{row.name}' has no tag: neither a version pin nor the declaration supplies one.",
                        location=row.name,
                        code="image_without_tag",
                    )
            # Without a policy nothing asked for a judgement, so a module strata cannot see into is only noted.
            for module in unknown:
                diagnostics.info(unknown_module_message(module), location=module.module, code="image_unknown_to_strata")

        if output == "console":
            _print_rows(rows)
            _print_unknown(unknown)
        run.report(diagnostics, root=context.root)
        run.ok = diagnostics.ok
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "deployment": deployment,
                "version": resolved.spec.version,
                "pin": pin,
                "images": [asdict(row) for row in rows],
                "unknown_image_modules": [asdict(module) for module in unknown],
            }


def _source(row: EffectiveImage) -> str:
    if row.tag_source == "pin":
        return f"{'--pin' if row.pin_flag else 'pin'} {row.pin_version}"
    return row.tag_source


def _delivered(row: EffectiveImage) -> str:
    return "yes" if row.delivered else f"no ({row.delivery})"


def _print_unknown(unknown: list[UnknownImageModule]) -> None:
    if not unknown:
        return
    click.echo("")
    click.echo("  Known only to Helm (declare no images, so none of theirs is listed):")
    for module in unknown:
        chart = " ".join(part for part in (module.chart, module.chart_version) if part)
        click.echo(f"    {module.module}  namespace {module.namespace}" + (f"  chart {chart}" if chart else ""))


def _print_rows(rows: list[EffectiveImage]) -> None:
    if not rows:
        click.echo("  (no images)")
        return
    # DIGEST, DELIVERED and INTEGRATION only when a row needs one, so the usual table does not gain a column of
    # "-" (or of "yes").
    with_digest = any(row.image_digest for row in rows)
    with_delivered = any(not row.delivered for row in rows)
    with_integration = any(row.integration for row in rows)
    headers: tuple[str, ...] = ("NAME", "ORIGIN", "IMAGE", "TAG", *(("DIGEST",) if with_digest else ()), "SOURCE")
    headers += ("DELIVERED",) if with_delivered else ()
    headers += ("INTEGRATION", "USED BY") if with_integration else ("USED BY",)
    table = [
        (
            row.name,
            row.origin,
            row.image_name,
            row.image_tag or "-",
            *((row.image_digest or "-",) if with_digest else ()),
            _source(row),
            *((_delivered(row),) if with_delivered else ()),
            *((row.integration or "-",) if with_integration else ()),
            ", ".join(row.via) or "-",
        )
        for row in rows
    ]
    widths = [max(len(header), *(len(line[i]) for line in table)) for i, header in enumerate(headers[:-1])]
    for line in [headers, *table]:
        cells = [cell.ljust(width) for cell, width in zip(line[:-1], widths, strict=True)]
        click.echo("  " + "  ".join([*cells, line[-1]]))
