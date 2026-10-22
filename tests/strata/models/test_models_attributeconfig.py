#!/usr/bin/env python3
"""Tests for AttributeConfigModel (standalone attribute schema kind) validation.

Phase 1 only — these tests cover the Pydantic/structural shape (target
binding, `schema` passthrough, kind matching). Schema-compiles-as-real-
JSON-Schema and schema-applied-to-matching-documents checks land with the
Phase 2 evaluator; see docs/design/dynamic-configuration-validation.md.
"""

import pytest
from pydantic import ValidationError

from strata.models.artifact_model import ArtifactSpecModel
from strata.models.asset_model import AssetSpecModel
from strata.models.attributeconfig_model import BLOCKS_BY_KIND, AttributeConfigModel, AttributeConfigSpecModel
from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationSpecModel
from strata.models.deployment_model import DeploymentSpecModel
from strata.models.dns_model import DnsSpecModel
from strata.models.environment_model import EnvironmentSpecModel
from strata.models.firewall_model import FirewallSpecModel
from strata.models.integration_model import IntegrationSpecModel
from strata.models.module_model import ModuleSpecModel
from strata.models.namespace_model import NamespaceSpecModel
from strata.models.network_model import NetworkSpecModel
from strata.models.provider_config_model import ProviderConfigSpecModel
from strata.models.provider_model import ProviderSpecModel
from strata.models.resource_model import ResourceSpecModel
from strata.models.tenant_model import TenantSpecModel
from strata.models.topology_config_model import TopologyConfigSpecModel
from strata.models.version_model import VersionSpecModel
from strata.models.workspace_model import WorkspaceSpecModel

#: Every indexable kind's own top-level `*SpecModel`, for the drift-proofing
#: pinning test below. `solution` is deliberately absent — never indexed
#: (semantic_checks.py), so never a reachable attributeconfig target either way.
_SPEC_MODEL_BY_KIND = {
    PlatformKind.ARTIFACT: ArtifactSpecModel,
    PlatformKind.ASSET: AssetSpecModel,
    PlatformKind.ATTRIBUTECONFIG: AttributeConfigSpecModel,
    PlatformKind.CONFIGURATION: ConfigurationSpecModel,
    PlatformKind.DEPLOYMENT: DeploymentSpecModel,
    PlatformKind.DNS: DnsSpecModel,
    PlatformKind.ENVIRONMENT: EnvironmentSpecModel,
    PlatformKind.FIREWALL: FirewallSpecModel,
    PlatformKind.INTEGRATION: IntegrationSpecModel,
    PlatformKind.MODULE: ModuleSpecModel,
    PlatformKind.NAMESPACE: NamespaceSpecModel,
    PlatformKind.NETWORK: NetworkSpecModel,
    PlatformKind.PROVIDER: ProviderSpecModel,
    PlatformKind.PROVIDERCONFIG: ProviderConfigSpecModel,
    PlatformKind.RESOURCE: ResourceSpecModel,
    PlatformKind.TENANT: TenantSpecModel,
    PlatformKind.TOPOLOGYCONFIG: TopologyConfigSpecModel,
    PlatformKind.VERSION: VersionSpecModel,
    PlatformKind.WORKSPACE: WorkspaceSpecModel,
}

_BLOCK_NAMES = ("properties", "custom", "configuration", "variables", "features")


def _minimal_attributeconfig() -> dict:
    return {
        "meta": {"name": "environment-custom"},
        "spec": {
            "target": {"kind": "environment", "block": "custom"},
            "schema": {"type": "object", "properties": {"region": {"type": "string"}}},
        },
    }


def test_attributeconfig_minimal_is_valid():
    """A minimal attribute config document validates successfully."""
    model = AttributeConfigModel.model_validate(_minimal_attributeconfig())
    assert model.meta.name == "environment-custom"
    assert model.spec.target.kind.value == "environment"
    assert model.spec.target.block == "custom"
    assert model.spec.target.selector is None
    assert model.spec.target.view == "document"
    assert model.spec.schema_ == {"type": "object", "properties": {"region": {"type": "string"}}}
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "attributeconfig"


def test_attributeconfig_rejects_mismatched_kind():
    """A document declaring another kind is rejected (see validate_kind_matches)."""
    data = _minimal_attributeconfig()
    data["kind"] = "providerconfig"
    with pytest.raises(ValidationError, match="Expected kind 'attributeconfig'"):
        AttributeConfigModel.model_validate(data)


def test_attributeconfig_rejects_invalid_block():
    """target.block only accepts 'properties', 'custom', or 'configuration'."""
    data = _minimal_attributeconfig()
    data["spec"]["target"]["block"] = "spec"
    with pytest.raises(ValidationError):
        AttributeConfigModel.model_validate(data)


@pytest.mark.parametrize(
    ("kind", "block"),
    [
        ("artifact", "properties"),
        ("artifact", "custom"),
        ("artifact", "configuration"),
        ("resource", "custom"),
        ("resource", "configuration"),
        ("environment", "properties"),
        ("environment", "custom"),
        ("environment", "variables"),
        ("environment", "features"),
    ],
)
def test_attributeconfig_accepts_pair_the_kind_actually_has(kind, block):
    """A `(kind, block)` pair naming a field that kind's own spec model has is accepted."""
    data = _minimal_attributeconfig()
    data["spec"]["target"] = {"kind": kind, "block": block}
    model = AttributeConfigModel.model_validate(data)
    assert model.spec.target.kind.value == kind
    assert model.spec.target.block == block


def test_attributeconfig_rejects_pair_the_kind_does_not_have():
    """`resource` has no top-level `properties` field (only `custom`/`configuration`) — rejected at load,
    not silently evaluated against `{}` forever."""
    data = _minimal_attributeconfig()
    data["spec"]["target"] = {"kind": "resource", "block": "properties"}
    with pytest.raises(
        ValidationError, match="block 'properties' does not exist on kind 'resource' \\(has: configuration, custom\\)"
    ):
        AttributeConfigModel.model_validate(data)


def test_attributeconfig_rejects_any_block_for_a_kind_with_none():
    """`dns`'s own top-level spec model has none of properties/custom/configuration."""
    data = _minimal_attributeconfig()
    data["spec"]["target"] = {"kind": "dns", "block": "properties"}
    with pytest.raises(ValidationError, match="\\(has: none\\)"):
        AttributeConfigModel.model_validate(data)


def test_attributeconfig_rejects_secrets_as_a_block_outright():
    """'secrets' is never a valid `block` value, for any kind/view — a failing schema's message can echo the
    offending value, and a `store: constant` secret's value IS the literal secret."""
    data = _minimal_attributeconfig()
    data["spec"]["target"]["block"] = "secrets"
    with pytest.raises(ValidationError):
        AttributeConfigModel.model_validate(data)


def test_attributeconfig_document_view_rejects_variables_on_a_kind_without_them():
    """`deployment` has no `variables` field of its own (only `environment` does) — `view: document` still
    rejects it, same as any other kind/block mismatch."""
    data = _minimal_attributeconfig()
    data["spec"]["target"] = {"kind": "deployment", "block": "variables"}
    with pytest.raises(ValidationError, match="block 'variables' does not exist on kind 'deployment'"):
        AttributeConfigModel.model_validate(data)


@pytest.mark.parametrize("block", ["properties", "custom", "variables", "features"])
def test_attributeconfig_accepts_effective_view_on_deployment(block):
    """`view: effective` is accepted for every block it has a merge for — including `variables`/`features`,
    even though `deployment` itself has no such field (the design-gap fix: `validate_block_exists_on_kind()`
    no longer applies to `view: effective` at all, docs/work/environment-value-metadata.md)."""
    data = _minimal_attributeconfig()
    data["spec"]["target"] = {"kind": "deployment", "block": block, "view": "effective"}
    model = AttributeConfigModel.model_validate(data)
    assert model.spec.target.view == "effective"


def test_attributeconfig_rejects_effective_view_for_non_deployment_kind():
    """`view: effective` has no merge defined for any kind but `deployment`."""
    data = _minimal_attributeconfig()
    data["spec"]["target"] = {"kind": "environment", "block": "custom", "view": "effective"}
    with pytest.raises(ValidationError, match="view: effective is only supported for target.kind: deployment"):
        AttributeConfigModel.model_validate(data)


def test_attributeconfig_rejects_effective_view_for_configuration_block():
    """`view: effective` has no merge defined for `configuration` (only `properties`/`custom`/`variables`/
    `features`)."""
    data = _minimal_attributeconfig()
    data["spec"]["target"] = {"kind": "firewall", "block": "configuration", "view": "effective"}
    with pytest.raises(ValidationError, match="view: effective is only supported for target.kind: deployment"):
        AttributeConfigModel.model_validate(data)


def test_attributeconfig_blocks_by_kind_matches_every_real_spec_model():
    """Pins `BLOCKS_BY_KIND` against the real `*SpecModel` classes directly, so the table can't silently
    drift from the models it describes (docs/work/environment-value-metadata.md Phase 1)."""
    for kind, spec_model in _SPEC_MODEL_BY_KIND.items():
        expected = frozenset(block for block in _BLOCK_NAMES if block in spec_model.model_fields)
        assert BLOCKS_BY_KIND.get(kind, frozenset()) == expected, (
            f"{kind.value}: table out of sync with {spec_model.__name__}"
        )


def test_attributeconfig_accepts_selector():
    """target.selector narrows which documents of `kind` this schema applies to."""
    data = _minimal_attributeconfig()
    data["spec"]["target"]["selector"] = {"spec.provider": "kamatera"}
    model = AttributeConfigModel.model_validate(data)
    assert model.spec.target.selector == {"spec.provider": "kamatera"}


def test_attributeconfig_schema_accepts_arbitrary_json_schema_content():
    """spec.schema is a verbatim passthrough — any JSON-serializable dict is accepted here."""
    data = _minimal_attributeconfig()
    data["spec"]["schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["region"],
        "properties": {"region": {"type": "string", "pattern": "^[a-z-]+$"}},
        "additionalProperties": False,
    }
    model = AttributeConfigModel.model_validate(data)
    assert model.spec.schema_["required"] == ["region"]


def test_attributeconfig_round_trips_schema_alias():
    """model_dump(by_alias=True) serializes spec.schema_ back under the real 'schema' key."""
    model = AttributeConfigModel.model_validate(_minimal_attributeconfig())
    dumped = model.spec.model_dump(by_alias=True)
    assert "schema" in dumped
    assert "schema_" not in dumped
