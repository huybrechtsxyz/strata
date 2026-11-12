#!/usr/bin/env python3
"""Tests for the ``${artifact:NAME.FIELD}`` token's syntax and resolution in `utils/value_tokens.py`
(docs/archive/artifact-gap-analysis.md, design 1). The entries themselves are built in
`controllers/artifact_tokens.py` and tested there."""

import pytest

from strata.utils.value_tokens import (
    artifact_key,
    artifact_token_problem,
    extract_value_tokens,
    has_value_tokens,
    referenced_artifact_keys,
    resolve_value_tokens,
    resolve_value_tokens_renaming_secrets,
    split_artifact_token_key,
    validate_value_tokens,
)

_ARTIFACT_VALUES = {
    "artifact:api.image_name": "registry.example.com/api",
    "artifact:api.image_tag": "1.4.2",
    "artifact:api.image_ref": "registry.example.com/api:1.4.2",
    "artifact:untagged.image_name": "example/untagged",
    "artifact:untagged.image_ref": "example/untagged",
}


def test_artifact_key_is_the_prefixed_name_and_field():
    assert artifact_key("api", "image_tag") == "artifact:api.image_tag"


@pytest.mark.parametrize(
    ("key", "expected"),
    [("api.image_tag", ("api", "image_tag")), ("api", ("api", None)), ("api.", ("api", None))],
)
def test_split_artifact_token_key_splits_at_the_first_dot(key, expected):
    assert split_artifact_token_key(key) == expected


def test_an_artifact_token_is_well_formed():
    validate_value_tokens("${artifact:api.image_name}:${artifact:api.image_tag}")
    assert extract_value_tokens("${artifact:api.image_ref}") == [("artifact", "api.image_ref")]
    assert has_value_tokens("${artifact:api.image_ref}")


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("image_name", "registry.example.com/api"),
        ("image_tag", "1.4.2"),
        ("image_ref", "registry.example.com/api:1.4.2"),
    ],
)
def test_resolve_value_tokens_reads_the_prefixed_artifact_key(field, expected):
    assert resolve_value_tokens(f"${{artifact:api.{field}}}", _ARTIFACT_VALUES) == expected


def test_an_artifact_token_combines_with_other_kinds():
    values = {"env": "prd", **_ARTIFACT_VALUES}
    assert resolve_value_tokens("${artifact:api.image_name}-${var:env}", values) == "registry.example.com/api-prd"


def test_an_output_shaped_like_an_artifact_key_does_not_collide():
    values = {"api.image_tag": "from-an-output", **_ARTIFACT_VALUES}
    assert resolve_value_tokens("${output:api.image_tag}", values) == "from-an-output"
    assert resolve_value_tokens("${artifact:api.image_tag}", values) == "1.4.2"


def test_an_escaped_artifact_token_stays_literal():
    escaped = "$" + "${artifact:api.image_tag}"
    assert resolve_value_tokens(escaped, _ARTIFACT_VALUES) == "${artifact:api.image_tag}"
    assert referenced_artifact_keys(escaped) == set()


@pytest.mark.parametrize(
    ("token", "message"),
    [
        ("${artifact:untagged.image_tag}", "artifact 'untagged' has no image_tag"),
        ("${artifact:ghost.image_tag}", "there is no artifact named 'ghost'"),
        ("${artifact:api.digest}", "is not NAME.FIELD"),
        ("${artifact:api}", "is not NAME.FIELD"),
    ],
)
def test_an_unresolved_artifact_token_says_what_is_missing(token, message):
    with pytest.raises(ValueError, match=message) as excinfo:
        resolve_value_tokens(token, _ARTIFACT_VALUES)
    # Not the internal flat-map key.
    assert "'artifact:" not in str(excinfo.value)


def test_an_unknown_artifact_lists_the_artifacts():
    assert artifact_token_problem("ghost.image_name", _ARTIFACT_VALUES) == (
        "'${artifact:ghost.image_name}': there is no artifact named 'ghost'. Artifacts: ['api', 'untagged']."
    )
    assert artifact_token_problem("api.image_name", _ARTIFACT_VALUES) is None


def test_an_untagged_artifact_still_has_a_bare_image_ref():
    assert resolve_value_tokens("${artifact:untagged.image_ref}", _ARTIFACT_VALUES) == "example/untagged"


def test_a_compose_document_resolves_an_artifact_token_in_place():
    resolved, secrets = resolve_value_tokens_renaming_secrets(
        {"services": {"api": {"image": "${artifact:api.image_ref}"}}}, _ARTIFACT_VALUES
    )
    assert resolved == {"services": {"api": {"image": "registry.example.com/api:1.4.2"}}}
    assert secrets == {}


def test_referenced_artifact_keys_walks_nested_values():
    node = {"image": {"repository": "${artifact:api.image_name}", "tag": ["${artifact:api.image_tag}"]}}
    assert referenced_artifact_keys(node) == {"api.image_name", "api.image_tag"}
    assert referenced_artifact_keys({"x": "${var:api.image_tag}"}) == set()
