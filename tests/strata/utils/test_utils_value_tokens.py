#!/usr/bin/env python3
"""Tests for `value_tokens.resolve_value_tokens()`/`resolve_value_tokens_in_mapping()`
— the deploy-time Value token resolver (docs/design/value-token-resolution.md,
docs/design/deploy-command.md)."""

import pytest

from strata.utils.value_tokens import (
    extract_value_tokens,
    find_malformed_value_tokens,
    has_value_tokens,
    resolve_value_tokens,
    resolve_value_tokens_in_mapping,
    resolve_value_tokens_renaming_secrets,
    resolve_value_tokens_tracking_secrets,
    strip_escaped_value_tokens,
    validate_value_tokens,
)


def test_resolve_value_tokens_replaces_a_single_token():
    assert resolve_value_tokens("${var:REGION}", {"REGION": "westeurope"}) == "westeurope"


def test_resolve_value_tokens_replaces_multiple_tokens_in_one_string():
    result = resolve_value_tokens(
        "postgres://${var:HOST}/${secret:DB_PASS}",
        {"HOST": "db.internal", "DB_PASS": "hunter2"},
    )
    assert result == "postgres://db.internal/hunter2"


def test_resolve_value_tokens_ignores_kind_prefix_only_key_matters():
    """var/secret/feature only matter to the token's author — resolution reads key only."""
    assert resolve_value_tokens("${feature:ENABLE_X}", {"ENABLE_X": "true"}) == "true"
    assert resolve_value_tokens("${secret:ENABLE_X}", {"ENABLE_X": "true"}) == "true"


def test_resolve_value_tokens_plain_literal_passes_through_unchanged():
    assert resolve_value_tokens("cdn-feeds.omp.com", {}) == "cdn-feeds.omp.com"


def test_resolve_value_tokens_raises_on_unresolved_key():
    with pytest.raises(ValueError, match="did not resolve to a value"):
        resolve_value_tokens("${var:MISSING}", {})


def test_resolve_value_tokens_matches_extract_value_tokens_keys():
    """Sanity check both functions agree on what a token's key is."""
    text = "${var:tf_state_resource_group}"
    (kind, key) = extract_value_tokens(text)[0]
    assert kind == "var"
    assert resolve_value_tokens(text, {key: "rg-prod"}) == "rg-prod"


def test_resolve_value_tokens_in_mapping_resolves_flat_string_values():
    """Real shape: provisioner.backend.configuration's real values (cfg-int-deployment)."""
    data = {
        "resource_group_name": "${var:tf_state_resource_group}",
        "storage_account_name": "${var:tf_state_storage_account}",
        "use_azuread_auth": "true",
    }
    values = {"tf_state_resource_group": "rg-int", "tf_state_storage_account": "stint001"}
    resolved = resolve_value_tokens_in_mapping(data, values)
    assert resolved == {
        "resource_group_name": "rg-int",
        "storage_account_name": "stint001",
        "use_azuread_auth": "true",
    }


def test_resolve_value_tokens_in_mapping_recurses_into_nested_dicts_and_lists():
    data = {
        "nested": {"key": "${var:REGION}"},
        "list": ["${var:REGION}", "literal"],
    }
    resolved = resolve_value_tokens_in_mapping(data, {"REGION": "westeurope"})
    assert resolved == {"nested": {"key": "westeurope"}, "list": ["westeurope", "literal"]}


def test_resolve_value_tokens_in_mapping_passes_through_non_string_leaves():
    data = {"count": 3, "enabled": True, "nothing": None}
    assert resolve_value_tokens_in_mapping(data, {}) == data


def test_resolve_value_tokens_in_mapping_raises_on_unresolved_key():
    with pytest.raises(ValueError, match="did not resolve to a value"):
        resolve_value_tokens_in_mapping({"key": "${secret:MISSING}"}, {})


def test_malformed_token_still_rejected_by_validate_value_tokens_first():
    """resolve_value_tokens() assumes Phase 1 syntax validation already ran —
    confirm a malformed token is still caught upstream, not silently ignored."""
    with pytest.raises(ValueError, match="Malformed Value token"):
        validate_value_tokens("${vars:region}")


# ---------------------------------------------------------------------------
# Escape syntax ($${...}) — docs/design/value-token-resolution.md
# ---------------------------------------------------------------------------


def test_strip_escaped_value_tokens_removes_escaped_span():
    assert strip_escaped_value_tokens("$${TOKEN}") == ""


def test_strip_escaped_value_tokens_leaves_surrounding_text_and_real_tokens_alone():
    result = strip_escaped_value_tokens("prefix-$${TOKEN}-${var:REGION}-suffix")
    assert result == "prefix--${var:REGION}-suffix"


def test_strip_escaped_value_tokens_plain_literal_passes_through_unchanged():
    assert strip_escaped_value_tokens("cdn-feeds.omp.com") == "cdn-feeds.omp.com"


def test_find_malformed_value_tokens_ignores_escaped_candidates():
    """Gatus's real case (.v2-haven): a chart's own '${TOKEN}' placeholder,
    escaped, must not be flagged — that's the entire point of the escape."""
    assert find_malformed_value_tokens("$${TOKEN}") == []


def test_find_malformed_value_tokens_still_catches_an_unescaped_bare_token():
    assert find_malformed_value_tokens("${TOKEN}") == ["${TOKEN}"]


def test_find_malformed_value_tokens_accepts_well_formed_real_tokens():
    assert find_malformed_value_tokens("${var:region}") == []


def test_validate_value_tokens_accepts_an_escaped_literal():
    """Doesn't raise — an escaped '${...}' is never a candidate at all."""
    validate_value_tokens("$${TOKEN}")


def test_has_value_tokens_is_false_for_an_escape_only_string():
    assert has_value_tokens("$${TOKEN}") is False


def test_has_value_tokens_is_true_when_a_real_token_remains_alongside_an_escape():
    assert has_value_tokens("$${TOKEN}-${var:REGION}") is True


def test_extract_value_tokens_ignores_an_escaped_span_entirely():
    """Without escape-awareness, an unanchored match would find '${var:x}'
    inside '$${var:x}' and wrongly treat an intentional literal as a real
    token needing resolution — the exact bug found validating this design."""
    assert extract_value_tokens("$${var:x}") == []


def test_extract_value_tokens_finds_a_real_token_alongside_an_escaped_one():
    result = extract_value_tokens("$${TOKEN}-${var:REGION}")
    assert result == [("var", "REGION")]


def test_resolve_value_tokens_unescapes_an_escaped_literal_without_looking_it_up():
    """The exact bug found validating this design: an unanchored real-token
    match would otherwise find '${TOKEN}' inside '$${TOKEN}' and try to
    resolve it, even though no such key was ever provided."""
    assert resolve_value_tokens("$${TOKEN}", {}) == "${TOKEN}"


def test_resolve_value_tokens_resolves_a_real_token_alongside_an_escaped_one():
    result = resolve_value_tokens("$${TOKEN}-${var:REGION}", {"REGION": "westeurope"})
    assert result == "${TOKEN}-westeurope"


# ---------------------------------------------------------------------------
# ${value:kind.name.path} — docs/design/cross-document-value-references.md's
# 5th token kind, Phase 2: syntax recognition only, no existence/resolution
# check yet (that's Phase 3+, `value_controller.py`, not this module).
# ---------------------------------------------------------------------------


def test_extract_value_tokens_recognizes_a_value_kind_token():
    assert extract_value_tokens("${value:tenant.c0062.meta.name}") == [("value", "tenant.c0062.meta.name")]


def test_find_malformed_value_tokens_accepts_a_well_formed_value_token():
    assert find_malformed_value_tokens("${value:tenant.c0062.meta.name}") == []


def test_validate_value_tokens_accepts_a_well_formed_value_token():
    """Doesn't raise — '${value:...}' is syntactically well-formed like any
    other kind, even though nothing resolves it yet."""
    validate_value_tokens("${value:tenant.c0062.meta.name}")


def test_extract_value_tokens_accepts_a_bare_single_segment_value_token():
    """Deliberately NOT a Phase 1 malformed-token error: the regex's `key`
    group doesn't parse `kind.name.path` internally, only Phase 3's resolver
    does — a missing `name`/`path` surfaces later as
    `value_reference_invalid_path`, not here."""
    assert extract_value_tokens("${value:onlyonesegment}") == [("value", "onlyonesegment")]
    assert find_malformed_value_tokens("${value:onlyonesegment}") == []


def test_resolve_value_tokens_raises_on_an_unresolved_value_token():
    """This low-level function has no special knowledge of `value:`'s own
    `(kind, name)`-lookup semantics — that resolution happens upstream, in
    `resolve_values()`/`value_references.py` (Phase 3/4, now implemented),
    which populates the `"kind.name.path"` entry in `values` *before*
    calling this function. Called directly with an empty `values` dict
    (bypassing that upstream resolution), a `${value:...}` token still
    raises the same "did not resolve" error an undeclared var/secret key
    would — this function's own raw contract is unaffected by Phase 3/4,
    only `resolve_values()`'s wrapping of it changed."""
    with pytest.raises(ValueError, match="did not resolve to a value"):
        resolve_value_tokens("${value:tenant.c0062.meta.name}", {})


def test_resolve_value_tokens_substitutes_a_value_token_once_its_key_is_populated():
    """The positive case completing the pair above — once a caller (in
    practice, `resolve_values()`'s Phase 4 merge) has populated the exact
    `"kind.name.path"` key, this function substitutes a `${value:...}`
    token exactly like any other kind — no special-casing needed, matching
    `kind` only ever mattering to the token's author."""
    result = resolve_value_tokens("code=${value:tenant.c0062.meta.name}", {"tenant.c0062.meta.name": "c0062"})
    assert result == "code=c0062"


def test_resolve_value_tokens_in_mapping_unescapes_nested_escaped_literals():
    data = {"env": {"SMTP_HOST": "$${GATUS_SMTP_HOST}"}}
    assert resolve_value_tokens_in_mapping(data, {}) == {"env": {"SMTP_HOST": "${GATUS_SMTP_HOST}"}}


# ---------------------------------------------------------------------------
# resolve_value_tokens_tracking_secrets() — Full Solution Phase 3
# (docs/design/value-token-resolution.md), the path-tracking sibling Helm
# (--set-string <path>=<value>) and Compose (env: kwarg) delivery need.
# ---------------------------------------------------------------------------


def test_tracking_secrets_resolves_non_secret_leaves_in_place():
    data = {"env": {"REGION": "${var:REGION}"}}
    resolved, secrets = resolve_value_tokens_tracking_secrets(data, {"REGION": "westeurope"})
    assert resolved == {"env": {"REGION": "westeurope"}}
    assert secrets == {}


def test_tracking_secrets_leaves_a_secret_shaped_leaf_unresolved_in_the_dict():
    data = {"env": {"DB_PASSWORD": "${secret:db_password}"}}
    resolved, secrets = resolve_value_tokens_tracking_secrets(data, {"db_password": "hunter2"})
    assert resolved == {"env": {"DB_PASSWORD": "${secret:db_password}"}}
    assert secrets == {"env.DB_PASSWORD": "hunter2"}


def test_tracking_secrets_matches_the_real_immich_style_nested_path():
    """Real gap #8 shape: controllers.main.containers.main.env.DB_PASSWORD."""
    data = {"controllers": {"main": {"containers": {"main": {"env": {"DB_PASSWORD": "${secret:db_password}"}}}}}}
    resolved, secrets = resolve_value_tokens_tracking_secrets(data, {"db_password": "hunter2"})
    assert resolved["controllers"]["main"]["containers"]["main"]["env"]["DB_PASSWORD"] == "${secret:db_password}"
    assert secrets == {"controllers.main.containers.main.env.DB_PASSWORD": "hunter2"}


def test_tracking_secrets_tracks_a_list_index_path():
    data = {"services": [{"env": {"DB_PASSWORD": "${secret:db_password}"}}]}
    resolved, secrets = resolve_value_tokens_tracking_secrets(data, {"db_password": "hunter2"})
    assert resolved["services"][0]["env"]["DB_PASSWORD"] == "${secret:db_password}"
    assert secrets == {"services.0.env.DB_PASSWORD": "hunter2"}


def test_tracking_secrets_a_mixed_var_and_secret_string_is_secret_shaped_as_a_whole():
    """A connection string mixing ${var:}/${secret:} is reported only in the
    secrets map, fully resolved — never partially rewritten into the dict."""
    data = {"env": {"DATABASE_URL": "postgres://${var:HOST}/db?password=${secret:db_password}"}}
    resolved, secrets = resolve_value_tokens_tracking_secrets(data, {"HOST": "db.internal", "db_password": "hunter2"})
    assert resolved["env"]["DATABASE_URL"] == "postgres://${var:HOST}/db?password=${secret:db_password}"
    assert secrets == {"env.DATABASE_URL": "postgres://db.internal/db?password=hunter2"}


def test_tracking_secrets_handles_an_escaped_literal_alongside_a_real_secret():
    data = {"env": {"MIXED": "$${TOKEN}-${secret:db_password}"}}
    resolved, secrets = resolve_value_tokens_tracking_secrets(data, {"db_password": "hunter2"})
    assert resolved["env"]["MIXED"] == "$${TOKEN}-${secret:db_password}"
    assert secrets == {"env.MIXED": "${TOKEN}-hunter2"}


def test_tracking_secrets_plain_literal_passes_through_unchanged():
    data = {"env": {"ENABLED": "true"}}
    resolved, secrets = resolve_value_tokens_tracking_secrets(data, {})
    assert resolved == data
    assert secrets == {}


# ---------------------------------------------------------------------------
# resolve_value_tokens_renaming_secrets() — Full Solution Phase 5
# (docs/design/value-token-resolution.md), Compose's bare `${KEY}`-rename
# delivery — unlike Phase 3's per-leaf tracking (Helm's --set-string), this
# rewrites per token, since Compose interpolates ${KEY} occurrences within a
# string using its own process environment, not a whole-path override.
# ---------------------------------------------------------------------------


def test_renaming_secrets_resolves_non_secret_leaves_in_place():
    data = {"environment": {"REGION": "${var:REGION}"}}
    resolved, secrets = resolve_value_tokens_renaming_secrets(data, {"REGION": "westeurope"})
    assert resolved == {"environment": {"REGION": "westeurope"}}
    assert secrets == {}


def test_renaming_secrets_rewrites_a_secret_token_to_bare_dollar_brace_key():
    data = {"environment": {"DB_PASSWORD": "${secret:db_password}"}}
    resolved, secrets = resolve_value_tokens_renaming_secrets(data, {"db_password": "hunter2"})
    assert resolved == {"environment": {"DB_PASSWORD": "${db_password}"}}
    assert secrets == {"db_password": "hunter2"}


def test_renaming_secrets_resolves_var_and_renames_secret_within_the_same_string():
    """Unlike Phase 3's whole-leaf secret-shaped rule, a mixed string resolves
    the var token to its literal value while only the secret token is renamed
    — both halves independently correct, matching Compose's own per-${KEY}
    interpolation (no whole-path override mechanism exists for Compose)."""
    data = {"environment": {"DATABASE_URL": "postgres://${var:HOST}/db?password=${secret:db_password}"}}
    resolved, secrets = resolve_value_tokens_renaming_secrets(data, {"HOST": "db.internal", "db_password": "hunter2"})
    assert resolved["environment"]["DATABASE_URL"] == "postgres://db.internal/db?password=${db_password}"
    assert secrets == {"db_password": "hunter2"}


def test_renaming_secrets_tracks_a_list_index_path():
    data = {"services": [{"environment": {"DB_PASSWORD": "${secret:db_password}"}}]}
    resolved, secrets = resolve_value_tokens_renaming_secrets(data, {"db_password": "hunter2"})
    assert resolved["services"][0]["environment"]["DB_PASSWORD"] == "${db_password}"
    assert secrets == {"db_password": "hunter2"}


def test_renaming_secrets_handles_an_escaped_literal_alongside_a_real_secret():
    data = {"environment": {"MIXED": "$${TOKEN}-${secret:db_password}"}}
    resolved, secrets = resolve_value_tokens_renaming_secrets(data, {"db_password": "hunter2"})
    assert resolved["environment"]["MIXED"] == "${TOKEN}-${db_password}"
    assert secrets == {"db_password": "hunter2"}


def test_renaming_secrets_two_leaves_sharing_one_secret_key_both_get_renamed():
    data = {"environment": {"A": "${secret:shared}", "B": "${secret:shared}"}}
    resolved, secrets = resolve_value_tokens_renaming_secrets(data, {"shared": "hunter2"})
    assert resolved == {"environment": {"A": "${shared}", "B": "${shared}"}}
    assert secrets == {"shared": "hunter2"}


def test_renaming_secrets_plain_literal_passes_through_unchanged():
    data = {"environment": {"ENABLED": "true"}}
    resolved, secrets = resolve_value_tokens_renaming_secrets(data, {})
    assert resolved == data
    assert secrets == {}


def test_renaming_secrets_raises_on_unresolvable_key():
    data = {"environment": {"DB_PASSWORD": "${secret:missing}"}}
    with pytest.raises(ValueError, match="did not resolve to a value"):
        resolve_value_tokens_renaming_secrets(data, {})
