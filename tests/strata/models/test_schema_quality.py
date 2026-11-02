#!/usr/bin/env python3
"""Per-kind quality bar for the exported schemas (docs/design/schema-export.md).

Every kind must pass: a root description that says what the kind is for, a description on every property,
no maintainer jargon in descriptions, a stated rule for every cross-field validator, and an example.
"""

import re
from typing import Any

import pytest
from pydantic import BaseModel

from strata.controllers.solution_controller import SERVICE_BY_KIND
from strata.integrations.registry import _KNOWN, get_class
from strata.models.audit_model import EVENT_DEFAULTS, AuditConfigModel
from strata.models.common_models import PlatformBaseModel, PlatformKind
from strata.models.environment_model import EnvironmentModel
from strata.models.integration_model import Capability, IntegrationModel
from strata.models.provisioning_model import ProvisionerModel
from strata.models.schema_export import export_schema, render_schema
from strata.models.store_model import FeatureStoreType, SecretStoreType, VariableStoreType
from strata.utils.builtin_types import ProvisionerType

#: Models whose `after` validators only normalise or restate a constraint the schema already shows.
#: class name -> why no rule sentence is needed.
_NO_RULES: dict[str, str] = {
    "ResourceDependencyModel": "its only check restates that `category` is required, which the schema already shows",
    "VersionModel": "its only check restates that `kind` must be `version`, which every kind already shows",
}

#: Text that is for maintainers, not for a reader of the schema.
_JARGON = re.compile(
    r"ADR-\d+|\bPhase \d|\bv1\b|docs/(?:design|work|archive|decisions)|\bArm \d|gap #|\bbuilders?\b|"
    r"service layer|\binert\b|\bPorted\b|\bported\b|\.py\b|\b[A-Z][a-z]+(?:[A-Z][a-z]+)+Model\b"
)

_KINDS = list(SERVICE_BY_KIND)


def _model_of(kind: PlatformKind) -> type[BaseModel]:
    return SERVICE_BY_KIND[kind](data={})._get_model_class()


def _all_models() -> dict[str, type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    stack: list[type[BaseModel]] = [PlatformBaseModel]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            stack.append(sub)
            found.setdefault(sub.__name__, sub)
    return found


def _definitions(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {"<root>": schema, **schema.get("$defs", {})}


def _descriptions(node: Any):
    if isinstance(node, dict):
        if isinstance(node.get("description"), str):
            yield node["description"]
        for value in node.values():
            yield from _descriptions(value)
    elif isinstance(node, list):
        for item in node:
            yield from _descriptions(item)


@pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
def test_root_description_says_what_the_kind_is_for(kind):
    description = export_schema(_model_of(kind), kind).get("description", "")
    assert description, f"{kind.value}: no root description"
    assert not description.startswith("Root model"), f"{kind.value}: root description is the placeholder"


@pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
def test_every_property_has_a_description(kind):
    schema = export_schema(_model_of(kind), kind)
    missing = [
        f"{name}.{prop}"
        for name, definition in _definitions(schema).items()
        for prop, node in (definition.get("properties") or {}).items()
        if not node.get("description")
    ]
    assert not missing, f"{kind.value}: undescribed properties: {missing}"


@pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
def test_no_description_mentions_internals(kind):
    schema = export_schema(_model_of(kind), kind)
    leaks = sorted({m.group(0) + " in: " + d[:80] for d in _descriptions(schema) if (m := _JARGON.search(d))})
    assert not leaks, f"{kind.value}: descriptions that mention internals (move them under 'Design notes:'): {leaks}"


@pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
def test_every_model_with_an_after_validator_states_its_rules(kind):
    schema = export_schema(_model_of(kind), kind)
    models = _all_models()
    missing = []
    for name in _definitions(schema):
        cls = models.get(_model_of(kind).__name__ if name == "<root>" else name)
        if cls is None or cls.__name__ in _NO_RULES:
            continue
        own = [
            attr
            for attr, decorator in cls.__pydantic_decorators__.model_validators.items()
            if decorator.info.mode == "after" and attr in vars(cls)
        ]
        if own and not getattr(cls, "schema_rules", ()):
            missing.append(f"{cls.__name__} ({', '.join(own)})")
    assert not missing, f"{kind.value}: models with validators but no schema_rules: {missing}"


@pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
def test_the_schema_carries_at_least_one_example(kind):
    assert export_schema(_model_of(kind), kind).get("examples"), f"{kind.value}: no examples"


def test_audit_event_names_written_in_the_schema_match_the_known_event_types():
    schema = render_schema(AuditConfigModel)
    text = schema["properties"]["event_overrides"]["description"] + " ".join(
        rule for rule in AuditConfigModel.schema_rules
    )
    sink = render_schema(AuditConfigModel)["$defs"]["AuditSinkModel"]["description"]
    for event in EVENT_DEFAULTS:
        assert f"`{event}`" in text, f"event_overrides does not name {event}"
        assert f"`{event}`" in sink, f"AuditSinkModel does not name {event}"


def test_every_built_in_provisioner_tool_is_named_in_the_tool_description():
    tool = render_schema(ProvisionerModel)["properties"]["tool"]["description"]
    missing = [t.value for t in ProvisionerType if t.value not in tool]
    assert not missing, f"the `tool` description does not name: {missing}"


def _integration_definition(name: str) -> dict[str, Any]:
    return render_schema(IntegrationModel)["$defs"][name]


def test_integration_type_catalogue_matches_the_registry():
    description = _integration_definition("IntegrationSpecModel")["properties"]["type"]["description"]
    listed = {
        m.group(1): set(m.group(2).split(", "))
        for m in re.finditer(r"^- `([a-z-]+)`: ([a-z_, ]+)\.", description, re.M)
    }
    assert set(listed) == set(_KNOWN), "the `type` catalogue and the registry list different types"
    wrong = {t: (listed[t], {str(c) for c in get_class(t).CAPABILITIES}) for t in _KNOWN}
    wrong = {t: pair for t, pair in wrong.items() if pair[0] != pair[1]}
    assert not wrong, f"capabilities differ from the integration classes (listed, actual): {wrong}"


def test_every_core_capability_is_explained_in_the_capabilities_description():
    description = _integration_definition("IntegrationSpecModel")["properties"]["capabilities"]["description"]
    missing = [c.value for c in Capability if f"`{c.value}`" not in description]
    assert not missing, f"the `capabilities` description does not explain: {missing}"


@pytest.mark.parametrize(
    ("capability", "store_type"),
    [
        (Capability.VARIABLES, VariableStoreType),
        (Capability.SECRETS, SecretStoreType),
        (Capability.FEATURES, FeatureStoreType),
    ],
    ids=lambda v: getattr(v, "value", getattr(v, "__name__", v)),
)
def test_stores_that_resolve_through_an_integration_are_named_in_their_store_type_description(capability, store_type):
    resolved = {t for t in _KNOWN if capability in get_class(t).CAPABILITIES} & {m.value for m in store_type}
    assert resolved, f"no registered integration provides {capability.value} for {store_type.__name__}"
    description = render_schema(EnvironmentModel)["$defs"][store_type.__name__]["description"]
    missing = [t for t in sorted(resolved) if f"`{t}`" not in description]
    assert not missing, f"{store_type.__name__} does not name the resolvable stores: {missing}"
