#!/usr/bin/env python3
"""`strata deploy run`/`setup`/`check` — execute (or partially execute) a
deployment's workspace provisioners (Terraform plan/apply, the Compose/Helm
equivalents) against `build run`'s already-rendered output
(docs/design/deploy-command.md, docs/design/deploy-pipeline-stage-commands.md).

No `-f FILE` — same reasoning as `build_command`/`validate_command`/
`values_command`: v2 addresses documents by `(kind, name)`, so the argument
is the deployment's name, resolved against the solution found by walking up
from `--path` (default: cwd).

Never renders anything itself — `build run` is a separate, prior step;
`deploy_controller.deploy_run()` only calls `plan`/`deploy` against a
directory `build run` already wrote to (ADR-0022 D4's render-vs-execute
split).
"""

import copy
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar

import click

from strata.commands.deploy_images_command import deploy_images_command
from strata.commands.deploy_list_command import deploy_list_command
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import command_run
from strata.controllers.audit_run import finalize_and_distribute_deploy_audit
from strata.controllers.deploy_controller import deploy_run
from strata.controllers.solution_context import open_solution
from strata.utils.errors import UsageError
from strata.utils.layout import build_dir

_F = TypeVar("_F", bound=Callable[..., object])


@click.group("deploy")
def deploy_command() -> None:
    """List deployments and their images, or execute a deployment's workspace provisioners."""


deploy_command.add_command(deploy_list_command)
deploy_command.add_command(deploy_images_command)


def _shared_deploy_options(follow_default: str | None = None) -> Callable[[_F], _F]:
    """The `--path`/`--build-path`/`--follow`/`--stage`/`--scope`/`--pin`/
    `--output-max-age` options every deploy subcommand shares verbatim
    (docs/design/deploy-pipeline-stage-commands.md) — kept as one decorator
    factory so `setup`/`check`/`apply`/`plan` cannot drift from `run`'s own
    option definitions. `run` itself predates this helper and keeps its
    options declared inline, unchanged, to avoid touching a stable, already
    well-tested command for a pure internal refactor. A factory (not a bare
    decorator) only because `--follow`'s own default varies by command —
    `plan` defaults it to `"console"` (streams the diff like real
    `terraform plan`) where every other command defaults it to `None`
    (opt-in streaming); every other option is identical regardless.
    """

    def _decorate(f: _F) -> _F:
        f = click.option(
            "--output-max-age",
            type=click.IntRange(min=1),
            default=None,
            metavar="SECONDS",
            help="Trust window for durable cross-run step outputs, overriding the workspace's own "
            "'output_store_max_age' for this invocation only. A durable record older than this is a hard "
            "failure, never a silent stale read. Omit to use the workspace setting (itself optional, "
            "no staleness enforcement by default).",
        )(f)
        f = click.option(
            "--pin",
            default=None,
            metavar="VERSION",
            help="Override spec.version with this kind: version document for this invocation only "
            "(ADR-0019) — zero persisted mutation, the deployment document on disk is never touched. "
            "Covers the artifacts pin category (the one that resolves live, at deploy time); pass the "
            "same value to 'build run --pin' too so a deploy matches the build it is executing.",
        )(f)
        f = click.option(
            "--scope",
            default=None,
            metavar="LABEL",
            help="Restrict to steps whose ProvisioningStepModel.scope matches LABEL.",
        )(f)
        f = click.option(
            "--stage",
            default=None,
            metavar="NAME",
            help="Restrict to the one workspace execution step named NAME.",
        )(f)
        f = click.option(
            "--follow",
            "-f",
            is_flag=False,
            flag_value="console",
            default=follow_default,
            type=click.Choice(["console", "ndjson"]),
            help="Stream subprocess output live. Bare --follow streams tool-prefixed console "
            "text (to stdout normally; automatically rerouted to stderr under --output json, "
            "so stdout stays exactly one document). --follow ndjson streams newline-delimited "
            "JSON events to stdout instead — requires --output json (docs/archive/ndjson-output.md)."
            + ("" if follow_default is None else " Defaults to 'console' for this command."),
        )(f)
        f = click.option(
            "--build-path",
            type=click.Path(file_okay=False, path_type=Path),
            default=None,
            help="Where `build run` already rendered artifacts. Defaults to '<solution root>/build/"
            "<deployment>' — the same default `build run` itself uses. Never re-rendered here.",
        )(f)
        f = click.option(
            "--path",
            type=click.Path(file_okay=False, path_type=Path),
            default=None,
            help="Where to start looking for the solution. Defaults to the current directory.",
        )(f)
        return f

    return _decorate


def _line_follow_callbacks(
    follow: str | None, output: str, default_on_step: Callable[[str], None]
) -> tuple[Callable[[str], None], Callable[[str, str, str], None] | None]:
    """Builds `(on_step, on_line)` for `--follow console`/`--follow ndjson`,
    shared by every deploy subcommand that streams subprocess output
    (extracted from `deploy run`'s own original inline block — behaviour
    unchanged, docs/archive/ndjson-output.md).
    """
    on_line: Callable[[str, str, str], None] | None = None
    on_step = default_on_step
    if follow == "console":

        def on_line(tool: str, stream: str, text: str) -> None:
            to_stderr = stream == "stderr" or output == "json"
            click.secho(f"      {tool} │ {text}", fg=("yellow" if stream == "stderr" else "cyan"), err=to_stderr)

    elif follow == "ndjson":

        def on_line(tool: str, stream: str, text: str) -> None:
            click.echo(
                json.dumps(
                    {
                        "event": "line",
                        "tool": tool,
                        "stream": stream,
                        "text": text,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                )
            )

        def on_step(message: str) -> None:
            click.echo(
                json.dumps(
                    {"event": "progress", "message": message, "ts": datetime.now(timezone.utc).isoformat()},
                    ensure_ascii=False,
                )
            )
            default_on_step(message)

    return on_step, on_line


def _change_reference_options(f: _F) -> _F:
    """The 8 `--change-*` options shared by every deploy subcommand that
    makes a real infrastructure change and so needs an audit trail entry
    (`run`/`apply` — never `setup`/`check`/`plan`, which are
    non-mutating). Factored into one decorator because these feed
    `ChangeReferenceModel` (docs/design/audit-trail.md) — a
    compliance-relevant field set where two independently-maintained
    copies of the same 8 options drifting apart (a typo'd help string, a
    missing metavar) is a worse outcome than for purely ergonomic options,
    so unlike `_shared_deploy_options` this one IS also retrofitted onto
    `run` below, despite `run` otherwise being left alone on principle.
    """
    f = click.option(
        "--change-approved-at",
        default=None,
        metavar="TEXT",
        help="ISO-8601 timestamp of the approval itself (in the tracker), not of this invocation. Required "
        "together with --change-approved-by.",
    )(f)
    f = click.option(
        "--change-approved-by",
        default=None,
        metavar="TEXT",
        help="Who approved this change in the tracker — distinct from whoever ran this deploy or supplied this "
        "reference. Operator-supplied, not independently verified. Required together with --change-approved-at, "
        "and only meaningful alongside --change-system/--change-id/--change-reason.",
    )(f)
    f = click.option(
        "--change-url",
        default=None,
        metavar="TEXT",
        help="Link to the change record. Optional.",
    )(f)
    f = click.option(
        "--change-title",
        default=None,
        metavar="TEXT",
        help="Snapshot of the change record's title at invocation time. Optional.",
    )(f)
    f = click.option(
        "--change-classification",
        default=None,
        metavar="TEXT",
        help="Change classification, e.g. 'emergency'/'normal'. Optional.",
    )(f)
    f = click.option(
        "--change-reason",
        default=None,
        metavar="TEXT",
        help="Operator-supplied justification for this deployment. Required together with --change-system/--change-id.",
    )(f)
    f = click.option(
        "--change-id",
        default=None,
        metavar="TEXT",
        help="Change/ticket identifier in the tracker, e.g. 'OPS-1234'. Required together with "
        "--change-system/--change-reason.",
    )(f)
    f = click.option(
        "--change-system",
        default=None,
        metavar="TEXT",
        help="Change/ticket tracker, e.g. 'jira', 'azure_devops', 'servicenow', or an internal name. "
        "Required together with --change-id/--change-reason, or omit all three — docs/design/"
        "audit-trail.md's ChangeReferenceModel.",
    )(f)
    return f


def _validate_change_reference_flags(
    *,
    change_system: str | None,
    change_id: str | None,
    change_reason: str | None,
    change_approved_by: str | None,
    change_approved_at: str | None,
) -> None:
    """Raises `UsageError` for an incomplete `--change-*`/`--change-approved-*`
    group — shared by `run`/`apply` (extracted from `deploy run`'s own
    original inline checks, same reasoning as `_change_reference_options`).
    """
    change_fields = (change_system, change_id, change_reason)
    if any(change_fields) and not all(change_fields):
        raise UsageError("--change-system/--change-id/--change-reason must be supplied together, or not at all.")

    approval_fields = (change_approved_by, change_approved_at)
    if any(approval_fields) and not all(approval_fields):
        raise UsageError("--change-approved-by/--change-approved-at must be supplied together, or not at all.")
    if any(approval_fields) and not all(change_fields):
        raise UsageError(
            "--change-approved-by/--change-approved-at require --change-system/--change-id/--change-reason "
            "to also be supplied — an approval needs a change reference to approve."
        )


@deploy_command.command("run")
@click.argument("deployment")
@click.option(
    "--path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to start looking for the solution. Defaults to the current directory.",
)
@click.option(
    "--build-path",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where `build run` already rendered artifacts. Defaults to '<solution root>/build/"
    "<deployment>' — the same default `build run` itself uses. Never re-rendered here.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Currently has no effect (docs/design/deploy-command.md's Remaining Work item 7) — "
    "v1 uses it to bypass advisory approval gates (AI plan review, promotion overrides), "
    "neither of which v2 has built yet. Accepted for CLI parity with real invocations, which "
    "always pass it; applying a saved plan file never needs '-auto-approve' regardless.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Run init/validate/plan for real (saved to <step>.tfplan) and stop before deploy. "
    "A step whose tool doesn't meaningfully support a preview is warned about and skipped, "
    "not a hard failure. Mutually exclusive with --smoke-test (zero tool contact at all).",
)
@click.option(
    "--smoke-test",
    is_flag=True,
    default=False,
    help="Report which steps would run without calling init/validate/plan/deploy at all "
    "— zero tool contact, no auth needed. Mutually exclusive with --dry-run.",
)
@click.option(
    "--follow",
    "-f",
    is_flag=False,
    flag_value="console",
    default=None,
    type=click.Choice(["console", "ndjson"]),
    help="Stream subprocess output live. Bare --follow streams tool-prefixed console "
    "text (to stdout normally; automatically rerouted to stderr under --output json, "
    "so stdout stays exactly one document). --follow ndjson streams newline-delimited "
    "JSON events to stdout instead — requires --output json (docs/archive/ndjson-output.md).",
)
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Restrict to the one workspace execution step named NAME.",
)
@click.option(
    "--scope",
    default=None,
    metavar="LABEL",
    help="Restrict to steps whose ProvisioningStepModel.scope matches LABEL.",
)
@click.option(
    "--pin",
    default=None,
    metavar="VERSION",
    help="Override spec.version with this kind: version document for this invocation only "
    "(ADR-0019) — zero persisted mutation, the deployment document on disk is never touched. "
    "Covers the artifacts pin category (the one that resolves live, at deploy time); pass the "
    "same value to 'build run --pin' too so a deploy matches the build it is executing.",
)
@click.option(
    "--output-max-age",
    type=click.IntRange(min=1),
    default=None,
    metavar="SECONDS",
    help="Trust window for durable cross-run step outputs, overriding the workspace's own "
    "'output_store_max_age' for this invocation only. A durable record older than this is a hard "
    "failure, never a silent stale read. Omit to use the workspace setting (itself optional, "
    "no staleness enforcement by default).",
)
@_change_reference_options
@output_option
@quiet_option
@verbose_option
def deploy_run_command(
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    force: bool,
    dry_run: bool,
    smoke_test: bool,
    follow: str | None,
    stage: str | None,
    scope: str | None,
    pin: str | None,
    output_max_age: int | None,
    change_system: str | None,
    change_id: str | None,
    change_reason: str | None,
    change_classification: str | None,
    change_title: str | None,
    change_url: str | None,
    change_approved_by: str | None,
    change_approved_at: str | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Execute DEPLOYMENT's workspace provisioners against `build run`'s output.

    \b
    Exit codes:
      0  every filtered step deployed successfully
      2  bad arguments, DEPLOYMENT does not exist, --pin names an unknown
         version document, or not inside a solution
      3  a declared value failed to resolve, a filtered step's tool is not
         available on PATH, or a step's plan/deploy failed

    Run 'strata build run DEPLOYMENT' first — this never renders anything,
    only executes what is already on disk at --build-path.
    """
    with command_run("deploy run", output=output, quiet=quiet, verbose=verbose, compact=(follow == "ndjson")) as run:
        if dry_run and smoke_test:
            raise UsageError("--dry-run and --smoke-test are mutually exclusive — pick one.")

        if follow == "ndjson" and output != "json":
            raise UsageError("--follow ndjson requires --output json.")

        _validate_change_reference_flags(
            change_system=change_system,
            change_id=change_id,
            change_reason=change_reason,
            change_approved_by=change_approved_by,
            change_approved_at=change_approved_at,
        )

        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        target = build_path or build_dir(context.root, deployment)

        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
            build_path=target,
        )

        execution_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        # Live subprocess-line streaming — reroutes to stderr whenever stdout
        # must stay a single parseable JSON document, matching --verbose's own
        # real "logs to stderr, stdout stays clean" precedent instead of merely
        # citing it. Console mode keeps today's colour-coded dual-stream split
        # (stdout for stdout lines, stderr for stderr lines). `--follow ndjson`
        # (requires --output json, checked above) instead emits one JSON event
        # per line to stdout — `stream` stays metadata describing which stream
        # the *subprocess* line came from, never a routing instruction for our
        # own output (docs/archive/ndjson-output.md). `on_step`'s own progress
        # messages get the same treatment: a generic "progress" event per call
        # — a deliberate first-cut scope-down from v1's richer structured
        # step_start/step_end events, since v2's on_step only ever carries a
        # flat message string, not a separate step name/tool (ndjson-output.md's
        # own "Per-line event shape" design question).
        on_step, on_line = _line_follow_callbacks(follow, output, run.step)

        diagnostics = deploy_run(
            context,
            deployment,
            target,
            force=force,
            dry_run=dry_run,
            smoke_test=smoke_test,
            stage=stage,
            scope=scope,
            on_step=on_step,
            on_line=on_line,
            pin=pin,
            output_max_age=output_max_age,
        )

        # Audit trail — finalize + write locally + distribute (docs/design/
        # audit-trail.md's Layer 2). Never affects which deploy stages ran or
        # their recorded outcome; a required sink's push failure can still
        # fail this command's own exit code, merged in below. `smoke_test`
        # is zero-contact, same as `dry_run` — both must no-op the manifest/
        # metrics write.
        audit_diagnostics = finalize_and_distribute_deploy_audit(
            context,
            deployment,
            target,
            execution_id=execution_id,
            started_at=started_at,
            run_diagnostics=diagnostics,
            dry_run=dry_run or smoke_test,
            pin=pin,
            change_system=change_system,
            change_id=change_id,
            change_reason=change_reason,
            change_classification=change_classification,
            change_title=change_title,
            change_url=change_url,
            change_approved_by=change_approved_by,
            change_approved_at=change_approved_at,
        )
        diagnostics.extend(audit_diagnostics)

        run.report(diagnostics, root=context.root)
        run.ok = diagnostics.ok


def _run_partial_deploy(
    *,
    command_name: str,
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    follow: str | None,
    stage: str | None,
    scope: str | None,
    pin: str | None,
    output_max_age: int | None,
    output: str,
    quiet: bool,
    verbose: bool,
    stop_after: Literal["setup", "check"] | None = None,
    dry_run: bool = False,
) -> None:
    """Shared body for `deploy setup`/`deploy check` (and its hidden `deploy
    validate` alias)/`deploy plan` — none of these reach a real `deploy()`
    call (`setup`/`check` stop early via `stop_after`, `plan` stops before
    `deploy()` via `dry_run=True`), and so none needs an audit trail
    (docs/design/deploy-pipeline-stage-commands.md: unlike `run`/`apply`,
    none of these three makes a real infrastructure change). Exactly one of
    `stop_after`/`dry_run` is ever set by a real caller below — kept as two
    explicit, correctly-typed parameters (not a generic `**kwargs` blob)
    so mypy can still check every `deploy_run()` call site.
    """
    with command_run(command_name, output=output, quiet=quiet, verbose=verbose, compact=(follow == "ndjson")) as run:
        if follow == "ndjson" and output != "json":
            raise UsageError("--follow ndjson requires --output json.")

        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        target = build_path or build_dir(context.root, deployment)

        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
            build_path=target,
        )

        on_step, on_line = _line_follow_callbacks(follow, output, run.step)

        diagnostics = deploy_run(
            context,
            deployment,
            target,
            stop_after=stop_after,
            dry_run=dry_run,
            stage=stage,
            scope=scope,
            on_step=on_step,
            on_line=on_line,
            pin=pin,
            output_max_age=output_max_age,
        )

        run.report(diagnostics, root=context.root)
        run.ok = diagnostics.ok


@deploy_command.command("setup")
@click.argument("deployment")
@_shared_deploy_options()
@output_option
@quiet_option
@verbose_option
def deploy_setup_command(
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    follow: str | None,
    stage: str | None,
    scope: str | None,
    pin: str | None,
    output_max_age: int | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Run only the setup phase (`terraform init`, or equivalent) for DEPLOYMENT's workspace provisioners.

    \b
    Exit codes:
      0  every filtered step's setup phase completed successfully
      2  bad arguments, DEPLOYMENT does not exist, --pin names an unknown
         version document, or not inside a solution
      3  a declared value failed to resolve, a filtered step's tool is not
         available on PATH, or a step's setup phase failed

    A container-capable (Helm/Compose) step has no separate setup phase and
    is reported as skipped, not failed (docs/design/deploy-pipeline-stage-commands.md).
    """
    _run_partial_deploy(
        command_name="deploy setup",
        stop_after="setup",
        deployment=deployment,
        path=path,
        build_path=build_path,
        follow=follow,
        stage=stage,
        scope=scope,
        pin=pin,
        output_max_age=output_max_age,
        output=output,
        quiet=quiet,
        verbose=verbose,
    )


@deploy_command.command("check")
@click.argument("deployment")
@_shared_deploy_options()
@output_option
@quiet_option
@verbose_option
def deploy_check_command(
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    follow: str | None,
    stage: str | None,
    scope: str | None,
    pin: str | None,
    output_max_age: int | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Run setup then check (`terraform init`/`validate`, or equivalent) for DEPLOYMENT's workspace provisioners.

    \b
    Exit codes:
      0  every filtered step's setup/check phases completed successfully
      2  bad arguments, DEPLOYMENT does not exist, --pin names an unknown
         version document, or not inside a solution
      3  a declared value failed to resolve, a filtered step's tool is not
         available on PATH, or a step's setup/check phase failed

    Not the same thing as the top-level 'strata validate' command, which
    validates the whole solution's documents against their schemas —
    'deploy check' only asks the underlying IaC tool whether one
    deployment's already-rendered config is well-formed. A container-
    capable (Helm/Compose) step has no separate setup/check phase and is
    reported as skipped, not failed. Also available as the hidden alias
    'deploy validate', for anyone reaching for Terraform's own vocabulary
    (docs/design/deploy-pipeline-stage-commands.md).
    """
    _run_partial_deploy(
        command_name="deploy check",
        stop_after="check",
        deployment=deployment,
        path=path,
        build_path=build_path,
        follow=follow,
        stage=stage,
        scope=scope,
        pin=pin,
        output_max_age=output_max_age,
        output=output,
        quiet=quiet,
        verbose=verbose,
    )


# Hidden `deploy validate` alias of `deploy check`
# (docs/design/deploy-pipeline-stage-commands.md's naming-collision
# resolution) — a shallow copy so the alias can be `hidden=True`
# independently of the canonical, documented `check` command sharing the
# same callback; Click does not mutate `cmd.name` on `add_command(cmd,
# name=...)`, so the copy's own `.name` is set explicitly too (otherwise
# `--help`/usage text on the alias would still read "check").
_deploy_validate_command = copy.copy(deploy_check_command)
_deploy_validate_command.name = "validate"
_deploy_validate_command.hidden = True
deploy_command.add_command(_deploy_validate_command)


@deploy_command.command("apply")
@click.argument("deployment")
@_shared_deploy_options()
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Currently has no effect (docs/design/deploy-command.md's Remaining Work item 7) — "
    "v1 uses it to bypass advisory approval gates (AI plan review, promotion overrides), "
    "neither of which v2 has built yet. Accepted for CLI parity with real invocations, which "
    "always pass it; applying a saved plan file never needs '-auto-approve' regardless.",
)
@_change_reference_options
@output_option
@quiet_option
@verbose_option
def deploy_apply_command(
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    force: bool,
    follow: str | None,
    stage: str | None,
    scope: str | None,
    pin: str | None,
    output_max_age: int | None,
    change_system: str | None,
    change_id: str | None,
    change_reason: str | None,
    change_classification: str | None,
    change_title: str | None,
    change_url: str | None,
    change_approved_by: str | None,
    change_approved_at: str | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Apply an already-saved plan (`terraform apply <planfile>`, or equivalent) for DEPLOYMENT's workspace provisioners.

    \b
    Exit codes:
      0  every filtered step applied successfully
      2  bad arguments, DEPLOYMENT does not exist, --pin names an unknown
         version document, or not inside a solution
      3  a declared value failed to resolve, a filtered step's tool is not
         available on PATH, no saved plan exists for a filtered step, or a
         step's apply failed

    Never recomputes a plan — run 'strata deploy plan DEPLOYMENT' first.
    Applies exactly the '<step>.tfplan' that command saved, the same
    safeguard a plan/apply CI approval gate relies on: what gets applied is
    never a fresher plan than the one that was reviewed. A container-
    capable (Helm/Compose) step has no saved-plan-file concept at all and
    is deployed for real regardless, with no such guarantee — a tool
    limitation, not something this command can paper over
    (docs/design/deploy-pipeline-stage-commands.md).
    """
    with command_run("deploy apply", output=output, quiet=quiet, verbose=verbose, compact=(follow == "ndjson")) as run:
        if follow == "ndjson" and output != "json":
            raise UsageError("--follow ndjson requires --output json.")

        _validate_change_reference_flags(
            change_system=change_system,
            change_id=change_id,
            change_reason=change_reason,
            change_approved_by=change_approved_by,
            change_approved_at=change_approved_at,
        )

        context = open_solution(resolve_work_path(path)).require_valid()
        solution = context.controller.solution
        target = build_path or build_dir(context.root, deployment)

        run.describe(
            solution=solution.meta.name if solution else "(unnamed)",
            root=context.root,
            deployment=deployment,
            build_path=target,
        )

        execution_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        on_step, on_line = _line_follow_callbacks(follow, output, run.step)

        diagnostics = deploy_run(
            context,
            deployment,
            target,
            force=force,
            use_saved_plan=True,
            stage=stage,
            scope=scope,
            on_step=on_step,
            on_line=on_line,
            pin=pin,
            output_max_age=output_max_age,
        )

        # Audit trail — same as `deploy run` (docs/design/audit-trail.md's
        # Layer 2): `apply` makes a real infrastructure change, exactly
        # like `run`'s own apply tail, so a pipeline split into
        # plan/apply stages must not silently lose audit coverage relative
        # to a monolithic `deploy run`. Never a dry run here — `dry_run`
        # is always `False`, unlike `run`, which can itself be dry.
        audit_diagnostics = finalize_and_distribute_deploy_audit(
            context,
            deployment,
            target,
            execution_id=execution_id,
            started_at=started_at,
            run_diagnostics=diagnostics,
            dry_run=False,
            pin=pin,
            change_system=change_system,
            change_id=change_id,
            change_reason=change_reason,
            change_classification=change_classification,
            change_title=change_title,
            change_url=change_url,
            change_approved_by=change_approved_by,
            change_approved_at=change_approved_at,
        )
        diagnostics.extend(audit_diagnostics)

        run.report(diagnostics, root=context.root)
        run.ok = diagnostics.ok


@deploy_command.command("plan")
@click.argument("deployment")
@_shared_deploy_options(follow_default="console")
@output_option
@quiet_option
@verbose_option
def deploy_plan_command(
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    follow: str | None,
    stage: str | None,
    scope: str | None,
    pin: str | None,
    output_max_age: int | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Preview changes (`terraform plan`, or equivalent) for DEPLOYMENT's workspace provisioners, saving the result for 'deploy apply'.

    \b
    Exit codes:
      0  every filtered step's setup/check/plan phases completed successfully
      2  bad arguments, DEPLOYMENT does not exist, --pin names an unknown
         version document, or not inside a solution
      3  a declared value failed to resolve, a filtered step's tool is not
         available on PATH, or a step's setup/check/plan phase failed

    Streams the plan's own diff to the console by default — unlike every
    other deploy subcommand, which only streams under --follow — so this
    behaves like running 'terraform plan' directly. Pass --output json to
    reroute it to stderr instead (stdout stays one JSON document); there
    is currently no flag to silence it entirely, matching real 'terraform
    plan' run directly. Never touches real infrastructure and never needs
    --force. A container-capable (Helm/Compose) step gets the equivalent
    real-preview treatment. Saves '<step>.tfplan' for a later 'deploy
    apply' to apply exactly this plan — never a fresher one
    (docs/design/deploy-pipeline-stage-commands.md).
    """
    _run_partial_deploy(
        command_name="deploy plan",
        dry_run=True,
        deployment=deployment,
        path=path,
        build_path=build_path,
        follow=follow,
        stage=stage,
        scope=scope,
        pin=pin,
        output_max_age=output_max_age,
        output=output,
        quiet=quiet,
        verbose=verbose,
    )
