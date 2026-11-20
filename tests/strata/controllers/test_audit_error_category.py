#!/usr/bin/env python3
"""Tests for `error_category()` (docs/design/audit-trail.md, "Why a run failed")."""

import pytest

from strata.controllers.audit_error_category import error_category
from strata.models.audit_manifest_model import ManifestPolicyResultModel
from strata.models.audit_metrics_model import ErrorCategory
from strata.utils.diagnostics import Diagnostics


def _failed(message: str = "Step 'infra': deploy failed — exit code 1", code: str | None = None) -> Diagnostics:
    diagnostics = Diagnostics()
    diagnostics.error(message, code=code)
    return diagnostics


def _policy(enforcement: str, passed: bool) -> ManifestPolicyResultModel:
    return ManifestPolicyResultModel(
        policy_name="image_tag_policy",
        policy_type="image_tag_policy",
        phase="deploy",
        enforcement=enforcement,
        passed=passed,
    )


def test_a_run_without_errors_has_no_category():
    assert error_category(Diagnostics(), [_policy("deny", False)]) is None


def test_a_denying_policy_that_did_not_pass_is_a_policy_failure():
    assert error_category(_failed(), [_policy("deny", False)]) is ErrorCategory.POLICY


@pytest.mark.parametrize(("enforcement", "passed"), [("warn", False), ("deny", True)])
def test_a_policy_that_did_not_stop_the_run_is_not_the_cause(enforcement, passed):
    assert error_category(_failed(), [_policy(enforcement, passed)]) is ErrorCategory.UNKNOWN


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("integration_credentials_unresolved", ErrorCategory.AUTH),
        ("helm_registry_auth_failed", ErrorCategory.AUTH),
        ("helm_registry_credentials_invalid", ErrorCategory.AUTH),
    ],
)
def test_an_error_code_names_its_category(code, category):
    assert error_category(_failed("problem", code=code), None) is category


@pytest.mark.parametrize(
    "code", ["integration_unreachable", "integration_credentials_rejected", "helm_release_conflict"]
)
def test_codes_a_deploy_never_raises_are_not_mapped(code):
    # `strata integration test` and `validate` raise these; a deploy does not, so a mapping would be dead.
    assert error_category(_failed("problem", code=code), None) is ErrorCategory.UNKNOWN


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("Step 'infra': apply failed — timed out and was killed", ErrorCategory.TIMEOUT),
        ("Step 'infra': plan failed — exit code 1: Error: Error acquiring the state lock", ErrorCategory.STATE_LOCK),
    ],
)
def test_a_known_marker_in_the_error_names_its_category(message, category):
    assert error_category(_failed(message), None) is category


def test_a_code_wins_over_a_marker_in_a_later_error():
    diagnostics = _failed("Step 'a': timed out and was killed")
    diagnostics.error("registry refused", code="helm_registry_auth_failed")
    assert error_category(diagnostics, None) is ErrorCategory.AUTH


def test_anything_else_is_unknown_never_a_guess():
    assert error_category(_failed("Step 'infra': deploy failed — quota exceeded"), None) is ErrorCategory.UNKNOWN
