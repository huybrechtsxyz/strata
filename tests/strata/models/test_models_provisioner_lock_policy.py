#!/usr/bin/env python3
"""Tests for the provisioner lock policy model (docs/design/provisioner-lock-policy.md)."""

from datetime import date

import pytest
from pydantic import ValidationError

from strata.models.provisioner_lock_policy_model import ProvisionerLockAllowEntryModel, ProvisionerLockPolicyModel
from strata.models.workspace_model import WorkspaceModel


def _workspace(**policy) -> dict:
    return {
        "meta": {"name": "myapp-workspace"},
        "spec": {
            "providers": ["azure-main"],
            "provisioners": [{"name": "infra", "tool": "terraform", "source": {"source_path": "terraform/main"}}],
            "provisioner_lock_policy": policy,
        },
    }


def test_empty_policy_is_valid_and_denies_by_default():
    policy = ProvisionerLockPolicyModel.model_validate({})
    assert (policy.enforcement, policy.rings, policy.allow) == ("deny", None, None)


def test_policy_accepts_every_field():
    policy = ProvisionerLockPolicyModel.model_validate(
        {
            "enforcement": "warn",
            "rings": ["acc", "prd"],
            "allow": [{"provisioner": "infra", "reason": "vendored root", "expires": "2026-12-31"}],
        }
    )
    assert policy.enforcement == "warn"
    assert policy.allow is not None and policy.allow[0].expires == "2026-12-31"


@pytest.mark.parametrize(
    "overrides",
    [
        {"enforcement": "block"},
        {"on_missing_data": "skip"},
        {"rings": []},
        {"rings": [""]},
        {"allow": [{"provisioner": "infra"}]},
        {"allow": [{"reason": "vendored"}]},
        {"allow": [{"provisioner": "", "reason": "vendored"}]},
        {"allow": [{"provisioner": "infra", "reason": ""}]},
        {"allow": [{"provisioner": "infra", "reason": "vendored", "expires": "soon"}]},
        {"allow": [{"name": "infra", "reason": "vendored"}]},
        {"allow": [{"provisioner": "infra", "reason": "a"}, {"provisioner": "infra", "reason": "b"}]},
        {"unknown_field": True},
    ],
)
def test_policy_rejects(overrides):
    with pytest.raises(ValidationError):
        ProvisionerLockPolicyModel.model_validate(overrides)


def test_duplicate_allow_entries_are_named_in_the_error():
    with pytest.raises(ValidationError, match="infra"):
        ProvisionerLockPolicyModel.model_validate(
            {"allow": [{"provisioner": "infra", "reason": "a"}, {"provisioner": "infra", "reason": "b"}]}
        )


def test_an_unquoted_yaml_date_is_accepted_as_expires():
    entry = ProvisionerLockAllowEntryModel.model_validate(
        {"provisioner": "infra", "reason": "x", "expires": date(2026, 12, 31)}
    )
    assert entry.expires == "2026-12-31"


def test_workspace_policy_is_optional():
    data = _workspace()
    del data["spec"]["provisioner_lock_policy"]
    assert WorkspaceModel.model_validate(data).spec.provisioner_lock_policy is None


def test_workspace_accepts_an_empty_policy():
    assert WorkspaceModel.model_validate(_workspace()).spec.provisioner_lock_policy == ProvisionerLockPolicyModel()


def test_an_allow_entry_must_name_a_declared_provisioner():
    allow = [{"provisioner": "infra", "reason": "ok"}, {"provisioner": "infar", "reason": "typo"}]
    with pytest.raises(ValidationError, match="'infar' is not a declared provisioner"):
        WorkspaceModel.model_validate(_workspace(allow=allow))
