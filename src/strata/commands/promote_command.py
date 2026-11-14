#!/usr/bin/env python3
"""`strata promote status <workspace>` / `strata promote apply <workspace>
<ring>` / `strata promote view <workspace>` — ship visibility before
enforcement (docs/work/promotion.md Phases 3/5/6).

`status` is a read-only report, same posture as `graph_command`/
`path_command`: Phase 1 (schema) only, never `.require_valid()` — an
unrelated document elsewhere having a Phase 2 problem should not stop an
operator from seeing where a *different* workspace's rollout stands.
Enforcement already lives in `strata validate` (Phase 2's
`validate_promotions()`, wired into `SolutionContext.resolve()`); this
command never fails on a promotion finding itself, only surfaces it
alongside the table — same "always exits 0, run `strata validate` for
pass/fail" contract `graph_command` already uses.

Still runs `resolve_deployment_chains()` (not the full `.resolve()`
pipeline) so the orphan-ring warning and the table's own membership are
accurate for a deployment that only gets `spec.version` through `extends` —
the one piece of Phase 2 this command's own correctness actually depends
on, independent of whether the rest of the solution validates cleanly.

`apply` is different: a real file mutation, not a report, so it refuses
outright (via `apply_promotion()`'s own check) when the target workspace
has an unresolved Phase 2 error — see that function's docstring.

`view` reuses the shared `-o/--output console|json` convention (console
renders the view as YAML — matching the doc's own `promotion-view.yaml`
naming — json puts it in the standard envelope's `data`, same as every
other command, so `STRATA_OUTPUT=json` keeps working here too), plus one
new, deliberately differently-named `--output-path PATH` option for
writing the bare view to a file. Named `--output-path`, not `--output`,
because `-o/--output` already means something else (console vs JSON
rendering) on every command in this CLI — reusing it here for "where to
write" would silently break that established meaning.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click
import yaml

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.deployment_resolution import resolve_deployment_chains
from strata.controllers.promotion_controller import (
    ApplyResult,
    PromotionStatusRow,
    apply_promotion,
    build_promotion_view,
    build_status_rows,
    rollout_workspaces,
)
from strata.controllers.solution_context import open_solution
from strata.controllers.version_composition import VersionMatrix
from strata.utils.diagnostics import Diagnostics

_PATH_HELP = "Where to start looking for the solution. Defaults to the current directory."


@click.group("promote")
def promote_command() -> None:
    """Inspect and manage a workspace's ring/order/wave rollout."""


@promote_command.command("status")
@click.argument("workspace", required=False, default=None)
@click.option(
    "--ring",
    "rings",
    multiple=True,
    metavar="RING",
    help="Only these rings (repeatable). Without WORKSPACE, every workspace in a rollout is shown, so "
    "'--ring prd' answers what the whole prd ring runs.",
)
@click.option("--path", "start_path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def promote_status(
    workspace: str | None, rings: tuple[str, ...], start_path: Path | None, output: str, quiet: bool, verbose: bool
) -> None:
    """Show where WORKSPACE's rollout stands — one row per pin, per wave, per ring.

    Without WORKSPACE, every workspace in a rollout, with a workspace column; with --ring, only those rings.

    Reads WORKSPACE's deployments: the tagged version document each lists says its ring, order and wave, and its
    rows are the pins the deployments run with, all their listed documents together, each with the document that
    owns it (OWNER; "shared" when deployments of another workspace list that document too). A workspace with no
    tagged documents at all (the common, single-app case) prints an empty table, not an error.

    \b
    Exit codes:
      0  always — a promotion inconsistency is shown, not failed on; run
         `strata validate` for pass/fail
      2  not inside a solution, or bad arguments
      1  system failure
    """
    with command_run("promote status", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            workspace=workspace or "(every workspace in a rollout)",
        )

        index = context.controller.index
        resolved_deployments, resolution_diagnostics = resolve_deployment_chains(index)
        diagnostics = Diagnostics()
        diagnostics.extend(resolution_diagnostics)
        # One version picture for every workspace shown, not one per workspace.
        matrix = VersionMatrix.build(index, resolved_deployments)
        workspaces = [workspace] if workspace else rollout_workspaces(index, resolved_deployments, matrix)
        rows: list[tuple[str, PromotionStatusRow]] = []
        for name in workspaces:
            view, promotion_diagnostics = build_promotion_view(name, index, resolved_deployments, matrix)
            diagnostics.extend(promotion_diagnostics)
            rows.extend((name, row) for row in build_status_rows(view) if not rings or row.ring in rings)
        if workspace is None:
            rows.sort(key=lambda item: (item[1].order, item[0]))
        run.step(f"found {len(rows)} row(s) across {len(workspaces)} workspace(s)")

        if output == "console":
            _print_rows([row for _, row in rows], workspaces=None if workspace else [name for name, _ in rows])

        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "workspace": workspace,
                "rings": list(rings),
                "rows": [asdict(row) if workspace else {"workspace": name, **asdict(row)} for name, row in rows],
            }
        run.ok = True


def _print_rows(rows: list[PromotionStatusRow], *, workspaces: list[str] | None = None) -> None:
    """Render the flat table: ring/order/wave/target/version/status, plus a
    '<- behind' marker. A 'target' column beyond the doc's illustrative
    5-column example is deliberate — a real document pins several targets
    at once (version_model.py's own 14-pin production example), so a row
    is per-pin; omitting the column would silently hide which pin a row
    is even about once more than one exists.
    """
    if not rows:
        click.echo("  (no tagged version documents for this workspace)")
        return

    def _cell(value: str | None) -> str:
        return value if value is not None else "-"

    headers: tuple[str, ...] = ("ring", "order", "wave", "target", "version", "status", "owner")
    table: list[tuple[str, ...]] = [
        (
            row.ring,
            str(row.order),
            _cell(row.wave),
            _cell(row.target if row.category is None else f"{row.category}/{row.target}"),
            _cell(row.value),
            _cell(row.status),
            _cell(row.owner) + (" (shared)" if row.shared else ""),
        )
        for row in rows
    ]
    if workspaces is not None:
        # Several workspaces in one table: say whose row it is, after the ring.
        headers = (*headers[:2], "workspace", *headers[2:])
        table = [(*cells[:2], name, *cells[2:]) for cells, name in zip(table, workspaces, strict=True)]
    widths = [max(len(headers[i]), *(len(r[i]) for r in table)) for i in range(len(headers))]

    click.echo("  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
    for row, cells in zip(rows, table, strict=True):
        line = "  " + "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True))
        click.echo(line + (click.style("  \u2190 behind", fg="yellow") if row.behind else ""))


@promote_command.command("apply")
@click.argument("workspace")
@click.argument("ring")
@click.option("--wave", default=None, help="Which wave to promote, required when RING has more than one.")
@click.option(
    "--shared",
    is_flag=True,
    default=False,
    help="Also write pins owned by a version document that deployments of other workspaces list too (a shared "
    "core document): promoting them moves those workspaces as well.",
)
@click.option("--path", "start_path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def promote_apply(
    workspace: str,
    ring: str,
    wave: str | None,
    shared: bool,
    start_path: Path | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Promote WORKSPACE into RING: copy the pins its deployments in RING's preceding order run.

    A mechanical copy-forward, not a judgment call: every pin WORKSPACE's deployments in RING run that the
    preceding ring's deployments also run gets the preceding value, written into the version document that owns
    it in RING — resetting `status` to `current` and `reviewed` to today. Never creates a new key. Skipped, and
    listed: a pin owned by a document other workspaces list too (unless --shared), and a pin owned by a document
    only some of RING's deployments list (a customer's own document). Zero pins copied is a real, reported
    outcome, not an error.

    \b
    Exit codes:
      0  applied (including zero pins copied, when nothing is common)
      2  bad arguments (unknown ring/wave, no preceding order, an
         unresolved promotion inconsistency in WORKSPACE), or not inside
         a solution
      1  system failure
    """
    with command_run("promote apply", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            workspace=workspace,
            ring=ring,
            wave=wave,
        )

        result = apply_promotion(workspace, ring, context.controller.index, wave=wave, shared=shared)
        run.step(f"copied {len(result.copied)} pin(s) from '{result.source_version}' into '{result.target_version}'")

        if output == "console":
            _print_apply_result(result, context.root)

        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "workspace": result.workspace,
                "ring": result.ring,
                "order": result.order,
                "wave": result.wave,
                "source_version": result.source_version,
                "target_version": result.target_version,
                "path": str(result.path),
                "copied": [{"category": c, "target": n, "value": v} for c, n, v in result.copied],
                "written": [{"category": c, "target": n, "value": v, "owner": o} for c, n, v, o in result.written],
                "skipped": [{"target": t, "owner": o or None, "reason": r} for t, o, r in result.skipped],
            }
        run.ok = True


def _print_apply_result(result: ApplyResult, root: Path) -> None:
    from strata.utils.layout import display_path

    click.echo(
        f"\n  \u2705  '{result.ring}'"
        + (f" (wave: {result.wave})" if result.wave else "")
        + f" <- '{result.source_version}' in {display_path(str(result.path), root)}\n"
    )
    if not result.copied:
        click.echo("  (no common pin keys between source and target \u2014 nothing copied)")
    for category, target, value, owner in result.written:
        into = f"  into {owner}" if owner != result.target_version else ""
        click.echo(f"  +  {category}.{target} = {value}  (status: current){into}")
    reasons = {
        "shared": "is shared by other workspaces; use --shared",
        "own document": "is a document only some deployments list (a customer's own); promote it by hand",
        "not pinned": "is not pinned in the target ring; add it with 'strata version set'",
        "source own documents": "comes only from customers' own documents in the source ring; no one value to promote",
    }
    for target, owner, reason in result.skipped:
        where = f" ({owner})" if owner else ""
        click.echo(f"  -  {target}{where} not promoted: {reasons.get(reason, reason)}")


@promote_command.command("view")
@click.argument("workspace")
@click.option(
    "--output-path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the exported PromotionView here instead of stdout.",
)
@click.option("--path", "start_path", type=click.Path(file_okay=False, path_type=Path), default=None, help=_PATH_HELP)
@output_option
@quiet_option
@verbose_option
def promote_view(
    workspace: str, output_path: Path | None, start_path: Path | None, output: str, quiet: bool, verbose: bool
) -> None:
    """Export WORKSPACE's full PromotionView — every tagged ring/wave, every
    resolved version — as a structured, tool-consumable snapshot.

    Deliberately separate from `promote status` (a glanceable table that can
    change shape freely) and from the audit trail (nothing here is hashed,
    referenced in any manifest, or triggered by `build run`/`deploy run`) —
    a point-in-time export invoked on purpose, never a side effect of
    anything else. Never commit the output to the repo: regenerate it
    on demand instead, the same reasoning that keeps this a computed view
    rather than a persisted `kind: promotion` document in the first place.

    Rendered as YAML in console mode (`-o console`, the default — matching
    this doc's own `promotion-view.yaml` naming) or inside the standard
    JSON envelope's `data` with `-o json`. `--output-path PATH` writes the
    same rendered content to a file instead of stdout; with it, stdout's
    own `data`/output stays a small `{written_to, rings}` confirmation
    rather than a second, duplicate copy of a potentially large view.

    \b
    Exit codes:
      0  always — a promotion inconsistency is shown, not failed on; run
         `strata validate` for pass/fail
      2  not inside a solution, or bad arguments
      1  system failure
    """
    with command_run("promote view", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(start_path))
        solution = context.controller.solution
        run.describe(solution=solution.meta.name if solution else "(unnamed)", root=context.root, workspace=workspace)

        view, diagnostics = build_promotion_view(workspace, context.controller.index)
        run.step(f"found {len(view.rings)} ring(s)")

        payload = view.model_dump(mode="json")
        fmt = "json" if output == "json" else "yaml"
        rendered = _render_view(payload, fmt)

        if output_path is not None:
            output_path.write_text(rendered, encoding="utf-8")
            run.step(f"wrote {output_path}")
        elif output == "console":
            click.echo(rendered)

        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = (
                {"written_to": str(output_path), "rings": len(view.rings)} if output_path is not None else payload
            )
        run.ok = True


def _render_view(payload: dict[str, Any], fmt: str) -> str:
    """Render `PromotionView.model_dump(mode='json')` as YAML or JSON text —
    the bare structure, no run envelope around it, so what it produces
    parses straight back into a `PromotionView` (this phase's own "Done
    when" round-trip criterion)."""
    if fmt == "json":
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return yaml.safe_dump(payload, sort_keys=False)
