"""Tests for SecretStoreModel and SecretStoreType — github store behavior."""

import pytest
from pydantic import ValidationError

from strata.models.store_models import SecretStoreModel, SecretStoreType


class TestSecretStoreTypeGithub:
    def test_github_is_valid_enum_value(self):
        """store: github is a recognized SecretStoreType value."""
        assert SecretStoreType("github") == SecretStoreType.GITHUB

    def test_github_model_validates_without_error(self):
        """SecretStoreModel with store='github' constructs without raising."""
        model = SecretStoreModel(key="api_key", store="github", value="MY_API_KEY")
        assert model.store == SecretStoreType.GITHUB

    def test_github_with_version_raises_validation_error(self):
        """version is not allowed for store='github'; model_validator raises ValueError."""
        with pytest.raises(ValidationError) as exc_info:
            SecretStoreModel(key="api_key", store="github", value="MY_API_KEY", version="1")
        assert "not supported" in str(exc_info.value)

    def test_github_serializes_as_github_not_environment(self):
        """model_dump() preserves 'github', never coerced to 'environment'."""
        model = SecretStoreModel(key="api_key", store="github", value="MY_API_KEY")
        dumped = model.model_dump()
        assert dumped["store"] == "github"
