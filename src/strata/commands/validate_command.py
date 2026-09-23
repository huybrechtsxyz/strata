#!/usr/bin/env python3
"""`strata validate` — check a whole solution.

Takes no `-f FILE`. v2 addresses documents by `(kind, name)` and discovers
them by walking up to `strata.yaml` (ADR-0015), so validation is always
"this solution", not "this file". Every real v1 invocation passed
`-f $DEPLOYMENT_FILE`, and that argument simply has nothing to refer to now.

There is no `--deep` either. v1 made cross-reference checking opt-in because
it needed an initialised workspace with an active profile; discovery hands v2
the whole index for free, so the precondition is gone. All four production
invocations passed `--deep` anyway — nobody wanted the shallow check, so the
complete one is the default. If a fast path is ever needed it becomes
`--schema-only`: the weak mode should be the one you ask for, not the one you
get by forgetting.
"""

from pathlib import Path

import click

from strata.commands.options import output_option, quiet_option, verbose_option
from strata.commands.run import command_run
from strata.controllers.solution_context import open_solution
from strata.utils.diagnostics import Diagnostics


@click.command("validate")
@click.argument(
    "path",
    type=click.Path(file_okay=False, path_type=Path),
    required=False,
)
@output_option
@quiet_option
@verbose_option
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Treat warnings as errors. For release pipelines that must not ship a stale pin.",
)
def validate_command(
    path: Path | None, output: str, quiet: bool, verbose: bool, strict: bool
) -> None:
    """Validate every document in the solution.

    Walks up from PATH (default: the current directory) to find the solution
    root, then checks every document it contains.

    \b
    Exit codes:
      0  valid
      2  not inside a solution, or bad arguments
      3  validation errors found
      1  system failure
    """
    with command_run("validate", output=output, quiet=quiet, verbose=verbose) as run:
        # Loading first means the header can name the solution. Validation is
        # a local file walk finishing well under a second; streaming progress
        # matters when a phase is genuinely slow (fetching remotes, deploying),
        # and the reporter already supports it.
        context = open_solution(path)
        solution = context.controller.solution
        document_count = len(context.controller.index)

        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
        )
        run.step(f"discovered {document_count} documents")

        if context.ok:
            run.step("schema validated")
            context.resolve()
            run.step("cross-document references and semantics checked")
        else:
            # Cross-document checks run over the index, and a document that
            # failed schema validation never entered it. Running them anyway
            # turns one real error into a screenful of derived noise.
            run.step("cross-document checks skipped — some documents did not load")

        run.report(context.diagnostics, root=context.root, document_count=document_count)
        run.ok = _is_ok(context.diagnostics, strict=strict)


def _is_ok(diagnostics: Diagnostics, *, strict: bool) -> bool:
    """Whether the run passed, honouring `--strict`."""
    if not diagnostics.ok:
        return False
    return not (strict and diagnostics.warnings)
