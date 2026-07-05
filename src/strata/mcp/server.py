"""Strata MCP server — exposes workspace operations as Model Context Protocol tools.

Tools are thin wrappers around existing strata command classes.
The server is launched via ``strata mcp serve`` and communicates over stdio.

Install the optional dependency first:
    pip install xyz-strata[mcp]

The server resolves the workspace root from the process CWD (set to
``${workspaceFolder}`` by the MCP host) or an explicit ``work_path`` arg.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Lazy import guard — raise a clear error when `mcp` is not installed
# ---------------------------------------------------------------------------
try:
    from mcp.server.fastmcp import FastMCP
except ImportError as _exc:
    raise ImportError(
        "The `mcp` package is required to run the strata MCP server.\nInstall it with:  pip install xyz-strata[mcp]"
    ) from _exc


mcp = FastMCP(
    "strata",
    instructions=(
        "You are connected to a strata infrastructure workspace. "
        "Call `workspace_status` first to understand the workspace state "
        "before taking any other action. "
        "Always validate files before building. Always use build_plan before build_run. "
        "Always use deploy_plan before suggesting a deploy — deploy operations must be "
        "confirmed by the user and run via the CLI, not via MCP tools."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _work_path(work_path: Optional[str]) -> str:
    """Resolve workspace root: explicit arg → CWD."""
    return work_path if work_path else str(Path.cwd())


def _run_command(cmd: Any) -> Dict[str, Any]:
    """Execute a BaseCommand and return a structured envelope.

    Always returns ``{success, data, errors, messages}`` so MCP callers can
    inspect ``success`` and read ``errors`` without extra logic.
    """
    cmd.execute()
    return {
        "success": not cmd.has_errors(),  # type: ignore[attr-defined]
        "data": cmd._output_data,  # type: ignore[attr-defined]
        "errors": cmd.get_errors(),  # type: ignore[attr-defined]
        "messages": cmd.get_messages(),  # type: ignore[attr-defined]
    }


# ---------------------------------------------------------------------------
# Tool: workspace_status
# ---------------------------------------------------------------------------


@mcp.tool()
def workspace_status(work_path: Optional[str] = None) -> Dict[str, Any]:
    """Return full workspace state: readiness phases, profiles, repos, integrations, and health.

    Always call this first. The `readiness.next_step.hint` field tells you exactly
    what command the user should run next.
    """
    from strata.commands.status.show_status_command import StatusCommand

    cmd = StatusCommand(work_path=_work_path(work_path), output="json", quiet=True)
    return _run_command(cmd)


# ---------------------------------------------------------------------------
# Tool: validate_file
# ---------------------------------------------------------------------------


@mcp.tool()
def validate_file(
    file_path: str,
    work_path: Optional[str] = None,
    deep: bool = False,
) -> Dict[str, Any]:
    """Validate a strata YAML file against its kind-specific schema.

    Args:
        file_path: Path to the YAML file to validate (absolute or relative to work_path).
        work_path: Workspace root. Defaults to CWD.
        deep: Enable Phase 2 cross-reference validation (requires active profile).

    Returns a dict with `valid`, `kind`, `name`, and `errors` (list of field-level errors).
    """
    from strata.commands.validate.run_validate_command import ValidateCommand

    cmd = ValidateCommand(
        file=file_path,
        work_path=_work_path(work_path),
        deep=deep,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)


# ---------------------------------------------------------------------------
# Tool: list_schemas
# ---------------------------------------------------------------------------


@mcp.tool()
def list_schemas() -> Dict[str, Any]:
    """List all supported strata document kinds."""
    from strata.models.common_models import PlatformKind

    kinds = sorted(k.value for k in PlatformKind)
    return {"kinds": kinds}


# ---------------------------------------------------------------------------
# Tool: get_schema
# ---------------------------------------------------------------------------


@mcp.tool()
def get_schema(kind: str) -> Dict[str, Any]:
    """Return the full JSON Schema for a strata document kind.

    Args:
        kind: Document kind (e.g. deployment, configuration, environment, module).

    Returns the JSON Schema dict which describes every allowed field and type.
    """
    from strata.commands.cli_schema import _KIND_TO_MODEL
    from strata.models.common_models import PlatformKind

    try:
        platform_kind = PlatformKind(kind.lower())
    except ValueError:
        valid = ", ".join(sorted(k.value for k in PlatformKind))
        return {"error": f"Unknown kind '{kind}'. Valid kinds: {valid}"}

    model_cls = _KIND_TO_MODEL.get(platform_kind)
    if model_cls is None:
        return {"error": f"No schema available for kind '{kind}'."}

    return model_cls.model_json_schema()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tool: scaffold_file
# ---------------------------------------------------------------------------


@mcp.tool()
def scaffold_file(
    kind: str,
    name: str,
    extra_vars: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Generate a strata YAML file from its template and return the content as a string.

    Does NOT write to disk — the caller (AI) should show the content to the user
    and write it only after confirmation.

    Args:
        kind: Document kind (e.g. namespace, module, deployment).
        name: The `meta.name` value and base filename.
        extra_vars: Optional additional template variables (e.g. {"owner": "platform"}).

    Returns `{"kind": ..., "name": ..., "content": "<yaml string>", "suggested_path": ...}`.
    """
    from strata.utils.system import get_pkg_templates_path
    from strata.utils.templater import TemplateProcessor

    tpl_dir = get_pkg_templates_path() / "solution" / "dot.strata" / "templates"
    tpl_file = tpl_dir / f"{kind}.yaml"

    if not tpl_file.exists():
        available = sorted(f.stem for f in tpl_dir.glob("*.yaml"))
        return {"error": f"No template for kind '{kind}'. Available: {', '.join(available)}"}

    variables: Dict[str, str] = {"name": name, "owner": "platform", "version": "1.0.0"}
    if extra_vars:
        variables.update(extra_vars)

    raw = tpl_file.read_text(encoding="utf-8")
    content = TemplateProcessor.render(raw, variables)

    return {
        "kind": kind,
        "name": name,
        "content": content,
        "suggested_path": f"{kind}s/{name}.yaml",
    }


# ---------------------------------------------------------------------------
# Tool: build_plan
# ---------------------------------------------------------------------------


@mcp.tool()
def build_plan(
    file: str,
    work_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Show what a build would produce without writing any artifacts (dry-run).

    Args:
        file: Path to the deployment YAML file.
        work_path: Workspace root. Defaults to CWD.
    """
    from strata.commands.builders.run_build_command import RunBuildCommand

    cmd = RunBuildCommand(
        file=file,
        work_path=_work_path(work_path),
        dry_run=True,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)


# ---------------------------------------------------------------------------
# Tool: build_run
# ---------------------------------------------------------------------------


@mcp.tool()
def build_run(
    file: str,
    work_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full build pipeline and generate platform artifacts.

    Args:
        file: Path to the deployment YAML file.
        work_path: Workspace root. Defaults to CWD.
    """
    from strata.commands.builders.run_build_command import RunBuildCommand

    cmd = RunBuildCommand(
        file=file,
        work_path=_work_path(work_path),
        dry_run=False,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)


# ---------------------------------------------------------------------------
# Tool: deploy_plan
# ---------------------------------------------------------------------------


@mcp.tool()
def deploy_plan(
    file: str,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Preview what a deployment would do without applying any changes (dry-run).

    Use this to show the user what will happen before they confirm a deploy.
    Deployments must always be run via the CLI after explicit user confirmation —
    never call deploy via MCP without the user reviewing the plan output first.

    Args:
        file: Path to the deployment YAML file.
        work_path: Workspace root. Defaults to CWD.
        stage: Limit plan to a specific deployment stage name.
    """
    from strata.commands.deploy.run_deploy_command import RunDeployCommand

    cmd = RunDeployCommand(
        file=file,
        work_path=_work_path(work_path),
        stage=stage,
        dry_run=True,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)


# ---------------------------------------------------------------------------
# Tool: audit_query
# ---------------------------------------------------------------------------


@mcp.tool()
def audit_query(
    work_path: Optional[str] = None,
    last: int = 20,
    since: Optional[str] = None,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Query deploy-log entries from workspace audit logs.

    Args:
        work_path: Workspace root. Defaults to CWD.
        last: Maximum number of entries to return (default 20).
        since: ISO timestamp — return only entries after this time (e.g. "2026-07-01T00:00:00").
        stage: Filter to entries that executed a specific stage name.

    Returns ``{"success": true, "entries": [...], "count": N}``.
    Each entry has: timestamp, deployment, success, duration_seconds, stages[].
    """
    from strata.controllers.audit_controller import AuditController
    from strata.utils.config import SOLUTION_DEPLOY_LOG_DIR, SOLUTION_DIR

    wp = Path(_work_path(work_path))
    base_path = wp / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR
    controller = AuditController(work_path=wp)
    entries = controller.query_deploy_logs(
        base_path=base_path,
        since=since,
        stage=stage,
        last=last,
    )
    entries_data = [e.model_dump(exclude_none=True) for e in entries]
    return {"success": True, "entries": entries_data, "count": len(entries_data)}


# ---------------------------------------------------------------------------
# Tool: deploy_history
# ---------------------------------------------------------------------------


@mcp.tool()
def deploy_history(
    work_path: Optional[str] = None,
    lines: int = 20,
    operation: Optional[str] = None,
) -> Dict[str, Any]:
    """Return recent deployment execution history from workspace logs.

    Does not require a deployment YAML file — reads ``.strata/logs/`` only.

    Args:
        work_path: Workspace root. Defaults to CWD.
        lines: Maximum history entries to return (default 20).
        operation: Filter by operation type — ``"run"`` or ``"destroy"``.
    """
    from strata.commands.deploy.history_deploy_command import HistoryDeployCommand

    cmd = HistoryDeployCommand(
        work_path=_work_path(work_path),
        lines=lines,
        operation=operation,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)


# ---------------------------------------------------------------------------
# Tool: deploy_status
# ---------------------------------------------------------------------------


@mcp.tool()
def deploy_status(
    file: str,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Return live infrastructure outputs (Terraform) for a deployment.

    Runs ``terraform output -json`` for each provisioned stage and returns the
    current infrastructure state. Use this to inspect what is deployed, not to
    check execution history (use ``deploy_history`` for that).

    Args:
        file: Path to the deployment YAML file.
        work_path: Workspace root. Defaults to CWD.
        stage: Limit output to a specific stage name.
    """
    from strata.commands.deploy.status_deploy_command import StatusDeployCommand

    cmd = StatusDeployCommand(
        file=file,
        work_path=_work_path(work_path),
        stage=stage,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)


# ---------------------------------------------------------------------------
# Tool: deploy_health
# ---------------------------------------------------------------------------


@mcp.tool()
def deploy_health(
    file: str,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
) -> Dict[str, Any]:
    """Run health checks against provisioned deployment stages.

    For each stage that has ``health_checks`` defined, resolves the target
    (URL / host:port) from Terraform outputs and executes HTTP GET or TCP
    connect checks. Returns pass/fail per check and an overall ``success`` flag.

    Args:
        file: Path to the deployment YAML file.
        work_path: Workspace root. Defaults to CWD.
        stage: Limit checks to a specific stage name.
    """
    from strata.commands.deploy.health_deploy_command import HealthDeployCommand

    cmd = HealthDeployCommand(
        file=file,
        work_path=_work_path(work_path),
        stage=stage,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)


# ---------------------------------------------------------------------------
# Tool: build_sbom
# ---------------------------------------------------------------------------


@mcp.tool()
def build_sbom(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    scan: Optional[str] = None,
    report: str = "inventory",
) -> Dict[str, Any]:
    """Generate an SBOM or dependency inventory for a deployment.

    Standard mode (``file`` provided): loads the platform artifact from a
    previous ``build_run``, runs all SBOM collectors, and returns the result.

    Scan mode (``scan`` provided): runs file-based collectors against the given
    directory without requiring a workspace or deployment file.

    Args:
        file: Path to the deployment YAML file (standard mode).
        work_path: Workspace root. Defaults to CWD.
        scan: Directory to scan directly (scan mode; no deployment file needed).
        report: ``"cyclonedx"`` for a machine-readable SBOM, or ``"inventory"``
                for a human-readable component list (default).
    """
    from strata.commands.builders.sbom_build_command import SbomBuildCommand

    cmd = SbomBuildCommand(
        file=file,
        work_path=_work_path(work_path),
        scan_path=scan,
        report=report,
        output="json",
        quiet=True,
    )
    return _run_command(cmd)


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------


@mcp.resource("strata://schema/{kind}")
def schema_resource(kind: str) -> str:
    """JSON Schema for a strata document kind — loaded automatically into AI context."""
    import json

    result = get_schema(kind)
    return json.dumps(result, indent=2)


@mcp.resource("strata://workspace")
def workspace_resource() -> str:
    """Current workspace state — loaded automatically into AI context."""
    import json

    result = workspace_status()
    return json.dumps(result, indent=2)
