#!/usr/bin/env python3
"""Tests for reference-field introspection."""

from typing import Annotated

from pydantic import BaseModel

from strata.models.common_models import PlatformKind
from strata.models.reference_fields import References, RemoteReference, extract_references


class _Leaf(BaseModel):
    resource: Annotated[str, References(PlatformKind.RESOURCE)]
    other: str


class _Doc(BaseModel):
    providers: list[Annotated[str, References(PlatformKind.PROVIDER)]]
    single: Annotated[str, References(PlatformKind.TENANT)] | None = None
    items: list[_Leaf] | None = None
    plain: str | None = None
    remote: Annotated[str, RemoteReference()] | None = None


# ---------------------------------------------------------------------------
# The two bugs found while building this
# ---------------------------------------------------------------------------


def test_finds_a_reference_inside_a_list():
    """`list[Annotated[...]]` — pydantic leaves this Annotated untouched."""
    rules = extract_references(_Doc)
    assert any(r.path == "providers[]" and r.kind is PlatformKind.PROVIDER for r in rules)


def test_finds_a_reference_behind_pep604_optional():
    """`X | None`, not `Optional[X]` — every v2 model uses this form.

    `get_origin(X | None)` is `types.UnionType`, a different object from
    `typing.Union`; missing that silently drops every optional reference.
    """
    rules = extract_references(_Doc)
    assert any(r.path == "single" and r.kind is PlatformKind.TENANT for r in rules)


def test_finds_a_reference_on_a_bare_annotated_field():
    """A field whose *entire* type is `Annotated[...]` — pydantic hoists the
    metadata into `FieldInfo.metadata`, leaving `.annotation` bare.
    """
    rules = extract_references(_Leaf)
    assert any(r.path == "resource" and r.kind is PlatformKind.RESOURCE for r in rules)


def test_finds_a_reference_nested_in_a_list_of_optional_models():
    """The combination that exposed both bugs at once: `list[Model] | None`
    where `Model` itself has a bare-Annotated reference field.
    """
    rules = extract_references(_Doc)
    assert any(r.path == "items[].resource" and r.kind is PlatformKind.RESOURCE for r in rules)


# ---------------------------------------------------------------------------
# Remotes and absence
# ---------------------------------------------------------------------------


def test_remote_reference_has_no_kind():
    """`kind=None` is the signal to check against the manifest, not the index."""
    rules = extract_references(_Doc)
    remote_rules = [r for r in rules if r.path == "remote"]
    assert remote_rules == [type(remote_rules[0])("remote", None)]


def test_unannotated_fields_produce_no_rule():
    """Most PlatformName-shaped fields are not references; silence is correct."""
    rules = extract_references(_Doc)
    assert not any(r.path == "plain" for r in rules)
    assert not any(r.path.endswith("other") for r in rules)


def test_a_model_with_nothing_annotated_yields_no_rules():
    """A document with no reference fields is not an error."""

    class Empty(BaseModel):
        name: str

    assert extract_references(Empty) == ()


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_extraction_is_cached_per_class():
    """A kind's model class is fixed; introspection runs once regardless of
    how many documents of that kind are loaded.
    """
    assert extract_references(_Doc) is extract_references(_Doc)
