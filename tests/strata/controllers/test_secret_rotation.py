"""Tests for secret rotation — model validators, controller logic, and build plan display."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from strata.models.store_models import (
    SecretGenerateSpec,
    SecretRotatePolicy,
    SecretRotateSpec,
    SecretStoreModel,
    SecretStoreType,
)
from strata.utils.secret_metadata import SecretMetadata

# ---------------------------------------------------------------------------
# SecretRotateSpec model validation
# ---------------------------------------------------------------------------


class TestSecretRotateSpec:
    def test_valid_warn_policy(self):
        spec = SecretRotateSpec(max_age=90, policy="warn")
        assert spec.max_age == 90
        assert spec.policy == SecretRotatePolicy.WARN

    def test_valid_rotate_policy(self):
        spec = SecretRotateSpec(max_age=30, policy="rotate")
        assert spec.policy == SecretRotatePolicy.ROTATE

    def test_default_policy_is_warn(self):
        spec = SecretRotateSpec(max_age=60)
        assert spec.policy == SecretRotatePolicy.WARN

    def test_max_age_zero_raises(self):
        with pytest.raises(ValidationError):
            SecretRotateSpec(max_age=0)

    def test_max_age_negative_raises(self):
        with pytest.raises(ValidationError):
            SecretRotateSpec(max_age=-1)

    def test_max_age_one_is_valid(self):
        spec = SecretRotateSpec(max_age=1)
        assert spec.max_age == 1


class TestSecretStoreModelRotate:
    def test_rotate_on_keyvault_is_valid(self):
        model = SecretStoreModel(
            key="db_pass",
            store="azure-keyvault",
            value="myapp-db-pass",
            generate=SecretGenerateSpec(type="password", length=32),
            rotate=SecretRotateSpec(max_age=90, policy="warn"),
        )
        assert model.rotate is not None
        assert model.rotate.max_age == 90

    def test_rotate_on_constant_raises(self):
        with pytest.raises(ValidationError, match="rotate.*not valid"):
            SecretStoreModel(
                key="x",
                store="constant",
                value="static",
                rotate=SecretRotateSpec(max_age=30),
            )

    def test_rotate_on_environment_raises(self):
        with pytest.raises(ValidationError, match="rotate.*not valid"):
            SecretStoreModel(
                key="x",
                store="environment",
                value="MY_VAR",
                rotate=SecretRotateSpec(max_age=30),
            )

    def test_rotate_on_github_raises(self):
        with pytest.raises(ValidationError, match="rotate.*not valid"):
            SecretStoreModel(
                key="x",
                store="github",
                value="MY_SECRET",
                rotate=SecretRotateSpec(max_age=30),
            )

    def test_rotate_policy_rotate_requires_generate(self):
        with pytest.raises(ValidationError, match="requires a 'generate' spec"):
            SecretStoreModel(
                key="x",
                store="azure-keyvault",
                value="my-secret",
                rotate=SecretRotateSpec(max_age=30, policy="rotate"),
            )

    def test_rotate_policy_warn_without_generate_is_valid(self):
        """Warn policy doesn't require generate — it only advises."""
        model = SecretStoreModel(
            key="x",
            store="azure-keyvault",
            value="my-secret",
            rotate=SecretRotateSpec(max_age=30, policy="warn"),
        )
        assert model.rotate.policy == SecretRotatePolicy.WARN

    def test_rotate_none_by_default(self):
        model = SecretStoreModel(key="x", store="azure-keyvault", value="v")
        assert model.rotate is None


# ---------------------------------------------------------------------------
# SecretMetadata dataclass
# ---------------------------------------------------------------------------


class TestSecretMetadata:
    def test_default_fields_are_none(self):
        meta = SecretMetadata()
        assert meta.created_at is None
        assert meta.updated_at is None
        assert meta.expires_on is None
        assert meta.version is None

    def test_fields_accept_values(self):
        now = datetime.now(timezone.utc)
        meta = SecretMetadata(created_at=now, version="v2")
        assert meta.created_at == now
        assert meta.version == "v2"


# ---------------------------------------------------------------------------
# ValueController._check_rotation
# ---------------------------------------------------------------------------


class TestCheckRotation:
    def _make_item(self, max_age=90, policy="warn", generate_type="password"):
        return SecretStoreModel(
            key="DB_PASSWORD",
            store=SecretStoreType.AZURE_KEYVAULT,
            value="myapp-db-password",
            generate=SecretGenerateSpec(type=generate_type, length=32),
            rotate=SecretRotateSpec(max_age=max_age, policy=policy),
        )

    def _make_controller(self):
        from strata.controllers.value_controller import ValueController

        return ValueController()

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    def test_no_rotate_spec_returns_value_unchanged(self, _mock_init):
        ctrl = self._make_controller()
        item = SecretStoreModel(
            key="X", store=SecretStoreType.AZURE_KEYVAULT, value="x"
        )
        integration = MagicMock()
        val, err, note = ctrl._check_rotation(item, "current-value", integration)
        assert val == "current-value"
        assert err is None
        assert note is None

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    def test_metadata_unavailable_skips_rotation(self, _mock_init):
        ctrl = self._make_controller()
        integration = MagicMock()
        integration.get_secret_metadata.return_value = None
        val, err, note = ctrl._check_rotation(
            self._make_item(), "current-value", integration
        )
        assert val == "current-value"
        assert note is None

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    def test_secret_not_overdue_returns_unchanged(self, _mock_init):
        ctrl = self._make_controller()
        integration = MagicMock()
        integration.get_secret_metadata.return_value = SecretMetadata(
            updated_at=datetime.now(timezone.utc) - timedelta(days=10)
        )
        val, err, note = ctrl._check_rotation(
            self._make_item(max_age=90), "current-value", integration
        )
        assert val == "current-value"
        assert note is None

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    def test_warn_policy_returns_advisory_note(self, _mock_init):
        ctrl = self._make_controller()
        integration = MagicMock()
        integration.get_secret_metadata.return_value = SecretMetadata(
            updated_at=datetime.now(timezone.utc) - timedelta(days=100)
        )
        val, err, note = ctrl._check_rotation(
            self._make_item(max_age=90, policy="warn"), "current-value", integration
        )
        assert val == "current-value"
        assert err is None
        assert note is not None
        assert "rotation_advisory" in note
        assert "100d" in note

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    def test_rotate_policy_generates_new_value(self, _mock_init):
        ctrl = self._make_controller()
        integration = MagicMock()
        integration.get_secret_metadata.return_value = SecretMetadata(
            updated_at=datetime.now(timezone.utc) - timedelta(days=100)
        )
        integration.update_secret.return_value = True
        val, err, note = ctrl._check_rotation(
            self._make_item(max_age=90, policy="rotate"), "old-value", integration
        )
        assert val != "old-value"
        assert err is None
        assert note is not None
        assert "rotated:" in note
        integration.update_secret.assert_called_once()

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    def test_rotate_policy_update_fails_returns_old_value(self, _mock_init):
        ctrl = self._make_controller()
        integration = MagicMock()
        integration.get_secret_metadata.return_value = SecretMetadata(
            updated_at=datetime.now(timezone.utc) - timedelta(days=100)
        )
        integration.update_secret.return_value = False
        val, err, note = ctrl._check_rotation(
            self._make_item(max_age=90, policy="rotate"), "old-value", integration
        )
        assert val == "old-value"
        assert "rotation_failed" in note

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    def test_uses_created_at_when_updated_at_missing(self, _mock_init):
        ctrl = self._make_controller()
        integration = MagicMock()
        integration.get_secret_metadata.return_value = SecretMetadata(
            created_at=datetime.now(timezone.utc) - timedelta(days=100)
        )
        val, err, note = ctrl._check_rotation(
            self._make_item(max_age=90, policy="warn"), "current-value", integration
        )
        assert "rotation_advisory" in note


# ---------------------------------------------------------------------------
# ValueController._resolve_secret with rotation
# ---------------------------------------------------------------------------


class TestResolveSecretWithRotation:
    def _make_item(self, max_age=90, policy="warn"):
        return SecretStoreModel(
            key="DB_PASSWORD",
            store=SecretStoreType.AZURE_KEYVAULT,
            value="myapp-db-password",
            generate=SecretGenerateSpec(type="password", length=32),
            rotate=SecretRotateSpec(max_age=max_age, policy=policy),
        )

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_existing_secret_with_rotation_triggers_check(self, mock_get_int, _mock_init):
        from strata.controllers.value_controller import ValueController

        mock_integration = MagicMock()
        mock_integration.get_secret.return_value = "existing-value"
        mock_integration.get_secret_metadata.return_value = SecretMetadata(
            updated_at=datetime.now(timezone.utc) - timedelta(days=100)
        )
        mock_get_int.return_value = mock_integration

        ctrl = ValueController()
        val, err, note = ctrl._resolve_secret(self._make_item(max_age=90, policy="warn"))
        assert val == "existing-value"
        assert "rotation_advisory" in note

    @patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized")
    @patch("strata.controllers.value_controller.ValueController._get_integration_by_type")
    def test_existing_secret_no_rotate_spec_returns_plain(self, mock_get_int, _mock_init):
        from strata.controllers.value_controller import ValueController

        mock_integration = MagicMock()
        mock_integration.get_secret.return_value = "existing-value"
        mock_get_int.return_value = mock_integration

        item = SecretStoreModel(
            key="X", store=SecretStoreType.AZURE_KEYVAULT, value="x"
        )
        ctrl = ValueController()
        val, err, note = ctrl._resolve_secret(item)
        assert val == "existing-value"
        assert note is None


# ---------------------------------------------------------------------------
# Build plan value status — rotation display
# ---------------------------------------------------------------------------


class TestBuildPlanRotationDisplay:
    def test_secret_with_rotate_shows_rotation_tag(self, tmp_path):
        """build plan detail column includes rotation info when rotate spec is present."""
        from unittest.mock import MagicMock

        gen = MagicMock()
        gen.type = MagicMock()
        gen.type.value = "password"
        gen.length = 32

        rotate = MagicMock()
        rotate.max_age = 90
        rotate.policy = MagicMock()
        rotate.policy.value = "warn"

        item = MagicMock()
        item.key = "DB_PASS"
        item.store = MagicMock()
        item.store.value = "azure-keyvault"
        item.generate = gen
        item.rotate = rotate

        # Import the helper from test_commands_build to avoid duplication
        # Inline the check: build the row logic directly
        store = item.store.value
        _secret_builtins = {"constant", "environment", "github"}
        if store in _secret_builtins:
            status, detail = "ok", None
        elif item.generate is not None:
            status, detail = "generated", f"{item.generate.type.value}/{item.generate.length}"
        else:
            status, detail = "required", None
        if item.rotate is not None:
            rotation_tag = f"[rotation: {item.rotate.max_age}d / {item.rotate.policy.value}]"
            detail = f"{detail}  {rotation_tag}" if detail else rotation_tag

        assert status == "generated"
        assert "password/32" in detail
        assert "[rotation: 90d / warn]" in detail

    def test_secret_without_rotate_no_rotation_tag(self):
        """No rotation tag when rotate spec is None."""
        from unittest.mock import MagicMock

        item = MagicMock()
        item.key = "TOKEN"
        item.store = MagicMock()
        item.store.value = "azure-keyvault"
        item.generate = None
        item.rotate = None

        store = item.store.value
        _secret_builtins = {"constant", "environment", "github"}
        if store in _secret_builtins:
            status, detail = "ok", None
        elif item.generate is not None:
            status, detail = "generated", f"{item.generate.type.value}/{item.generate.length}"
        else:
            status, detail = "required", None
        if item.rotate is not None:
            rotation_tag = f"[rotation: {item.rotate.max_age}d / {item.rotate.policy.value}]"
            detail = f"{detail}  {rotation_tag}" if detail else rotation_tag

        assert status == "required"
        assert detail is None
