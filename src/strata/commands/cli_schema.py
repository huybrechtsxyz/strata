"""Click CLI wiring for the ``schema`` command group."""

import json
from pathlib import Path
from typing import Optional

import click

from strata.commands.cli_common import click_output_format
from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_manifest_model import DeploymentManifestModel
from strata.models.deployment_model import DeploymentModel
from strata.models.dns_model import DnsModel
from strata.models.environment_model import EnvironmentModel
from strata.models.firewall_model import FirewallModel
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.network_model import NetworkModel
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.provider_model import ProviderModel
from strata.models.resource_model import ResourceModel
from strata.models.tenant_model import TenantModel
from strata.models.version_lock_model import VersionLockModel
from strata.models.version_manifest_model import VersionManifestModel
from strata.models.workspace_model import WorkspaceModel
from strata.utils.config import SOLUTION_DIR, SOLUTION_SCHEMAS_DIR

# Maps every PlatformKind to its top-level Pydantic model class.
_KIND_TO_MODEL = {
    PlatformKind.CONFIGURATION: ConfigurationModel,
    PlatformKind.TENANT: TenantModel,
    PlatformKind.DEPLOYMENT: DeploymentModel,
    PlatformKind.DEPLOYMENT_MANIFEST: DeploymentManifestModel,
    PlatformKind.DNS: DnsModel,
    PlatformKind.ENVIRONMENT: EnvironmentModel,
    PlatformKind.FIREWALL: FirewallModel,
    PlatformKind.MODULE: ModuleModel,
    PlatformKind.NAMESPACE: NamespaceModel,
    PlatformKind.NETWORK: NetworkModel,
    PlatformKind.PLATFORM_MODEL: PlatformArtifactModel,
    PlatformKind.PROVIDER: ProviderModel,
    PlatformKind.RESOURCE: ResourceModel,
    PlatformKind.WORKSPACE: WorkspaceModel,
    PlatformKind.VERSION_LOCK: VersionLockModel,
    PlatformKind.VERSION_MANIFEST: VersionManifestModel,
}


@click.group(name="schema", help="Inspect JSON schemas for platform YAML document kinds.")
def schema_group():
    """Schema command group."""
    pass


@schema_group.command(name="list", help="List all supported platform document kinds.")
@click_output_format
def schema_list(output: Optional[str] = None) -> None:
    """List all supported platform document kinds."""
    from strata.models.common_models import INTERNAL_KINDS

    kinds = sorted(k.value for k in _KIND_TO_MODEL)

    if output == "json":
        items = [
            {"kind": k.value, "model": m.__name__, "internal": k in INTERNAL_KINDS} for k, m in _KIND_TO_MODEL.items()
        ]
        click.echo(json.dumps({"kinds": sorted(items, key=lambda x: x["kind"])}, indent=2))
        return

    if output == "text":
        for kind in kinds:
            click.echo(kind)
        return

    # Console mode — simple table
    click.echo("")
    click.echo(f"  {'KIND':<24}  MODEL CLASS")
    click.echo(f"  {'─' * 24}  {'─' * 30}")
    for kind in PlatformKind:
        model_cls = _KIND_TO_MODEL.get(kind)
        if model_cls:
            suffix = "  (internal)" if kind in INTERNAL_KINDS else ""
            click.echo(f"  {kind.value:<24}  {model_cls.__name__}{suffix}")
    click.echo("")


@schema_group.command(name="get", help="Emit the JSON Schema for a platform document kind.")
@click.argument("kind")
@click_output_format
def schema_get(kind: str, output: Optional[str] = None) -> None:
    """Emit the JSON Schema for a platform document kind (e.g. deployment, environment)."""
    try:
        platform_kind = PlatformKind(kind.lower())
    except ValueError:
        valid = ", ".join(sorted(k.value for k in _KIND_TO_MODEL))
        raise click.UsageError(f"Unknown kind '{kind}'. Valid kinds: {valid}") from None

    model_cls = _KIND_TO_MODEL.get(platform_kind)
    if model_cls is None:
        raise click.UsageError(f"No schema available for kind '{kind}'.")

    schema = model_cls.model_json_schema()  # type: ignore[attr-defined]

    if output in (None, "json"):
        click.echo(json.dumps(schema, indent=2))
        return

    if output == "text":
        # Flat summary: title + top-level required fields
        title = schema.get("title", model_cls.__name__)
        required = schema.get("required", [])
        properties = list(schema.get("properties", {}).keys())
        click.echo(f"Kind:       {platform_kind.value}")
        click.echo(f"Model:      {title}")
        click.echo(f"Required:   {', '.join(required) if required else '(none)'}")
        click.echo(f"Properties: {', '.join(properties)}")
        return

    # Console mode — same as text summary
    click.echo("")
    click.echo(f"  Kind:       {platform_kind.value}")
    click.echo(f"  Model:      {schema.get('title', model_cls.__name__)}")
    required = schema.get("required", [])
    click.echo(f"  Required:   {', '.join(required) if required else '(none)'}")
    click.echo(f"  Properties: {', '.join(schema.get('properties', {}).keys())}")
    click.echo("")
    click.echo("  Run with --output json to get the full JSON Schema.")
    click.echo("")


@schema_group.command(name="export", help="Export JSON Schemas for all document kinds to files.")
@click.option(
    "--output-dir",
    default=f"{SOLUTION_DIR}/{SOLUTION_SCHEMAS_DIR}",
    show_default=True,
    help="Directory to write schema files. Created if it does not exist.",
)
def schema_export(output_dir: str) -> None:
    """Export JSON Schemas for all platform document kinds to individual files."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    written = []
    errors = []
    for kind, model_cls in _KIND_TO_MODEL.items():
        schema_file = output_path / f"{kind.value}.json"
        try:
            schema_file.write_text(json.dumps(model_cls.model_json_schema(), indent=2), encoding="utf-8")  # type: ignore[attr-defined]
            written.append(schema_file)
        except Exception as exc:
            errors.append(f"  {kind.value}: {exc}")

    click.echo("")
    for f in written:
        click.echo(f"  Wrote: {f}")
    if errors:
        click.echo("")
        for e in errors:
            click.echo(f"  ERROR: {e}", err=True)
    click.echo(f"\n  {len(written)} schema(s) exported to {output_path}")
    click.echo("")


# Maps each user-authored kind to glob patterns that match its files in a standard workspace layout.
# Internal artifact kinds (platform_model, deployment-manifest) are excluded — not user-authored.
_KIND_TO_GLOBS: dict = {
    PlatformKind.CONFIGURATION: ["config/**/*.yaml"],
    PlatformKind.DEPLOYMENT: ["deploy/**/*.yaml"],
    PlatformKind.DNS: ["dns/**/*.yaml"],
    PlatformKind.ENVIRONMENT: ["envs/**/*.yaml", "environments/**/*.yaml"],
    PlatformKind.FIREWALL: ["firewalls/**/*.yaml"],
    PlatformKind.MODULE: ["modules/**/*.yaml"],
    PlatformKind.NAMESPACE: ["namespaces/**/*.yaml"],
    PlatformKind.NETWORK: ["networks/**/*.yaml"],
    PlatformKind.PROVIDER: ["providers/**/*.yaml"],
    PlatformKind.RESOURCE: ["resources/**/*.yaml"],
    PlatformKind.TENANT: ["tenants/**/*.yaml"],
    PlatformKind.WORKSPACE: ["stack/**/*.yaml"],
}


@schema_group.command(name="wire", help="Wire JSON Schemas into .vscode/settings.json for YAML autocomplete.")
@click.option(
    "--work-path",
    default=None,
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=str),
    help="Workspace root. Defaults to current directory.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be written without making changes.",
)
def schema_wire(work_path: Optional[str], dry_run: bool) -> None:
    """Merge yaml.schemas into .vscode/settings.json and export schemas.

    Reads the existing .vscode/settings.json (if any), merges in the
    yaml.schemas mapping for all user-authored kinds, and writes it back.
    All other settings are preserved.  Also exports the JSON Schema files
    to .strata/schemas/ so the paths exist.
    """
    root = Path(work_path) if work_path else Path.cwd()
    schemas_dir = root / SOLUTION_DIR / SOLUTION_SCHEMAS_DIR
    vscode_dir = root / ".vscode"
    settings_file = vscode_dir / "settings.json"

    # --- Build the yaml.schemas mapping -------------------------------------------
    schemas_prefix = "${workspaceFolder}/" + SOLUTION_DIR + "/" + SOLUTION_SCHEMAS_DIR
    yaml_schemas: dict = {}
    for kind, globs in _KIND_TO_GLOBS.items():
        schema_key = f"{schemas_prefix}/{kind.value}.json"
        yaml_schemas[schema_key] = globs if len(globs) > 1 else globs[0]

    # --- Read existing settings.json or start fresh --------------------------------
    existing: dict = {}
    if settings_file.exists():
        try:
            existing = json.loads(settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise click.ClickException(f".vscode/settings.json is not valid JSON: {exc}") from exc

    merged = dict(existing)
    merged["yaml.schemas"] = yaml_schemas

    merged_text = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"

    # --- Dry-run mode: just show the diff -----------------------------------------
    if dry_run:
        click.echo("")
        click.echo(f"  Would write: {settings_file}")
        click.echo(f"  yaml.schemas entries: {len(yaml_schemas)}")
        click.echo("")
        click.echo(json.dumps({"yaml.schemas": yaml_schemas}, indent=2))
        click.echo("")
        return

    # --- Export schemas -----------------------------------------------------------
    schemas_dir.mkdir(parents=True, exist_ok=True)
    export_errors = []
    for kind, model_cls in _KIND_TO_MODEL.items():
        if kind not in _KIND_TO_GLOBS:
            continue  # skip internal artifact kinds
        schema_file = schemas_dir / f"{kind.value}.json"
        try:
            schema_file.write_text(json.dumps(model_cls.model_json_schema(), indent=2), encoding="utf-8")  # type: ignore[attr-defined]
        except Exception as exc:
            export_errors.append(f"  {kind.value}: {exc}")

    # --- Write settings.json ------------------------------------------------------
    vscode_dir.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(merged_text, encoding="utf-8")

    click.echo("")
    click.echo(f"  Schemas exported  → {schemas_dir}")
    click.echo(f"  Settings updated  → {settings_file}")
    click.echo(f"  yaml.schemas entries: {len(yaml_schemas)}")
    if export_errors:
        click.echo("")
        for e in export_errors:
            click.echo(f"  WARNING: {e}", err=True)
    click.echo("")
    click.echo("  Reload VS Code (Ctrl+Shift+P → 'Reload Window') for changes to take effect.")
    click.echo("")
