#!/usr/bin/env python3
"""`strata sln init`/`strata sln update` — scaffold or refresh the files
strata itself owns in a solution repo (docs/design/solution-scaffolding.md).

First command group in v2 — every other command (`validate`/`values`/
`build`/`deploy`) is a flat top-level command. Deliberately not on the
CI-critical path (ADR-0020's Tier 2 classification of `init`/`repo`/
`profile`/`config`, still accurate — neither real consumer's CI ever calls
these): a one-time bootstrap / occasional-refresh operation a human runs.

`init` resolves its target directory directly (`resolve_work_path()`) —
unlike `validate`/`build`/`deploy`, it never walks up looking for an
existing `strata.yaml`, since it may be creating one for the first time.
`update` does walk up (`find_solution_root()`), matching every other
command's convention, since it requires an already-initialised solution.
"""

from pathlib import Path

import click

from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import CommandRun, command_run
from strata.controllers.scaffold_controller import ScaffoldController
from strata.controllers.solution_controller import find_solution_root
from strata.utils.diagnostics import Diagnostics
from strata.utils.errors import UsageError

_PATH_HELP = "Where to operate. Defaults to the current directory."


@click.group("sln")
def sln_command() -> None:
    """Scaffold or refresh a solution's strata-owned files."""


@sln_command.command("init")
@click.argument("name")
@click.option("--path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def init_command(name: str, path: Path | None, output: str, quiet: bool, verbose: bool) -> None:
    """Create strata.yaml (if missing) and scaffold NAME's solution files.

    Safe to re-run on an already-initialised solution: an existing
    strata.yaml is never overwritten (even to change its name), while
    package-owned scaffold files (.strata/) are still refreshed.
    """
    root = resolve_work_path(path)
    with command_run("sln init", output=output, quiet=quiet, verbose=verbose) as run:
        run.describe(name=name, root=root)
        controller = ScaffoldController(root)
        controller.init(name)
        _report(run, controller.messages, root=root)


@sln_command.command("update")
@click.option("--path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def update_command(path: Path | None, output: str, quiet: bool, verbose: bool) -> None:
    """Refresh package-owned scaffold files (.strata/) in an existing solution.

    Never touches user-owned files (.github/) that already exist — a
    customization made there is never silently overwritten.
    """
    start = resolve_work_path(path)
    with command_run("sln update", output=output, quiet=quiet, verbose=verbose) as run:
        root = find_solution_root(start)
        if root is None:
            raise UsageError(
                f"Not inside a strata solution: no strata.yaml found in '{start}' or any parent directory."
            )
        run.describe(root=root)
        controller = ScaffoldController(root)
        controller.update()
        _report(run, controller.messages, root=root)


def _report(run: CommandRun, messages: list[str], *, root: Path) -> None:
    """Render each scaffold message as a step, then a summary — the same
    two-part shape every other command's `run.report()` produces."""
    diagnostics = Diagnostics()
    for message in messages:
        run.step(message)
        diagnostics.info(message)
    run.report(diagnostics, root=root)
