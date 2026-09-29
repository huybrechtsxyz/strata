#!/usr/bin/env python3
"""Tests for EnvironmentService — declared keys and Phase 2 token resolution."""

from strata.models.configuration_model import ConfigurationSecurityModel
from strata.models.dns_model import DnsModel
from strata.models.network_model import NetworkModel
from strata.services.environment_service import EnvironmentService


def _environment() -> EnvironmentService:
    service = EnvironmentService(
        data={
            "meta": {"name": "prd"},
            "spec": {
                "variables": [{"key": "PUBLIC_IP", "store": "constant", "value": "1.2.3.4"}],
                "secrets": [{"key": "DB_PASSWORD", "store": "constant", "value": "x"}],
                "features": [{"key": "ENABLE_X", "store": "constant", "value": "true"}],
            },
        }
    )
    service.validate()
    return service


def _dns(value: str) -> DnsModel:
    return DnsModel.model_validate(
        {
            "meta": {"name": "public-dns"},
            "spec": {
                "zones": [
                    {
                        "name": "example.com",
                        "records": [{"name": "@", "type": "A", "value": value}],
                        "default_tags": {"environment": "test"},
                    }
                ]
            },
        }
    )


def test_environment_service_validates_from_data():
    """An EnvironmentService constructed from an in-memory dict validates."""
    service = _environment()
    assert service.model is not None
    assert service.model.meta.name == "prd"


def test_declared_keys_are_grouped_by_token_kind():
    """declared_keys() is keyed by token kind, matching parsed tokens."""
    assert _environment().declared_keys() == {
        "var": {"PUBLIC_IP"},
        "secret": {"DB_PASSWORD"},
        "feature": {"ENABLE_X"},
    }


def test_document_with_resolvable_token_passes():
    """A token whose key the environment declares is accepted."""
    result = _environment().validate_document_tokens(_dns("${var:PUBLIC_IP}"))
    assert result.ok
    assert result.messages() == []


def test_document_with_unknown_key_is_rejected_with_path_and_suggestions():
    """An undeclared key is reported with where it was found and what exists."""
    result = _environment().validate_document_tokens(_dns("${var:GHOST_IP}"))
    assert not result.ok
    assert len(result.errors) == 1
    message = result.messages()[0]
    assert "GHOST_IP" in message
    assert "PUBLIC_IP" in message
    assert "records" in message


def test_token_kind_is_checked_against_the_right_store():
    """A key declared as a variable does not satisfy a '${secret:}' token."""
    result = _environment().validate_document_tokens(_dns("${secret:PUBLIC_IP}"))
    assert not result.ok
    assert "spec.secrets" in result.messages()[0]


def test_output_token_is_rejected_cleanly_not_a_crash():
    """A '${output:}' token has no declared-keys set (gap #11, docs/_gap_v1.md) —
    must produce a clean diagnostic, not an unhandled KeyError."""
    result = _environment().validate_document_tokens(_dns("${output:provision-hearth.public_ip}"))
    assert not result.ok
    message = result.messages()[0]
    assert "output" in message
    assert "not supported" in message


def test_value_token_is_skipped_entirely_not_judged_here():
    """docs/design/cross-document-value-references.md's 5th kind,
    `${value:...}` — Phase 5 added a `kind == "value"` skip branch to
    `unresolved_value_tokens()` specifically because this function has no
    way to judge it at all: existence/path/scalar/literal correctness is a
    `DocumentIndex` lookup (`semantic_checks.py`'s `_check_value_references()`),
    never an Environment-declared-keys question. So a `${value:...}` token
    passes *this* function unconditionally, even one that would fail
    `_check_value_references()` (e.g. an unknown target) — this function
    genuinely has no opinion on it either way, unlike `${output:}` above,
    which this function does still judge (with an explicit
    'not supported' rejection by default)."""
    result = _environment().validate_document_tokens(_dns("${value:tenant.c0062.meta.name}"))
    assert result.ok, result.messages()

    result = _environment().validate_document_tokens(_dns("${value:tenant.doesnotexist.meta.name}"))
    assert result.ok, result.messages()


def test_output_token_is_accepted_when_output_claimed_is_true():
    """docs/design/deploy-command.md's "Cross-invocation output access"
    fix: a document some execution step's `targets` claims (gap #12,
    generalized to namespaces/modules) must no longer be rejected —
    `unresolved_value_tokens()`'s `output_claimed` param, called directly
    since `validate_document_tokens()`'s wrapper has no claiming context of
    its own (semantic_checks.py's `_check_deployment_value_tokens()` is the
    real caller that computes and passes this)."""
    from strata.services.environment_service import unresolved_value_tokens

    declared = {"var": {"PUBLIC_IP"}, "secret": {"DB_PASSWORD"}, "feature": {"ENABLE_X"}}
    result = unresolved_value_tokens(_dns("${output:provision-hearth.public_ip}"), declared, "prd", output_claimed=True)
    assert result.ok, result.messages()


def test_output_token_default_still_rejects_when_output_claimed_omitted():
    """`output_claimed` defaults to False — every pre-existing caller
    (e.g. `validate_document_tokens()`) is unaffected by this change."""
    from strata.services.environment_service import unresolved_value_tokens

    declared = {"var": {"PUBLIC_IP"}, "secret": {"DB_PASSWORD"}, "feature": {"ENABLE_X"}}
    result = unresolved_value_tokens(_dns("${output:provision-hearth.public_ip}"), declared, "prd")
    assert not result.ok
    assert "not supported" in result.messages()[0]


def test_literal_document_without_tokens_passes():
    """A document with no tokens has nothing to resolve."""
    result = _environment().validate_document_tokens(_dns("1.2.3.4"))
    assert result.ok
    assert result.messages() == []


def test_tokens_are_found_in_deeply_nested_documents():
    """The walk is generic — it reaches tokens at any depth, in any kind."""
    network = NetworkModel.model_validate(
        {
            "meta": {"name": "core"},
            "spec": {
                "networks": [
                    {
                        "name": "vpc-main",
                        "address_space": ["10.0.0.0/16"],
                        "subnets": [{"name": "web", "cidr": "${var:MISSING_CIDR}"}],
                        "default_tags": {"environment": "test"},
                    }
                ]
            },
        }
    )
    result = _environment().validate_document_tokens(network)
    assert not result.ok
    message = result.messages()[0]
    assert "MISSING_CIDR" in message
    assert "subnets" in message


def test_multiple_tokens_in_one_string_are_each_checked():
    """A composite string reports one error per unresolved token."""
    result = _environment().validate_document_tokens(_dns("${var:PUBLIC_IP}-${var:NOPE}-${secret:ALSO_NOPE}"))
    assert not result.ok
    assert len(result.errors) == 2


# ---------------------------------------------------------------------------
# Generalized Phase 1 (malformed-syntax) check in configuration/custom
# passthrough fields — docs/design/value-token-resolution.md's "Full
# Solution" section, docs/_gap_v1.md gap #8. `DnsZoneModel.configuration`
# has no field_validator of its own (dict[str, Any], real .v2-haven shape),
# unlike DnsRecordModel.value.
# ---------------------------------------------------------------------------


def _dns_with_zone_configuration(configuration: dict) -> DnsModel:
    return DnsModel.model_validate(
        {
            "meta": {"name": "public-dns"},
            "spec": {
                "zones": [
                    {
                        "name": "example.com",
                        "configuration": configuration,
                        "default_tags": {"environment": "test"},
                    }
                ]
            },
        }
    )


def test_bare_token_in_configuration_passthrough_is_now_caught():
    """The real gap #8 case: 'configuration' has no field_validator, so a bare
    '${IMMICH_DB_PASSWORD}' (missing 'kind:') was previously invisible to
    both Phase 1 and Phase 2. Now caught by the generalized walk."""
    result = _environment().validate_document_tokens(
        _dns_with_zone_configuration({"env": {"DB_PASSWORD": "${IMMICH_DB_PASSWORD}"}})
    )
    assert not result.ok
    message = result.messages()[0]
    assert "Malformed Value token" in message
    assert "IMMICH_DB_PASSWORD" in message


def test_escaped_token_in_configuration_passthrough_validates_clean():
    """Gatus's real, documented case: a chart's own native '${TOKEN}'
    placeholder, escaped as '$${TOKEN}', must not be flagged."""
    result = _environment().validate_document_tokens(
        _dns_with_zone_configuration({"env": {"SMTP_HOST": "$${GATUS_SMTP_HOST}"}})
    )
    assert result.ok
    assert result.messages() == []


def test_real_token_in_configuration_passthrough_is_cross_checked_as_before():
    """A well-formed '${secret:KEY}' in configuration still goes through the
    normal Phase 2 declared-key check, unaffected by the new Phase 1 pass."""
    result = _environment().validate_document_tokens(
        _dns_with_zone_configuration({"env": {"DB_PASSWORD": "${secret:GHOST_PASSWORD}"}})
    )
    assert not result.ok
    assert "is not declared" in result.messages()[0]


# ---------------------------------------------------------------------------
# merge_environment_models
# ---------------------------------------------------------------------------


def _model(**overrides):
    from strata.models.environment_model import EnvironmentModel

    spec = {
        "variables": [{"key": "REGION", "store": "constant", "value": "westeurope"}],
        "secrets": [{"key": "DB_PASSWORD", "store": "constant", "value": "base"}],
        "features": [{"key": "NEW_UI", "store": "constant", "value": "true"}],
    }
    spec.update(overrides)
    return EnvironmentModel.model_validate({"meta": {"name": "e"}, "spec": spec})


def test_merge_of_one_environment_returns_its_own_stores():
    from strata.services.environment_service import merge_environment_models

    variables, secrets, features = merge_environment_models([_model()])
    assert variables["REGION"].value == "westeurope"
    assert secrets["DB_PASSWORD"].value == "base"
    assert features["NEW_UI"].value == "true"


def test_later_environment_overrides_earlier_on_key_collision():
    from strata.services.environment_service import merge_environment_models

    base = _model()
    override = _model(secrets=[{"key": "DB_PASSWORD", "store": "constant", "value": "override"}])
    _, secrets, _ = merge_environment_models([base, override])
    assert secrets["DB_PASSWORD"].value == "override"


def test_keys_unique_to_either_environment_both_survive_the_merge():
    from strata.services.environment_service import merge_environment_models

    base = _model()
    extra = _model(variables=[{"key": "EXTRA", "store": "constant", "value": "1"}])
    variables, _, _ = merge_environment_models([base, extra])
    assert set(variables) == {"REGION", "EXTRA"}


# ---------------------------------------------------------------------------
# validate_artifact_references() (docs/design/artifact-references.md's
# full-review finding — store: artifact's value had no cross-reference check)
# ---------------------------------------------------------------------------


def _environment_with_artifact_variable() -> EnvironmentService:
    service = EnvironmentService(
        data={
            "meta": {"name": "prd"},
            "spec": {
                "variables": [
                    {"key": "IMAGE_TAG", "store": "artifact", "value": "dspapi_container", "field": "image_tag"}
                ],
            },
        }
    )
    service.validate()
    return service


def test_artifact_reference_to_a_real_artifact_passes():
    result = _environment_with_artifact_variable().validate_artifact_references({"dspapi_container"})
    assert result.ok
    assert result.messages() == []


def test_artifact_reference_to_an_unknown_artifact_is_rejected():
    result = _environment_with_artifact_variable().validate_artifact_references({"some-other-artifact"})
    assert not result.ok
    assert "dspapi_container" in result.messages()[0]
    assert "undefined_artifact" in [d.code for d in result.errors]


def test_non_artifact_variables_are_never_checked():
    """A constant/environment-store variable's value means something else
    entirely — never mistaken for an artifact reference."""
    result = _environment().validate_artifact_references(set())
    assert result.ok


def test_no_artifacts_declared_anywhere_still_reports_the_reference():
    result = _environment_with_artifact_variable().validate_artifact_references(set())
    assert not result.ok


# ---------------------------------------------------------------------------
# validate_allowed_stores() (docs/_gap_v1.md gap #7 — Configuration's
# spec.security allow-lists, real usage: cfg-int-deployment's
# config/stores.yaml)
# ---------------------------------------------------------------------------


def _security(**overrides) -> ConfigurationSecurityModel:
    return ConfigurationSecurityModel.model_validate(overrides)


def test_store_in_the_allow_list_passes():
    result = _environment().validate_allowed_stores(_security(allowed_variable_stores=["constant"]))
    assert result.ok
    assert result.messages() == []


def test_store_not_in_the_allow_list_is_rejected():
    result = _environment().validate_allowed_stores(_security(allowed_variable_stores=["azure-appconfig"]))
    assert not result.ok
    message = result.messages()[0]
    assert "PUBLIC_IP" in message
    assert "constant" in message
    assert "disallowed_store" in [d.code for d in result.errors]


def test_a_builtin_store_type_is_not_implicitly_exempt():
    """v1's own real usage deliberately omits 'environment' from
    allowed_variable_stores platform-wide — builtins get no free pass."""
    result = _environment().validate_allowed_stores(_security(allowed_variable_stores=[]))
    assert not result.ok
    assert "constant" in result.messages()[0]


def test_each_store_kind_is_checked_independently():
    """An unset field on ConfigurationSecurityModel means that store kind is
    unrestricted, even when the other two are restricted."""
    result = _environment().validate_allowed_stores(
        _security(allowed_secret_stores=["azure-keyvault"], allowed_feature_stores=["azure-appconfig"])
    )
    assert not result.ok
    codes = [d.code for d in result.errors]
    assert codes.count("disallowed_store") == 2  # secrets + features; variables unrestricted


def test_no_fields_set_on_security_means_fully_unrestricted():
    result = _environment().validate_allowed_stores(_security())
    assert result.ok
    assert result.messages() == []
