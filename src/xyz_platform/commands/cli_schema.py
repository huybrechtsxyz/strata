"""Click CLI wiring for the ``schema`` command group."""

import json
from typing import Optional

import click

from xyz_platform.commands.cli_common import click_output_format
from xyz_platform.models.common_models import PlatformKind
from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.deployment_model import DeploymentModel
from xyz_platform.models.environment_model import EnvironmentModel
from xyz_platform.models.firewall_model import FirewallModel
from xyz_platform.models.module_model import ModuleModel
from xyz_platform.models.namespace_model import NamespaceModel
from xyz_platform.models.platform_artifact_model import PlatformArtifactModel
from xyz_platform.models.provider_model import ProviderModel
from xyz_platform.models.resource_model import ResourceModel
from xyz_platform.models.workspace_model import WorkspaceModel

# Maps every PlatformKind to its top-level Pydantic model class.
_KIND_TO_MODEL = {
    PlatformKind.CONFIGURATION: ConfigurationModel,
    PlatformKind.DEPLOYMENT: DeploymentModel,
    PlatformKind.ENVIRONMENT: EnvironmentModel,
    PlatformKind.FIREWALL: FirewallModel,
    PlatformKind.MODULE: ModuleModel,
    PlatformKind.NAMESPACE: NamespaceModel,
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

    schema = model_cls.model_json_schema()

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
