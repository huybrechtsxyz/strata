#!/usr/bin/env python3
"""Tests for `integration_resolution.resolve_integration` (ADR-0022 D2)."""

from pathlib import Path

import pytest

from strata.controllers.integration_resolution import resolve_integration
from strata.controllers.solution_controller import DocumentIndex, DocumentRef, IndexEntry
from strata.integrations.terraform import TerraformIntegration
from strata.models.common_models import PlatformKind, SourceModel
from strata.models.integration_model import IntegrationMetaModel, IntegrationModel, IntegrationSpecModel
from strata.models.provisioning_model import ProvisionerModel
from strata.utils.errors import UsageError


def _provisioner(name: str = "prov", *, tool: str = "terraform", integration: str | None = None) -> ProvisionerModel:
    return ProvisionerModel(
        name=name, tool=tool, source=SourceModel(source_path="infra"), integration=integration
    )


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
