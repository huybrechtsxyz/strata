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
from strata.controllers.checkov_controller import CheckovFindingsReport
from strata.controllers.policy_controller import PolicyInfo, check_policies, list_policies, policy_findings
from strata.controllers.sbom_controller import CveFindingsReport
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
    "(cve_policy, checkov_policy, release_gate_policy, image_gate_policy).",
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
    """Evaluate DEPLOYMENT's policies now, exactly as 'build run' / 'deploy run' would.

    cve_policy scans --build-path's sbom.json, checkov_policy its rendered
    sources. Results are appended to policy_results.json when --build-path exists.

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
@click.option(
    "--policy",
    "policy_name",
    required=True,
    metavar="NAME",
    help="The policy: image_gate_policy, cve_policy or checkov_policy.",
)
@click.option(
    "--severity",
    type=click.Choice(["CRITICAL", "HIGH", "MEDIUM", "LOW"], case_sensitive=False),
    default=None,
    help="Lowest severity to list. Defaults to the policy's own threshold, else HIGH.",
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
    """List the issues behind a policy: image_gate_policy per SBOM image, cve_policy per
    vulnerability, checkov_policy per failed check and target.

    Read-only. Issues the cve_allowed allowlist would suppress are shown and
    marked, not hidden; so are Checkov findings without a severity, which the
    gate never counts.

    \b
    Exit codes:
      0  listed
      1  the platform could not be reached or rejected the request; no
         CVE scanner or checkov, or a scan failed
      2  unsupported policy, no integration to ask, no sbom.json or build
         output; bad arguments
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
            if isinstance(report, CveFindingsReport):
                _print_cve_findings(report)
            elif isinstance(report, CheckovFindingsReport):
                _print_checkov_findings(report)
            else:
                _print_image_findings(report)
        run.report(Diagnostics(), root=context.root)
        if isinstance(run.reporter, JsonReporter):
            if isinstance(report, CveFindingsReport):
                run.reporter.data = _cve_findings_json(policy_name, report)
            elif isinstance(report, CheckovFindingsReport):
                run.reporter.data = _checkov_findings_json(policy_name, report)
            else:
                run.reporter.data = _image_findings_json(policy_name, report)


def _print_checkov_findings(report: CheckovFindingsReport) -> None:
    version = f"checkov {report.scanner_version}" if report.scanner_version else "checkov (nothing scanned)"
    click.echo(f"  {report.framework}, scanned with {version}, severities {', '.join(report.severities)}")
    if report.nothing is not None:
        click.echo(f"  nothing in scope: {report.nothing}")
    for target in report.targets:
        if target.missing is not None:
            click.echo(f"  {target.label}: not scanned ({target.missing})")
            continue
        unrated = sum(item.unrated for item in target.findings)
        rated = len(target.findings) - unrated
        click.echo(f"  {target.label}: {rated} finding(s), {unrated} unrated")
        for item in target.findings[:_CONSOLE_ISSUES]:
            finding = item.finding
            lines = finding.file_line_range or []
            where = f"{finding.file_path}:{lines[0]}" if lines else finding.file_path
            marker = " [unrated]" if item.unrated else ""
            click.echo(f"      {finding.severity:<8} {finding.check_id} {finding.resource} at {where}{marker}")
        if len(target.findings) > _CONSOLE_ISSUES:
            click.echo(f"      ... {len(target.findings) - _CONSOLE_ISSUES} more (--output json lists all)")


def _checkov_findings_json(policy_name: str, report: CheckovFindingsReport) -> dict[str, Any]:
    return {
        "policy": policy_name,
        "scanner_version": report.scanner_version,
        "framework": report.framework,
        "severities": list(report.severities),
        "nothing": report.nothing,
        "targets": [
            {
                "target": target.label,
                "path": str(target.path) if target.path is not None else None,
                "missing": target.missing,
                "findings": [
                    {**item.finding.model_dump(mode="json"), "unrated": item.unrated} for item in target.findings
                ],
            }
            for target in report.targets
        ],
    }


def _print_cve_findings(report: CveFindingsReport) -> None:
    counts = _cve_counts(report)
    summary = ", ".join(f"{count} {severity}" for severity, count in counts.items() if count) or "no findings"
    click.echo(f"  scanned with {report.scanner} {report.scanner_version}, severities {', '.join(report.severities)}")
    click.echo(f"  {summary}")
    for item in report.findings[:_CONSOLE_ISSUES]:
        finding = item.finding
        marker = " [allowlisted]" if item.allowlisted else ""
        fixed = f" (fixed in {finding.fixed_version})" if finding.fixed_version else ""
        click.echo(
            f"      {finding.severity:<8} {finding.vulnerability_id} in {finding.package_name} "
            f"{finding.installed_version}{fixed}{marker}"
        )
    if len(report.findings) > _CONSOLE_ISSUES:
        click.echo(f"      ... {len(report.findings) - _CONSOLE_ISSUES} more (--output json lists all)")


def _cve_counts(report: CveFindingsReport) -> dict[str, int]:
    counts = dict.fromkeys(report.severities, 0)
    for item in report.findings:
        if item.finding.severity in counts:
            counts[item.finding.severity] += 1
    return counts


def _cve_findings_json(policy_name: str, report: CveFindingsReport) -> dict[str, Any]:
    return {
        "policy": policy_name,
        "scanner": report.scanner,
        "scanner_version": report.scanner_version,
        "severities": list(report.severities),
        "counts": _cve_counts(report),
        "findings": [
            {**item.finding.model_dump(mode="json"), "allowlisted": item.allowlisted} for item in report.findings
        ],
    }


def _print_image_findings(report: ImageFindingsReport) -> None:
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


def _image_findings_json(policy_name: str, report: ImageFindingsReport) -> dict[str, Any]:
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
