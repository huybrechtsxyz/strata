"""Unit tests for LockFactory."""

import pytest

from strata.integrations.lock.lock_azurerm import AzurermLockBackend
from strata.integrations.lock.lock_consul import ConsulLockBackend
from strata.integrations.lock.lock_factory import LockFactory
from strata.integrations.lock.lock_local import LocalLockBackend
from strata.integrations.lock.lock_tfc import TfcLockBackend
from strata.models.workspace_model import WorkspaceIacBackendModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend_model(backend_type: str, config: dict | None = None) -> WorkspaceIacBackendModel:
    return WorkspaceIacBackendModel(
        type=backend_type,
        configuration=config or {"key": "value"},
    )


# ---------------------------------------------------------------------------
# None / local
# ---------------------------------------------------------------------------


class TestLockFactoryLocal:
    def test_none_model_returns_local(self, tmp_path):
        backend = LockFactory.create(None, tmp_path)
        assert isinstance(backend, LocalLockBackend)

    def test_explicit_local_type_returns_local(self, tmp_path):
        model = _make_backend_model("local")
        backend = LockFactory.create(model, tmp_path)
        assert isinstance(backend, LocalLockBackend)

    def test_unknown_type_falls_back_to_local(self, tmp_path):
        model = _make_backend_model("some_future_backend")
        backend = LockFactory.create(model, tmp_path)
        assert isinstance(backend, LocalLockBackend)

    def test_work_path_passed_to_local_backend(self, tmp_path):
        backend = LockFactory.create(None, tmp_path)
        assert isinstance(backend, LocalLockBackend)
        assert backend._locks_dir == tmp_path / ".strata" / "locks"


# ---------------------------------------------------------------------------
# Phase 2 remote backends — now implemented
# ---------------------------------------------------------------------------


class TestLockFactoryRemotePhase2:
    def test_azurerm_returns_azurerm_backend(self, tmp_path):
        model = _make_backend_model(
            "azurerm",
            {"storage_account_name": "mysa", "container_name": "tfstate"},
        )
        backend = LockFactory.create(model, tmp_path)
        assert isinstance(backend, AzurermLockBackend)

    def test_azurerm_passes_configuration(self, tmp_path):
        model = _make_backend_model(
            "azurerm",
            {"storage_account_name": "mysa", "container_name": "tfstate"},
        )
        backend = LockFactory.create(model, tmp_path)
        assert isinstance(backend, AzurermLockBackend)
        assert backend._configuration["storage_account_name"] == "mysa"

    def test_terraform_cloud_returns_tfc_backend(self, tmp_path):
        model = _make_backend_model(
            "terraform_cloud",
            {"organization": "myorg", "workspaces": {"name": "myws"}},
        )
        backend = LockFactory.create(model, tmp_path)
        assert isinstance(backend, TfcLockBackend)

    def test_remote_alias_returns_tfc_backend(self, tmp_path):
        model = _make_backend_model(
            "remote",
            {"organization": "myorg", "workspace": "myws"},
        )
        backend = LockFactory.create(model, tmp_path)
        assert isinstance(backend, TfcLockBackend)

    def test_consul_returns_consul_backend(self, tmp_path):
        model = _make_backend_model(
            "consul",
            {"address": "http://localhost:8500"},
        )
        backend = LockFactory.create(model, tmp_path)
        assert isinstance(backend, ConsulLockBackend)

    def test_consul_passes_work_path(self, tmp_path):
        model = _make_backend_model("consul", {"address": "http://localhost:8500"})
        backend = LockFactory.create(model, tmp_path)
        assert isinstance(backend, ConsulLockBackend)
        assert backend._locks_dir == tmp_path / ".strata" / "locks"


# ---------------------------------------------------------------------------
# Phase 3 remote backends — still NotImplementedError
# ---------------------------------------------------------------------------


class TestLockFactoryRemoteNotImplemented:
    @pytest.mark.parametrize("backend_type", ["s3", "gcs"])
    def test_phase3_type_raises_not_implemented(self, tmp_path, backend_type):
        model = _make_backend_model(backend_type)
        with pytest.raises(NotImplementedError) as exc_info:
            LockFactory.create(model, tmp_path)
        assert "Phase 3" in str(exc_info.value)

    def test_s3_error_mentions_backend_name(self, tmp_path):
        model = _make_backend_model("s3")
        with pytest.raises(NotImplementedError, match="S3LockBackend"):
            LockFactory.create(model, tmp_path)

    def test_gcs_error_mentions_backend_name(self, tmp_path):
        model = _make_backend_model("gcs")
        with pytest.raises(NotImplementedError, match="GcsLockBackend"):
            LockFactory.create(model, tmp_path)
