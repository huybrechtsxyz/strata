#!/usr/bin/env python3
"""Tests for the JSON Schema export (docs/design/schema-export.md)."""

from typing import Annotated, Any, ClassVar

import jsonschema
import pytest
from pydantic import BaseModel, Field

from strata.controllers.solution_controller import SERVICE_BY_KIND
from strata.models.common_models import PlatformKind, PlatformName
from strata.models.reference_fields import References, RemoteReference, extract_references
from strata.models.schema_examples import EXAMPLES
from strata.models.schema_export import export_schema, reflow, render_schema, strip_design_notes


def _model_of(kind: PlatformKind) -> type[BaseModel]:
    return SERVICE_BY_KIND[kind](data={})._get_model_class()


# ---------------------------------------------------------------------------
# Design notes
# ---------------------------------------------------------------------------


def test_strip_design_notes_drops_that_paragraph_and_everything_after_it():
    text = "What it does.\n\nDesign notes: ported from v1, see ADR-0003.\n\nMore internals."
    assert strip_design_notes(text) == "What it does."


def test_a_description_without_notes_is_unchanged():
    assert strip_design_notes("What it does.\n\nA second paragraph.") == "What it does.\n\nA second paragraph."


def test_reflow_joins_wrapped_lines_but_keeps_list_items_and_paragraphs():
    text = "One sentence that was\nwrapped twice\nin the source.\n\nRules:\n- first\n- second"
    assert reflow(text) == "One sentence that was wrapped twice in the source.\n\nRules:\n- first\n- second"


def test_reflow_keeps_indented_blocks_on_their_own_lines():
    assert reflow("Example::\n\n    a: 1\n    b: 2") == "Example::\n\n    a: 1\n    b: 2"


def test_exported_descriptions_do_not_carry_source_line_wraps():
    class Sample(BaseModel):
        """Wrapped in the
        source file."""

        name: str = Field(description="Wrapped\nfield text.")

    schema = render_schema(Sample)
    assert schema["description"] == "Wrapped in the source file."
    assert schema["properties"]["name"]["description"] == "Wrapped field text."


def test_reflow_joins_the_wrapped_continuation_of_a_list_item():
    text = "Pick one:\n\n- first item that\n  wraps onto a second line\n- second item"
    assert reflow(text) == "Pick one:\n\n- first item that wraps onto a second line\n- second item"


def test_a_scalar_shorthand_model_keeps_its_description_and_rules_on_the_wrapper():
    from pydantic import ConfigDict

    from strata.models.common_models import PlatformBaseModel, scalar_shorthand_schema

    class Slot(PlatformBaseModel):
        """A slot, or just its name."""

        model_config = ConfigDict(json_schema_extra=scalar_shorthand_schema(PlatformName))
        schema_rules: ClassVar[tuple[str, ...]] = ("A rule.",)

        name: PlatformName = Field(description="Slot name.")

    schema = render_schema(Slot)
    assert schema["description"] == "A slot, or just its name.\n\nRules:\n- A rule."
    assert all(
        "description" not in branch or branch["description"] != schema["description"] for branch in schema["anyOf"]
    )


def test_design_notes_are_dropped_from_field_and_model_descriptions():
    class Sample(BaseModel):
        """A sample model.

        Design notes: the reason it exists.
        """

        name: str = Field(description="The name.\n\nDesign notes: why it is a string.")

    schema = render_schema(Sample)
    assert schema["description"] == "A sample model."
    assert schema["properties"]["name"]["description"] == "The name."


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def test_schema_rules_are_appended_to_the_model_description():
    class WithDoc(BaseModel):
        """Either a or b."""

        schema_rules: ClassVar[tuple[str, ...]] = ("Set exactly one of a or b.", "b needs c.")
        a: str | None = None

    class WithoutDoc(BaseModel):
        schema_rules: ClassVar[tuple[str, ...]] = ("Only rule.",)
        a: str | None = None

    assert (
        render_schema(WithDoc)["description"] == "Either a or b.\n\nRules:\n- Set exactly one of a or b.\n- b needs c."
    )
    assert render_schema(WithoutDoc)["description"] == "Rules:\n- Only rule."


def test_a_model_without_rules_keeps_its_description_untouched():
    class Plain(BaseModel):
        """Plain."""

        a: str | None = None

    assert render_schema(Plain)["description"] == "Plain."


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_a_reference_annotation_names_its_target_kind_in_the_schema():
    class Sample(BaseModel):
        one: Annotated[PlatformName, References(PlatformKind.PROVIDER)] = Field(description="one")
        many: list[Annotated[PlatformName, References(PlatformKind.NAMESPACE)]] | None = Field(None, description="many")
        optional: Annotated[PlatformName, References(PlatformKind.INTEGRATION)] | None = Field(None, description="opt")
        remote: Annotated[PlatformName, RemoteReference()] | None = Field(None, description="remote")

    props = render_schema(Sample)["properties"]
    assert props["one"]["x-strata-references"] == "provider"
    assert props["many"]["anyOf"][0]["items"]["x-strata-references"] == "namespace"
    assert props["optional"]["anyOf"][0]["x-strata-references"] == "integration"
    assert props["remote"]["anyOf"][0]["x-strata-remote"] is True
    assert "x-strata-references" not in props["remote"]["anyOf"][0]


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    return node


def _candidates(schema: dict[str, Any], node: dict[str, Any]) -> list[dict[str, Any]]:
    node = _resolve(schema, node)
    found = [node]
    for branch in [*node.get("anyOf", []), *node.get("allOf", [])]:
        found.extend(_candidates(schema, branch))
    return found


def _leaf_candidates(schema: dict[str, Any], path: str) -> list[dict[str, Any]]:
    """The schema node(s) a dotted reference path (`spec.providers[]`) ends at, through $ref/anyOf/items."""
    nodes = [schema]
    for part in path.split("."):
        name, is_list = part.removesuffix("[]"), part.endswith("[]")
        stepped = [
            c["properties"][name]
            for node in nodes
            for c in _candidates(schema, node)
            if name in c.get("properties", {})
        ]
        if is_list:
            stepped = [c["items"] for node in stepped for c in _candidates(schema, node) if "items" in c]
        nodes = stepped
    return [c for node in nodes for c in _candidates(schema, node)]


@pytest.mark.parametrize("kind", list(SERVICE_BY_KIND), ids=lambda k: k.value)
def test_every_reference_strata_resolves_is_marked_in_the_kinds_schema(kind):
    model = _model_of(kind)
    schema = export_schema(model, kind)
    for rule in extract_references(model):
        leaves = _leaf_candidates(schema, rule.path)
        assert leaves, f"{kind.value}: no schema node found for reference path {rule.path}"
        if rule.kind is None:
            assert any(leaf.get("x-strata-remote") is True for leaf in leaves), f"{kind.value}: {rule.path}"
        else:
            assert any(leaf.get("x-strata-references") == rule.kind.value for leaf in leaves), (
                f"{kind.value}: {rule.path} should be marked {rule.kind.value}"
            )


# ---------------------------------------------------------------------------
# Size
# ---------------------------------------------------------------------------


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


@pytest.mark.parametrize("kind", list(SERVICE_BY_KIND), ids=lambda k: k.value)
def test_no_title_and_no_null_default_survives(kind):
    schema = export_schema(_model_of(kind), kind)
    for node in _walk(schema):
        assert not isinstance(node.get("title"), str), f"{kind.value}: a title survived: {node['title']}"
        assert not ("default" in node and node["default"] is None), f"{kind.value}: a `default: null` survived"


def test_model_class_names_do_not_become_titles():
    class Sample(BaseModel):
        a: str | None = None

    schema = render_schema(Sample)
    assert "title" not in schema
    assert "title" not in schema["properties"]["a"]
    assert "default" not in schema["properties"]["a"]


def test_a_field_named_title_is_kept():
    class Sample(BaseModel):
        title: str = Field(description="The title.")

    assert render_schema(Sample)["properties"]["title"]["description"] == "The title."


class _Ruled(BaseModel):
    """A model with a rule."""

    schema_rules: ClassVar[tuple[str, ...]] = ("The first rule.", "The second rule.")

    name: str = Field(description="Name.")


def test_a_described_field_repeats_the_rules_of_the_model_it_references():
    class Holder(BaseModel):
        direct: _Ruled = Field(description="Direct.")
        nullable: _Ruled | None = Field(None, description="Nullable.")
        many: list[_Ruled] = Field(default_factory=list, description="Many.")

    properties = render_schema(Holder)["properties"]
    for name, text in (("direct", "Direct."), ("nullable", "Nullable."), ("many", "Many.")):
        assert properties[name]["description"] == f"{text}\n\nRules:\n- The first rule.\n- The second rule."


def test_an_undescribed_reference_and_the_model_itself_are_left_alone():
    class Holder(BaseModel):
        bare: _Ruled

    schema = render_schema(Holder)
    assert "description" not in schema["properties"]["bare"]
    assert schema["$defs"]["_Ruled"]["description"].count("Rules:") == 1


def test_rules_are_not_repeated_when_the_field_already_carries_them():
    class Holder(BaseModel):
        direct: _Ruled = Field(description="Direct.\n\nRules:\n- Own rule.")

    assert render_schema(Holder)["properties"]["direct"]["description"] == "Direct.\n\nRules:\n- Own rule."


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------

_EXAMPLE_CASES = [(kind, i) for kind, docs in EXAMPLES.items() for i in range(len(docs))]


@pytest.mark.parametrize(("kind", "index"), _EXAMPLE_CASES, ids=[f"{k.value}-{i}" for k, i in _EXAMPLE_CASES])
def test_every_example_is_a_valid_document_of_its_own_kind(kind, index):
    document = EXAMPLES[kind][index]
    assert document["kind"] == kind.value
    model = _model_of(kind)
    model.model_validate(document)
    jsonschema.validate(document, export_schema(model, kind))


def test_examples_are_exported_as_the_root_examples_keyword(monkeypatch):
    kind = PlatformKind.PROVIDER
    monkeypatch.setitem(EXAMPLES, kind, [{"kind": "provider"}])
    assert export_schema(_model_of(kind), kind)["examples"] == [{"kind": "provider"}]
    monkeypatch.delitem(EXAMPLES, kind)
    assert "examples" not in export_schema(_model_of(kind), kind)
