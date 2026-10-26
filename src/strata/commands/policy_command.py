#!/usr/bin/env python3
"""`strata policy list | check | findings` (docs/work/security-gates-design.md D7).

Thin layer over `controllers/policy_controller.py`. `check` evaluates the deploy-phase gates now,
exactly as `deploy run` would, so a pipeline can fail before it starts deploying; `list` and
`findings` are read-only.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, cast

import click

from strata.commands.json_output import JsonReporter
from strata.commands.options import output_option, quiet_option, resolve_work_path, verbose_option
from strata.commands.run import CommandRun, command_run
from strata.controllers.policy_controller import PolicyInfo, check_policies, list_policies, policy_findings
from strata.controllers.security_gate_controller import ImageFindingsReport
from strata.controllers.solution_context import SolutionContext, open_solution
from strata.integrations.security_findings import SecurityIssue
from strata.models.audit_manifest_model import ManifestPolicyResultModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.layout import build_dir

_F = TypeVar("_F", bound=Callable[..., object])

#: Issues shown per image on the console; JSON carries them all.
_CONSOLE_ISSUES = 10


@click.group("policy")
def policy_command() -> None:
    """List, check and inspect a deployment's workspace policies."""


def _shared_options(f: _F) -> _F:
    f = cast(_F, verbose_option(quiet_option(output_option(f))))
    f = click.option(
        "--pin",
        default=None,
        metavar="VERSION",
        help="Resolve the deployment (and credentials) with this kind: version document, as 'deploy run --pin' does.",
    )(f)
    f = click.option(
        "--build-path",
        type=click.Path(file_okay=False, path_type=Path),
        default=None,
        help="Where 'build run' rendered the deployment. Defaults to '<solution root>/build/<deployment>'.",
    )(f)
    f = click.option(
        "--path",
        type=click.Path(file_okay=False, path_type=Path),
        default=None,
        help="Where to start looking for the solution. Defaults to the current directory.",
    )(f)
    return f


def _open(run: CommandRun, path: Path | None, deployment: str, build_path: Path | None) -> tuple[SolutionContext, Path]:
    context = open_solution(resolve_work_path(path)).require_valid()
    solution = context.controller.solution
    target = build_path or build_dir(context.root, deployment)
    run.describe(
        solution=solution.meta.name if solution else "(unnamed)",
        root=context.root,
        deployment=deployment,
        build_path=target,
    )
    return context, target


@policy_command.command("list")
@click.argument("deployment")
@_shared_options
def list_command(
    deployment: str,
    path: Path | None,
    build_path: Path | None,
    pin: str | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """List the policies DEPLOYMENT's workspace configures, with their last recorded result.

    Read-only, no network: the last result comes from policy_results.json under --build-path.

    \b
    Exit codes:
      0  listed (also when nothing is configured)
      2  bad arguments, DEPLOYMENT does not exist, or not inside a solution
      3  the solution itself is invalid
    """
    with command_run("policy list", output=output, quiet=quiet, verbose=verbose) as run:
        context, target = _open(run, path, deployment, build_path)
        infos = list_policies(context, deployment, target, version_pin=pin)
        run.step(f"{len(infos)} configured policy(ies)")
        if output == "console":
            _print_policies(infos)
        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {
                "policies": [
                    {
                        "name": info.name,
                        "phase": info.phase,
                        "enforcement": info.enforcement,
                        "backing": info.backing,
                        "check": info.checkable,
                        "findings": info.findings,
                        "last_result": info.last_result.model_dump(mode="json") if info.last_result else None,
                    }
                    for info in infos
                ]
            }


def _print_policies(infos: list[PolicyInfo]) -> None:
    if not infos:
        click.echo("  (no policies configured)")
        return
    rows = [
        (
            info.name,
            info.phase,
            info.enforcement,
            info.backing,
            "yes" if info.checkable else "no",
            _last_result(info.last_result),
        )
        for info in infos
    ]
    headers = ("POLICY", "PHASE", "ENFORCEMENT", "BACKED BY", "CHECK", "LAST RESULT")
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers) - 1)]
    click.echo("  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=False)) + "  " + headers[-1])
    for row in rows:
        click.echo("  " + "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=False)) + "  " + row[-1])


def _last_result(result: ManifestPolicyResultModel | None) -> str:
    if result is None:
        return "-"
    verdict = "passed" if result.passed else "failed"
    violations = f", {len(result.violations)} violation(s)" if result.violations else ""
    return f"{verdict} ({result.phase}{violations})"


@policy_command.command("check")
@click.argument("deployment")
@click.option(
    "--policy",
    "policies",
    multiple=True,
    metavar="NAME",
    help="Evaluate only this policy (repeatable). Default: every configured policy 'policy check' supports "
    "(release_gate_policy, image_gate_policy).",
)
@click.option(
    "--commit",
    default=None,
    metavar="SHA",
    help="The 40-character commit for release_gate_policy, instead of the policy pin, CI variables or git HEAD.",
)
@_shared_options
def check_command(
    deployment: str,
    policies: tuple[str, ...],
    commit: str | None,
    path: Path | None,
    build_path: Path | None,
    pin: str | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """Evaluate DEPLOYMENT's policies now, exactly as 'deploy run' would.

    Results are appended to policy_results.json when --build-path exists.

    \b
    Exit codes:
      0  every evaluated policy passed (a 'warn' breach included)
      2  nothing to check, or --policy names one that is unknown, not
         configured or not supported yet; bad arguments
      3  a 'deny' breach, missing data under 'on_missing_data: block',
         or a configuration error
    """
    with command_run("policy check", output=output, quiet=quiet, verbose=verbose) as run:
        context, target = _open(run, path, deployment, build_path)
        diagnostics, checked = check_policies(
            context,
            deployment,
            target,
            policies=policies,
            version_pin=pin,
            commit_override=commit,
            on_step=run.step,
        )
        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"checked": checked}
        run.ok = diagnostics.ok


@policy_command.command("findings")
@click.argument("deployment")
@click.option("--policy", "policy_name", required=True, metavar="NAME", help="The policy, e.g. image_gate_policy.")
@click.option(
    "--severity",
    type=click.Choice(["CRITICAL", "HIGH", "MEDIUM", "LOW"], case_sensitive=False),
    default=None,
    help="Lowest severity to list. Defaults to the policy's max_severity, else HIGH.",
)
@click.option(
    "--integration",
    default=None,
    metavar="NAME",
    help="An image_findings Integration document to ask, so the lookup works before the policy is configured.",
)
@_shared_options
def findings_command(
    deployment: str,
    policy_name: str,
    severity: str | None,
    integration: str | None,
    path: Path | None,
    build_path: Path | None,
    pin: str | None,
    output: str,
    quiet: bool,
    verbose: bool,
) -> None:
    """List the open issues behind a policy (image_gate_policy: per SBOM image).

    Read-only. Issues the cve_allowed allowlist would suppress are shown and
    marked, not hidden.

    \b
    Exit codes:
      0  listed
      1  the platform could not be reached or rejected the request
      2  unsupported policy, no integration to ask, no sbom.json; bad arguments
      3  the integration is misconfigured or its credentials do not resolve
    """
    with command_run("policy findings", output=output, quiet=quiet, verbose=verbose) as run:
        context, target = _open(run, path, deployment, build_path)
        report = policy_findings(
            context,
            deployment,
            target,
            policy_name,
            severity=severity.upper() if severity else None,
            integration=integration,
            version_pin=pin,
            on_step=run.step,
        )
        if output == "console":
            _print_findings(report)
        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = _findings_json(policy_name, report)


def _print_findings(report: ImageFindingsReport) -> None:
    click.echo(f"  via {report.integration}, severities {', '.join(report.severities)}")
    if not report.images:
        click.echo("  (no container images in sbom.json)")
    for found in report.images:
        counts = _counts(found.issues, report.severities)
        summary = ", ".join(f"{count} {severity}" for severity, count in counts.items() if count) or "no open issues"
        name = f" as '{found.platform_name}'" if found.platform_name else ""
        detail = f" ({found.detail})" if found.detail else ""
        status = summary if found.status == "found" else found.status
        click.echo(f"  {found.label}{name}: {status}{detail}")
        for issue in found.issues[:_CONSOLE_ISSUES]:
            marker = " [allowlisted]" if issue.id in found.allowlisted else ""
            ident = issue.cve_id or issue.id
            click.echo(f"      {issue.severity:<8} {ident} in {issue.package or '?'}{marker}")
        if len(found.issues) > _CONSOLE_ISSUES:
            click.echo(f"      ... {len(found.issues) - _CONSOLE_ISSUES} more (--output json lists all)")


def _counts(issues: tuple[SecurityIssue, ...], severities: tuple[str, ...]) -> dict[str, int]:
    counts = dict.fromkeys(severities, 0)
    for issue in issues:
        if issue.severity in counts:
            counts[issue.severity] += 1
    return counts


def _findings_json(policy_name: str, report: ImageFindingsReport) -> dict[str, Any]:
    return {
        "policy": policy_name,
        "integration": report.integration,
        "severities": list(report.severities),
        "images": [
            {
                "image": found.label,
                "service": found.image.service,
                "platform_name": found.platform_name,
                "status": found.status,
                "detail": found.detail,
                "counts": _counts(found.issues, report.severities),
                "issues": [
                    {
                        "id": issue.id,
                        "severity": issue.severity,
                        "status": issue.status,
                        "cve_id": issue.cve_id,
                        "package": issue.package,
                        "link": issue.link,
                        "allowlisted": issue.id in found.allowlisted,
                    }
                    for issue in found.issues
                ],
            }
            for found in report.images
        ],
    }
