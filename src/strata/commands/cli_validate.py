"""Click CLI wiring for the validate command group."""

import json
from typing import Optional

import click

from strata.commands.cli_common import (
    click_file,
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)
from strata.commands.validate.graph_validate_command import GraphCommand
from strata.commands.validate.run_validate_command import ValidateCommand
from strata.commands.validate.sbom_ignore_validate_command import SbomIgnoreValidateCommand


class _ValidateGroup(click.Group):
    """Custom group that delegates to 'run' when no subcommand is given.

    This preserves backward compatibility: `strata validate -f foo.yaml`
    continues to work without requiring `strata validate run -f foo.yaml`.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        # If no args or first arg is a known subcommand or --help/-h, use normal group behavior
        if not args or args[0] in self.commands or args[0] in ("--help", "-h"):
            return super().parse_args(ctx, args)
        # Otherwise, prepend 'run' for backward compatibility
        args = ["run"] + args
        return super().parse_args(ctx, args)


@click.group(name="validate", cls=_ValidateGroup)
def validate_group() -> None:
    """Validate platform YAML files and visualize workspace dependencies."""


@validate_group.command(
    name="run",
    epilog=(
        "Exit codes:\n"
        "  0  success\n"
        "  1  system error (infrastructure unavailable, timeout, permissions) — alert\n"
        "  2  usage error (bad arguments, file not found) — fix script\n"
        "  3  validation error (schema, cross-ref) — fix config\n\n"
        "Note: exit code 4 (lock conflict) is only returned by 'deploy run' and 'deploy destroy'."
    ),
)
@click_file
@click.option(
    "--pattern",
    "-p",
    default=None,
    metavar="PATTERN",
    help=(
        "Glob pattern to select multiple deployment manifests for cross-manifest overlap validation. "
        "Resolved relative to the workspace root against the active profile's configfile_paths. "
        "Requires an initialized workspace with an active profile. "
        "Example: 'deployments/**' or 'deployments/acme-*'"
    ),
)
@click.option(
    "--deep",
    is_flag=True,
    default=False,
    help=(
        "Enable Phase 2 (cross-reference) validation against the active profile's "
        "configuration sources. Requires an initialized workspace with an active profile. "
        "Fails with exit code 1 if the configuration cannot be loaded."
    ),
)
@click.option(
    "--verify-digests",
    is_flag=True,
    default=False,
    help=(
        "Check that resolved_sha values on version-lock pins use a recognised immutable-reference "
        "format (git SHA or OCI digest). Implies --deep. "
        "Mismatched format emits a warning; missing resolved_sha on a ring with require_digests: true "
        "is an error (exit 3)."
    ),
)
@click.option(
    "--explain",
    is_flag=True,
    default=False,
    help="After validation, emit a plain-English summary of what the file describes.",
)
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Run AI review of validation errors and policy violations (requires an ai_agent integration).",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def validate_run(
    file: Optional[str] = None,
    pattern: Optional[str] = None,
    deep: bool = False,
    verify_digests: bool = False,
    explain: bool = False,
    ai: bool = False,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Validate a platform YAML file against its kind-specific schema.

    Single-file validation (requires -f / --file):

        strata validate run -f config/deployment.yaml

    Cross-manifest overlap validation (requires --pattern glob):

        strata validate run --pattern "deployments/**"
        strata validate run --pattern "deployments/acme-*"
    """
    if not file and not pattern:
        raise click.UsageError("Specify a single file with '-f' / '--file', or a glob with '--pattern' / '-p'.")
    command = ValidateCommand(
        file=file,
        path=pattern,
        deep=deep or verify_digests,
        verify_digests=verify_digests,
        explain=explain,
        ai=ai,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@validate_group.command(name="graph")
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["files", "resources"]),
    default="files",
    help="Graph type: 'files' shows YAML file dependency tree (default), 'resources' shows logical infrastructure topology.",
)
@click.option(
    "--entry",
    "-e",
    default=None,
    metavar="PATH",
    help="Entry point file (deployment or workspace YAML). If omitted, discovers all deployments in the workspace.",
)
@click.option(
    "--save",
    "-s",
    default=None,
    metavar="PATH",
    is_flag=False,
    flag_value="graph.md",
    help="Write Mermaid markdown to file (default: graph.md). Written in addition to console output.",
)
@click.option(
    "--direction",
    type=click.Choice(["LR", "TD", "BT", "RL"]),
    default=None,
    help="Mermaid graph direction. Default: LR for files, TD for resources.",
)
@click.option(
    "--no-validate",
    is_flag=True,
    default=False,
    help="Skip validation (all nodes shown as neutral). Faster for large workspaces.",
)
@click_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def validate_graph(
    mode: str = "files",
    entry: Optional[str] = None,
    save: Optional[str] = None,
    direction: Optional[str] = None,
    no_validate: bool = False,
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Build and render a workspace dependency graph.

    File mode (default): shows YAML file dependency tree.

        strata validate graph
        strata validate graph --entry deploy/deploy-prd.yaml

    Resource mode: shows logical infrastructure topology.

        strata validate graph --mode resources
        strata validate graph --mode resources --entry stack/ws-platform.yaml

    Save as Mermaid markdown:

        strata validate graph --save graph.md
    """
    # --file/-f is an alias for --entry
    resolved_entry = entry or file
    command = GraphCommand(
        mode=mode,
        entry=resolved_entry,
        save=save,
        direction=direction,
        no_validate=no_validate,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@validate_group.command(name="sbom-ignore")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
@click.pass_context
def validate_sbom_ignore(
    ctx: click.Context,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Validate .strata/sbom-ignore.yaml and detect orphaned rules.

    Checks the schema of the ignore file and runs a dependency scan to find
    rules that no longer match any file or package in the workspace.

        strata validate sbom-ignore
        strata validate sbom-ignore --output json
    """
    command = SbomIgnoreValidateCommand(
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()

    if output == "json":
        envelope = {
            "success": success,
            "command": "validate sbom-ignore",
            "data": command.get_result(),
        }
        click.echo(json.dumps(envelope, indent=2))

    handle_command_exit(command, success)


# Backward compatibility: expose as `validate_command` so cli.py and tests keep working.
validate_command = validate_group
