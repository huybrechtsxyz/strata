"""Tests for EnvironmentRemoteOverrideModel and related environment model changes."""

import pytest
from pydantic import ValidationError

from strata.models.environment_model import (
    EnvironmentOverridesModel,
    EnvironmentRemoteOverrideModel,
)


class TestEnvironmentRemoteOverrideModel:
    def test_valid_remote_override(self):
        override = EnvironmentRemoteOverrideModel(remote="tf_landscape", reference="v1.2.3")
        assert str(override.remote) == "tf_landscape"
        assert override.reference == "v1.2.3"

    def test_reference_cannot_be_empty(self):
        with pytest.raises(ValidationError):
            EnvironmentRemoteOverrideModel(remote="tf_landscape", reference="")

    def test_reference_whitespace_stripped(self):
        override = EnvironmentRemoteOverrideModel(remote="tf_landscape", reference="  v2.0.0  ")
        assert override.reference == "v2.0.0"

    def test_remote_name_required(self):
        with pytest.raises(ValidationError):
            EnvironmentRemoteOverrideModel(reference="v1.0.0")

    def test_reference_accepts_branch_name(self):
        override = EnvironmentRemoteOverrideModel(remote="infra", reference="feature/my-branch")
        assert override.reference == "feature/my-branch"

    def test_reference_accepts_sha(self):
        sha = "abc1234abc1234abc1234abc1234abc1234abc123"
        override = EnvironmentRemoteOverrideModel(remote="infra", reference=sha)
        assert override.reference == sha


class TestEnvironmentOverridesModelRemotes:
    def test_overrides_accepts_remotes_list(self):
        data = {
            "remotes": [
                {"remote": "tf_landscape", "reference": "v1.0.0"},
                {"remote": "tf_modules", "reference": "v2.0.0"},
            ]
        }
        overrides = EnvironmentOverridesModel.model_validate(data)
        assert overrides.remotes is not None
        assert len(overrides.remotes) == 2

    def test_overrides_remotes_none_by_default(self):
        overrides = EnvironmentOverridesModel()
        assert overrides.remotes is None

    def test_duplicate_remote_names_rejected(self):
        data = {
            "remotes": [
                {"remote": "tf_landscape", "reference": "v1.0.0"},
                {"remote": "tf_landscape", "reference": "v2.0.0"},
            ]
        }
        with pytest.raises(ValidationError, match="Duplicate remote overrides"):
            EnvironmentOverridesModel.model_validate(data)

    def test_single_remote_override_accepted(self):
        data = {"remotes": [{"remote": "tf_landscape", "reference": "main"}]}
        overrides = EnvironmentOverridesModel.model_validate(data)
        assert overrides.remotes[0].reference == "main"

    def test_remotes_coexist_with_other_overrides(self):
        data = {
            "remotes": [{"remote": "tf_landscape", "reference": "v1.0.0"}],
            "properties": {"env": "dev"},
        }
        overrides = EnvironmentOverridesModel.model_validate(data)
        assert overrides.remotes is not None
        assert overrides.properties == {"env": "dev"}
