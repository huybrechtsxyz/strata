#!/usr/bin/env python3
"""Tests for `integration_resolution.resolve_integration` (ADR-0022 D2)."""

from pathlib import Path

import pytest

from strata.controllers.integration_resolution import (
    resolve_integration,
    resolve_module_integration,
    resolve_output_store_integration,
)
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.integrations.azure_appconfig_resolver import AzureAppConfigResolver
from strata.integrations.helm import HelmIntegration
from strata.integrations.terraform import TerraformIntegration
from strata.models.common_models import PlatformKind, SourceModel
from strata.models.integration_model import IntegrationMetaModel, IntegrationModel, IntegrationSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel
from strata.utils.errors import UsageError


def _provisioner(name: str = "prov", *, tool: str = "terraform", integration: str | None = None) -> ProvisionerModel:
    return ProvisionerModel(name=name, tool=tool, source=SourceModel(source_path="infra"), integration=integration)


def _integration_doc(name: str, *, type: str = "terraform", enabled: bool = True) -> IntegrationModel:
    return IntegrationModel(
        meta=IntegrationMetaModel(name=name),
        spec=IntegrationSpecModel(type=type, enabled=enabled),
    )


def _index(*integrations: IntegrationModel) -> DocumentIndex:
    index = DocumentIndex()
    for model in integrations:
        ref = DocumentRef(kind=PlatformKind.INTEGRATION, name=model.meta.name)
        index.add(IndexEntry(ref=ref, model=model, source=Path(f"{model.meta.name}.yaml")))
    return index


def test_named_binding_wins_even_with_other_candidates_present():
    index = _index(
        _integration_doc("tf_prod", type="terraform"),
        _integration_doc("tf_dev", type="terraform"),
    )
    provisioner = _provisioner(integration="tf_dev")

    integration = resolve_integration(index, provisioner)

    assert isinstance(integration, TerraformIntegration)
    assert integration.config is not None
    assert integration.config.meta.name == "tf_dev"


def test_named_binding_missing_from_index_raises():
    index = _index()
    provisioner = _provisioner(integration="ghost")

    with pytest.raises(UsageError, match="ghost"):
        resolve_integration(index, provisioner)


def test_auto_bind_with_zero_candidates_falls_back_to_env_path_only():
    index = _index()
    provisioner = _provisioner()

    integration = resolve_integration(index, provisioner)

    assert isinstance(integration, TerraformIntegration)
    assert integration.config is None


def test_auto_bind_with_one_enabled_candidate():
    index = _index(_integration_doc("tf_main", type="terraform"))
    provisioner = _provisioner()

    integration = resolve_integration(index, provisioner)

    assert integration.config is not None
    assert integration.config.meta.name == "tf_main"


def test_auto_bind_ignores_disabled_candidates():
    index = _index(_integration_doc("tf_disabled", type="terraform", enabled=False))
    provisioner = _provisioner()

    integration = resolve_integration(index, provisioner)

    assert integration.config is None


def test_auto_bind_with_multiple_candidates_names_every_one():
    index = _index(
        _integration_doc("tf_a", type="terraform"),
        _integration_doc("tf_b", type="terraform"),
    )
    provisioner = _provisioner()

    with pytest.raises(UsageError, match="tf_a.*tf_b|tf_b.*tf_a"):
        resolve_integration(index, provisioner)


def test_auto_bind_ignores_candidates_of_a_different_type():
    index = _index(_integration_doc("helm_main", type="helm"))
    provisioner = _provisioner(tool="terraform")

    integration = resolve_integration(index, provisioner)

    assert isinstance(integration, TerraformIntegration)
    assert integration.config is None


def test_store_integration_tool_raises_a_clear_error():
    provisioner = _provisioner(tool="infisical")
    index = _index()

    with pytest.raises(UsageError, match="not an infrastructure/container integration"):
        resolve_integration(index, provisioner)


def test_unregistered_tool_type_raises_usage_error_not_a_bare_integration_error():
    """registry.get() raises IntegrationNotFoundError (a plain Exception, not
    a StrataError) for a real, documented-but-not-yet-ported v1 type - left
    unguarded this would escape command_run()'s `except StrataError` as a
    raw traceback instead of a clean exit code."""
    provisioner = _provisioner(tool="ansible")
    index = _index()

    with pytest.raises(UsageError, match="ansible"):
        resolve_integration(index, provisioner)


# ---------------------------------------------------------------------------
# resolve_module_integration() — ADR-0022 D5 (bare type, no named binding)
# ---------------------------------------------------------------------------


def test_module_integration_auto_binds_with_zero_candidates():
    integration = resolve_module_integration(_index(), "helm")

    assert isinstance(integration, HelmIntegration)
    assert integration.config is None


def test_module_integration_auto_binds_with_one_enabled_candidate():
    index = _index(_integration_doc("helm_main", type="helm"))

    integration = resolve_module_integration(index, "helm")

    assert integration.config is not None
    assert integration.config.meta.name == "helm_main"


def test_module_integration_multiple_candidates_names_every_one():
    index = _index(
        _integration_doc("helm_a", type="helm"),
        _integration_doc("helm_b", type="helm"),
    )

    with pytest.raises(UsageError, match="helm_a.*helm_b|helm_b.*helm_a"):
        resolve_module_integration(index, "helm")


def test_module_integration_store_type_raises_a_clear_error():
    with pytest.raises(UsageError, match="not an infrastructure/container integration"):
        resolve_module_integration(_index(), "infisical")


def test_module_integration_unregistered_type_raises_usage_error():
    with pytest.raises(UsageError, match="ansible"):
        resolve_module_integration(_index(), "ansible")


# ---------------------------------------------------------------------------
# resolve_output_store_integration() — docs/work/cross-run-step-output-
# delivery.md's Implementation Plan step 1. Same named-binding-wins-else-
# auto-bind duality as resolve_integration(), except both fields unset is
# not an error (feature disabled for this workspace), not a fallback.
# ---------------------------------------------------------------------------


def _workspace(*, output_store: str | None = None, output_store_integration: str | None = None) -> WorkspaceModel:
    return WorkspaceModel(
        meta=WorkspaceMetaModel(name="main"),
        spec=WorkspaceSpecModel(
            providers=["azure-main"],
            provisioners=[_provisioner()],
            output_store=output_store,
            output_store_integration=output_store_integration,
        ),
    )


def test_output_store_both_unset_returns_none_not_an_error():
    """Unlike resolve_integration() (a Provisioner always names a tool),
    neither field set means the feature is disabled for this workspace —
    today's in-memory-only behavior, not a usage error."""
    integration = resolve_output_store_integration(_index(), _workspace())

    assert integration is None


def test_output_store_named_binding_wins_even_with_other_candidates_present():
    index = _index(
        _integration_doc("appconfig_prod", type="azure-appconfig"),
        _integration_doc("appconfig_dev", type="azure-appconfig"),
    )
    workspace = _workspace(output_store_integration="appconfig_dev")

    integration = resolve_output_store_integration(index, workspace)

    assert isinstance(integration, AzureAppConfigResolver)
    assert integration.config is not None
    assert integration.config.meta.name == "appconfig_dev"


def test_output_store_named_binding_missing_from_index_raises():
    workspace = _workspace(output_store_integration="ghost")

    with pytest.raises(UsageError, match="ghost"):
        resolve_output_store_integration(_index(), workspace)


def test_output_store_auto_bind_with_zero_candidates_falls_back_to_env_only():
    workspace = _workspace(output_store="azure-appconfig")

    integration = resolve_output_store_integration(_index(), workspace)

    assert isinstance(integration, AzureAppConfigResolver)
    assert integration.config is None


def test_output_store_auto_bind_with_one_enabled_candidate():
    index = _index(_integration_doc("appconfig_main", type="azure-appconfig"))
    workspace = _workspace(output_store="azure-appconfig")

    integration = resolve_output_store_integration(index, workspace)

    assert integration is not None
    assert integration.config is not None
    assert integration.config.meta.name == "appconfig_main"


def test_output_store_auto_bind_with_multiple_candidates_names_every_one():
    index = _index(
        _integration_doc("appconfig_a", type="azure-appconfig"),
        _integration_doc("appconfig_b", type="azure-appconfig"),
    )
    workspace = _workspace(output_store="azure-appconfig")

    with pytest.raises(UsageError, match="appconfig_a.*appconfig_b|appconfig_b.*appconfig_a"):
        resolve_output_store_integration(index, workspace)


def test_output_store_non_key_value_type_raises_a_clear_error():
    workspace = _workspace(output_store="terraform")

    with pytest.raises(UsageError, match="does not implement the key/value store capability"):
        resolve_output_store_integration(_index(), workspace)


def test_output_store_lacking_cas_support_raises_a_clear_error():
    """Defensive check (should not happen post-validation; a static check
    already catches this in semantic_checks.py) — azure-keyvault does
    implement KeyValueStoreIntegration, but SUPPORTS_CAS is False."""
    workspace = _workspace(output_store="azure-keyvault")

    with pytest.raises(UsageError, match="does not support compare-and-swap"):
        resolve_output_store_integration(_index(), workspace)


def test_output_store_unregistered_type_raises_usage_error():
    workspace = _workspace(output_store="vault")

    with pytest.raises(UsageError, match="vault"):
        resolve_output_store_integration(_index(), workspace)
