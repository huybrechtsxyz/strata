"""Tests for EnvironmentService remote override helpers and Phase 2 validation."""

from unittest.mock import MagicMock

from strata.models.environment_model import (
    EnvironmentModel,
    EnvironmentOverridesModel,
    EnvironmentRemoteOverrideModel,
    EnvironmentSpecModel,
)
from strata.services.environment_service import EnvironmentService


def _make_service_with_remotes(remote_overrides: list) -> EnvironmentService:
    """Build a pre-validated EnvironmentService with given remote overrides."""
    svc = EnvironmentService.__new__(EnvironmentService)
    # Minimal super().__init__ state
    svc._validated = True
    svc._errors = []
    svc._messages = []
    svc.logger = MagicMock()

    overrides = EnvironmentOverridesModel(
        remotes=[EnvironmentRemoteOverrideModel(**r) for r in remote_overrides] if remote_overrides else None
    )
    spec = EnvironmentSpecModel(overrides=overrides)

    # Build a minimal meta
    from strata.models.environment_model import EnvironmentMetaModel

    meta = EnvironmentMetaModel(name="test_env", labels=None)
    svc.model = EnvironmentModel(meta=meta, spec=spec)
    return svc


class TestGetOverriddenRemoteNames:
    def test_returns_empty_set_when_no_overrides(self):
        svc = _make_service_with_remotes([])
        assert svc.get_overridden_remote_names() == set()

    def test_returns_set_of_names(self):
        svc = _make_service_with_remotes(
            [
                {"remote": "tf_landscape", "reference": "v1.0.0"},
                {"remote": "tf_modules", "reference": "v2.0.0"},
            ]
        )
        assert svc.get_overridden_remote_names() == {"tf_landscape", "tf_modules"}

    def test_single_remote(self):
        svc = _make_service_with_remotes([{"remote": "infra", "reference": "main"}])
        assert svc.get_overridden_remote_names() == {"infra"}


class TestGetRemoteOverride:
    def test_returns_none_when_not_found(self):
        svc = _make_service_with_remotes([{"remote": "tf_landscape", "reference": "v1.0.0"}])
        assert svc.get_remote_override("unknown") is None

    def test_returns_override_when_found(self):
        svc = _make_service_with_remotes([{"remote": "tf_landscape", "reference": "v1.2.3"}])
        override = svc.get_remote_override("tf_landscape")
        assert override is not None
        assert override.reference == "v1.2.3"

    def test_returns_none_when_no_remotes_at_all(self):
        svc = _make_service_with_remotes([])
        assert svc.get_remote_override("tf_landscape") is None


class TestHasOverridesIncludesRemotes:
    def test_has_overrides_true_with_remotes(self):
        svc = _make_service_with_remotes([{"remote": "infra", "reference": "v1.0"}])
        assert svc.has_overrides() is True

    def test_has_overrides_false_when_no_remotes_and_no_other_overrides(self):
        svc = _make_service_with_remotes([])
        assert svc.has_overrides() is False


class TestValidateDynamicRemoteCrossCheck:
    def _make_config_model(self, remote_names: list):
        """Build a minimal ConfigurationModel with given remote names."""
        from strata.models.repository_model import RemoteModel

        config = MagicMock()
        config.spec.security = None  # no security block → skip security validation
        config.spec.remotes = [MagicMock(name=n, spec=RemoteModel) for n in remote_names]
        for i, name in enumerate(remote_names):
            config.spec.remotes[i].name = name
        return config

    def test_valid_remote_override_passes(self):
        from strata.services.environment_service import EnvironmentService

        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "env_dev", "labels": None},
            "spec": {"overrides": {"remotes": [{"remote": "tf_landscape", "reference": "v1.0.0"}]}},
        }
        svc = EnvironmentService(data=data)
        svc.validate()

        config_model = self._make_config_model(["tf_landscape"])
        ok, errors = svc._validate_dynamic(configuration_model=config_model)
        assert ok, errors

    def test_unknown_remote_override_fails(self):
        from strata.services.environment_service import EnvironmentService

        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "env_dev", "labels": None},
            "spec": {"overrides": {"remotes": [{"remote": "nonexistent_remote", "reference": "v1.0.0"}]}},
        }
        svc = EnvironmentService(data=data)
        svc.validate()

        config_model = self._make_config_model(["tf_landscape"])
        ok, errors = svc._validate_dynamic(configuration_model=config_model)
        assert not ok
        assert any("nonexistent_remote" in e for e in errors)

    def test_no_remotes_always_passes(self):
        from strata.services.environment_service import EnvironmentService

        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "env_dev", "labels": None},
            "spec": {},
        }
        svc = EnvironmentService(data=data)
        svc.validate()
        ok, errors = svc._validate_dynamic(configuration_model=self._make_config_model(["tf_landscape"]))
        assert ok
        assert errors == []
