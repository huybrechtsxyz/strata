"""CLI command group for deployment manifest queries."""

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_work_path,
)


@click.group(name="manifest", help="Query and export deployment manifests.")
def manifest_group():
    """Deployment manifest query commands."""


@manifest_group.command(name="list", help="List deployment manifests.")
@click.option(
    "--deployment",
    default=None,
    type=str,
    help="Filter by deployment name.",
)
@click.option(
    "--last",
    default=None,
    type=int,
    help="Show only the last N manifests.",
)
@click_work_path
@click_output_format
@click_output_quiet
@click.pass_context
def manifest_list(
    ctx: click.Context,
    deployment: Optional[str],
    last: Optional[int],
    work_path: str,
    output: str,
    quiet: bool,
) -> None:
    """List deployment manifests from the configured manifest store."""
    from strata.services.deployment_manifest_service import DeploymentManifestService
    from strata.utils.config import SOLUTION_DEPLOYMENTS_DIR, SOLUTION_DIR

    wp = Path(work_path)
    base_path = wp / SOLUTION_DIR / SOLUTION_DEPLOYMENTS_DIR

    if not base_path.exists():
        if output == "json":
            click.echo(json.dumps({"success": True, "data": {"manifests": []}}))
        elif not quiet:
            click.echo("No manifests found.")
        return

    # Collect manifest files
    manifests = DeploymentManifestService.list_manifests(base_path)
    if deployment:
        manifests = [m for m in manifests if m.stem.startswith(deployment + "_")]
    if last:
        manifests = manifests[:last]

    if output == "json":
        entries = []
        for m in manifests:
            try:
                data = json.loads(m.read_text(encoding="utf-8"))
                entries.append(
                    {
                        "path": str(m.relative_to(wp)),
                        "deployment": data.get("spec", {}).get("deployment_name", ""),
                        "action": data.get("spec", {}).get("action", ""),
                        "status": data.get("spec", {}).get("status", ""),
                        "started_at": data.get("spec", {}).get("started_at", ""),
                        "deployed_by": data.get("spec", {}).get("deployed_by", ""),
                    }
                )
            except (json.JSONDecodeError, OSError):
                entries.append({"path": str(m.relative_to(wp)), "error": "unreadable"})
        click.echo(json.dumps({"success": True, "data": {"manifests": entries}}, indent=2))
    else:
        if not manifests:
            if not quiet:
                click.echo("No manifests found.")
            return
        for m in manifests:
            try:
                data = json.loads(m.read_text(encoding="utf-8"))
                spec = data.get("spec", {})
                action = spec.get("action", "?")
                status = spec.get("status", "?")
                started = spec.get("started_at", "?")
                by = spec.get("deployed_by", "?")
                click.echo(f"  {m.stem}  {action}/{status}  {started}  by {by}")
            except (json.JSONDecodeError, OSError):
                click.echo(f"  {m.stem}  (unreadable)")


@manifest_group.command(name="show", help="Show a specific deployment manifest.")
@click.argument("manifest_path", type=click.Path(exists=True))
@click_output_format
@click_output_quiet
@click.pass_context
def manifest_show(
    ctx: click.Context,
    manifest_path: str,
    output: str,
    quiet: bool,
) -> None:
    """Display the full content of a deployment manifest file."""
    path = Path(manifest_path)
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except (json.JSONDecodeError, OSError) as exc:
        click.echo(f"Error reading manifest: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc

    if output == "json":
        click.echo(json.dumps({"success": True, "data": data}, indent=2))
    else:
        spec = data.get("spec", {})
        meta = data.get("meta", {})
        click.echo(f"Manifest: {meta.get('name', '?')}")
        click.echo(f"  Action:      {spec.get('action', '?')}")
        click.echo(f"  Status:      {spec.get('status', '?')}")
        click.echo(f"  Deployment:  {spec.get('deployment_name', '?')}")
        click.echo(f"  Workspace:   {spec.get('workspace_name', '?')}")
        click.echo(f"  Environment: {spec.get('environment', '—')}")
        click.echo(f"  Started:     {spec.get('started_at', '?')}")
        click.echo(f"  Completed:   {spec.get('completed_at', '—')}")
        click.echo(f"  Duration:    {spec.get('duration_seconds', '—')}s")
        click.echo(f"  By:          {spec.get('deployed_by', '?')}")

        artifacts = spec.get("artifacts", {})
        platform = artifacts.get("platform", {})
        if platform:
            click.echo(f"  Platform:    {platform.get('hash', '?')}")

        repos = artifacts.get("repositories", {})
        if repos:
            click.echo("  Repositories:")
            for name, info in repos.items():
                commit = info.get("commit", "?")[:12] if info.get("commit") else "?"
                click.echo(f"    {name}: {commit} ({info.get('ref', '—')})")

        stages = spec.get("stages", [])
        if stages:
            click.echo("  Stages:")
            for s in stages:
                click.echo(f"    {s.get('name', '?')}: {s.get('status', '?')} ({s.get('duration_seconds', '?')}s)")

        sbom = spec.get("sbom")
        if sbom:
            click.echo(f"  SBOM:        {sbom.get('path', '?')} ({sbom.get('component_count', '?')} components)")

        policy_results = spec.get("policy_results", [])
        if policy_results:
            click.echo("  Policies:")
            for pr in policy_results:
                icon = "✓" if pr.get("passed") else "✗"
                click.echo(f"    {icon} {pr.get('policy_name', '?')} [{pr.get('enforcement', '?')}]")

        lock = spec.get("lock")
        if lock:
            click.echo(f"  Lock:        {lock.get('backend', '?')} (id={lock.get('lock_id', '?')[:12]})")

        signatures = spec.get("signatures")
        if signatures:
            click.echo(f"  Signed:      {signatures.get('method', 'yes')}")


@manifest_group.command(name="export", help="Export manifests as a compliance evidence package.")
@click.option(
    "--deployment",
    default=None,
    type=str,
    help="Filter by deployment name.",
)
@click.option(
    "--last",
    default=None,
    type=int,
    help="Export only the last N manifests.",
)
@click.option(
    "--include-sbom",
    is_flag=True,
    default=False,
    help="Include referenced SBOM files in the export.",
)
@click.option(
    "--include-platform",
    is_flag=True,
    default=False,
    help="Include platform.json artifacts in the export.",
)
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(),
    help="Output directory for the evidence package.",
)
@click_work_path
@click_output_format
@click_output_quiet
@click.pass_context
def manifest_export(
    ctx: click.Context,
    deployment: Optional[str],
    last: Optional[int],
    include_sbom: bool,
    include_platform: bool,
    out_dir: str,
    work_path: str,
    output: str,
    quiet: bool,
) -> None:
    """Export deployment manifests as a compliance evidence package.

    Copies manifest files (and optionally SBOM and platform artifacts) to
    a directory suitable for audit submission.
    """
    import shutil

    from strata.services.deployment_manifest_service import DeploymentManifestService
    from strata.utils.config import SOLUTION_DEPLOYMENTS_DIR, SOLUTION_DIR

    wp = Path(work_path)
    base_path = wp / SOLUTION_DIR / SOLUTION_DEPLOYMENTS_DIR
    out = Path(out_dir)

    manifests = DeploymentManifestService.list_manifests(base_path) if base_path.exists() else []
    if deployment:
        manifests = [m for m in manifests if m.stem.startswith(deployment + "_")]
    if last:
        manifests = manifests[:last]

    out.mkdir(parents=True, exist_ok=True)
    manifests_dir = out / "manifests"
    manifests_dir.mkdir(exist_ok=True)

    exported: list[str] = []
    for m in manifests:
        dest = manifests_dir / m.name
        shutil.copy2(m, dest)
        exported.append(str(dest.relative_to(out)))

        # Optionally include referenced artifacts
        try:
            data = json.loads(m.read_text(encoding="utf-8"))
            spec = data.get("spec", {})

            if include_sbom:
                sbom = spec.get("sbom", {})
                sbom_path = sbom.get("path")
                if sbom_path:
                    src = wp / sbom_path
                    if src.exists():
                        sbom_dest = out / "sbom" / Path(sbom_path).name
                        sbom_dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, sbom_dest)
                        exported.append(str(sbom_dest.relative_to(out)))

            if include_platform:
                platform = spec.get("artifacts", {}).get("platform", {})
                platform_path = platform.get("path")
                if platform_path:
                    src = wp / platform_path
                    if src.exists():
                        plat_dest = out / "platform" / Path(platform_path).name
                        plat_dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, plat_dest)
                        exported.append(str(plat_dest.relative_to(out)))
        except (json.JSONDecodeError, OSError):
            pass

    if output == "json":
        click.echo(
            json.dumps(
                {
                    "success": True,
                    "data": {
                        "output_dir": str(out),
                        "manifest_count": len(manifests),
                        "files": exported,
                    },
                },
                indent=2,
            )
        )
    elif not quiet:
        click.echo(f"Exported {len(manifests)} manifest(s) to {out}")
        for f in exported:
            click.echo(f"  {f}")
