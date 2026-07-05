"""Click CLI wiring for the build command group."""

from typing import Optional

import click

from strata.commands.builders.clean_build_command import CleanBuildCommand
from strata.commands.builders.plan_build_command import PlanBuildCommand
from strata.commands.builders.run_build_command import RunBuildCommand
from strata.commands.builders.sbom_build_command import SbomBuildCommand
from strata.commands.cli_common import (
    click_file,
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)


@click.group(name="build", help="Build platform and Terraform artifacts.")
def build():
    """Build command group."""
    pass


@build.command(name="run", help="Run platform + terraform build pipeline.")
@click_file
@click_work_path
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate and plan the build without writing any output files.",
)
@click.option(
    "--audit",
    is_flag=True,
    default=False,
    help="Run CVE vulnerability scan after generating the SBOM (requires trivy or grype).",
)
@click.option(
    "--severity",
    "audit_severity",
    type=click.Choice(["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"], case_sensitive=False),
    default="MEDIUM",
    show_default=True,
    help="Minimum severity to report in CVE audit.",
)
@click.option(
    "--fail-on",
    "fail_on",
    type=click.Choice(["CRITICAL", "HIGH", "MEDIUM", "LOW"], case_sensitive=False),
    default=None,
    help="Exit non-zero (code 3) if findings at this severity or above exist.",
)
@click.option(
    "--audit-report",
    "audit_report",
    default=None,
    metavar="FORMATS",
    help="Write audit report files. Comma-separated: vex, sarif (e.g. --audit-report vex,sarif).",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def build_run(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    dry_run: bool = False,
    audit: bool = False,
    audit_severity: str = "MEDIUM",
    fail_on: Optional[str] = None,
    audit_report: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Execute the build pipeline."""
    command = RunBuildCommand(
        file=file,
        work_path=work_path,
        dry_run=dry_run,
        audit=audit,
        audit_severity=audit_severity.upper(),
        fail_on=fail_on.upper() if fail_on else None,
        audit_report=audit_report,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@build.command(name="clean", help="Clean deployment build artifacts.")
@click_file
@click_work_path
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show which path would be cleaned without deleting files.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def build_clean(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    dry_run: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Clean build artifacts for the selected deployment."""
    command = CleanBuildCommand(
        file=file,
        work_path=work_path,
        dry_run=dry_run,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@build.command(name="plan", help="Show artifact diff + terraform plan without writing to the real build path.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit terraform plan to a specific deployment stage.",
)
@click.option(
    "--artifacts-only",
    "artifacts_only",
    is_flag=True,
    default=False,
    help="Show only the artifact diff — skip terraform plan.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def build_plan(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    artifacts_only: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show what build run would write, then run terraform plan per stage.

    Builds into a temporary directory, diffs the result against the existing
    build artifacts, then runs ``terraform init → validate → plan`` for each
    stage.  Nothing is written to the real build path.
    """
    command = PlanBuildCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        artifacts_only=artifacts_only,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@build.command(name="sbom", help="(Re)generate SBOM from an existing platform.json or scan a directory.")
@click_file
@click_work_path
@click.option(
    "--scan",
    "scan_path",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    default=None,
    help="Scan a directory for SBOM components without a deployment file. Mutually exclusive with -f.",
)
@click.option(
    "--report",
    type=click.Choice(["cyclonedx", "inventory"], case_sensitive=False),
    default="cyclonedx",
    show_default=True,
    help="Output mode: cyclonedx writes sbom.json; inventory prints a human-readable component listing.",
)
@click.option(
    "--output-file",
    "output_file",
    default=None,
    metavar="PATH",
    help=(
        "Write output to PATH instead of the default location. "
        "For cyclonedx: overrides the default sbom.json path. "
        "For inventory: writes to PATH instead of stdout."
    ),
)
@click.option(
    "--no-deps",
    "no_deps",
    is_flag=True,
    default=False,
    help="Skip application dependency scanning (DependencyFileCollector). Useful for large repos where lockfile scanning is slow.",
)
@click.option(
    "--audit",
    is_flag=True,
    default=False,
    help="Run CVE vulnerability scan after generating the SBOM (requires trivy or grype).",
)
@click.option(
    "--severity",
    "audit_severity",
    type=click.Choice(["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"], case_sensitive=False),
    default="MEDIUM",
    show_default=True,
    help="Minimum severity to report in CVE audit.",
)
@click.option(
    "--fail-on",
    "fail_on",
    type=click.Choice(["CRITICAL", "HIGH", "MEDIUM", "LOW"], case_sensitive=False),
    default=None,
    help="Exit non-zero (code 3) if findings at this severity or above exist.",
)
@click.option(
    "--audit-report",
    "audit_report",
    default=None,
    metavar="FORMATS",
    help="Write audit report files. Comma-separated: vex, sarif (e.g. --audit-report vex,sarif).",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def build_sbom(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    scan_path: Optional[str] = None,
    report: str = "cyclonedx",
    output_file: Optional[str] = None,
    no_deps: bool = False,
    audit: bool = False,
    audit_severity: str = "MEDIUM",
    fail_on: Optional[str] = None,
    audit_report: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """(Re)generate the SBOM from an existing platform.json, or scan a directory directly."""
    if scan_path and file:
        raise click.UsageError("--scan and -f/--file are mutually exclusive.")

    command = SbomBuildCommand(
        file=file,
        work_path=work_path,
        scan_path=scan_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
        report=report,
        output_file=output_file,
        no_deps=no_deps,
        audit=audit,
        audit_severity=audit_severity.upper(),
        fail_on=fail_on.upper() if fail_on else None,
        audit_report=audit_report,
    )
    success = command.execute()
    handle_command_exit(command, success)
