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
from strata.models.workspace_model import WorkspaceModel

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
}


@click.group(name="schema", help="Inspect JSON schemas for platform YAML document kinds.")
def schema_group():
    """Schema command group."""
    pass


@schema_group.command(name="list", help="List all supported platform document kinds.")
@click_output_format
def schema_list(output: Optional[str] = None) -> None:
    """List all supported platform document kinds."""
    kinds = sorted(k.value for k in _KIND_TO_MODEL)

    if output == "json":
        click.echo(json.dumps({"kinds": kinds}, indent=2))
        return

    if output == "text":
        for kind in kinds:
            click.echo(kind)
        return

    # Console mode — simple table
    click.echo("")
    click.echo(f"  {'KIND':<20}  MODEL CLASS")
    click.echo(f"  {'─' * 20}  {'─' * 30}")
    for kind in PlatformKind:
        model_cls = _KIND_TO_MODEL.get(kind)
        if model_cls:
            click.echo(f"  {kind.value:<20}  {model_cls.__name__}")
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
    default=".strata/schemas",
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
