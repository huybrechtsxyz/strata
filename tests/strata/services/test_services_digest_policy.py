"""Tests for F-2: artifact digest policy (require_digests + --verify-digests).

Covers:
  - ProgressionRingModel.require_digests field
  - VersionService.validate_sha_format
  - DeploymentService.check_digest_policy
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

from strata.models.promotion_model import ProgressionRingModel
from strata.models.version_lock_model import VersionPinTargetType
from strata.services.version_service import VersionService

_API_VERSION = "strata.huybrechts.xyz/v1"


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_deployment_service(versions: Optional[List[Dict[str, str]]] = None):
    """Return a minimally configured DeploymentService-like object for testing.

    We import and instantiate the real service class here but bypass disk I/O by
    patching internal helpers.  The *versions* list takes dicts like
    ``[{"file": "lock.yaml"}]``.
    """
    from strata.services.deployment_service import DeploymentService

    svc = DeploymentService.__new__(DeploymentService)
    # Wire the minimal state that check_digest_policy inspects
    svc._repo_map = {}
    svc._workspace_service = None

    if versions is not None:
        from strata.models.deployment_model import DeploymentVersionRef

        version_refs = [DeploymentVersionRef(file=v["file"]) for v in versions]
    else:
        version_refs = None

    # Build a minimal DeploymentModel mock
    spec_mock = MagicMock()
    spec_mock.versions = version_refs
    spec_mock.environments = []
    model_mock = MagicMock()
    model_mock.spec = spec_mock
    svc.model = model_mock

    return svc


from strata.models.version_lock_model import VersionLockModel as _VersionLockModel


def _make_lock_model(pins: list) -> MagicMock:
    """Return a MagicMock that isinstance-checks as VersionLockModel."""
    m = MagicMock()
    m.__class__ = _VersionLockModel
    m.spec.pins = pins
    return m


def _make_lock_pin(
    name: str,
    target_type: VersionPinTargetType,
    resolved_sha: Optional[str] = None,
):
    """Return a mock VersionPinModel-like object."""
    pin = MagicMock()
    pin.target.name = name
    pin.target.type = target_type
    pin.resolved_sha = resolved_sha
    return pin


# ─── ProgressionRingModel.require_digests ────────────────────────────────────


class TestProgressionRingModelRequireDigests:
    """F-2: ProgressionRingModel schema accepts the require_digests field."""

    def test_field_absent_defaults_to_none(self):
        ring = ProgressionRingModel(name="prd", environments=["prd"])
        assert ring.require_digests is None

    def test_field_set_to_true(self):
        ring = ProgressionRingModel(name="prd", environments=["prd"], require_digests=True)
        assert ring.require_digests is True

    def test_field_set_to_false(self):
        ring = ProgressionRingModel(name="prd", environments=["prd"], require_digests=False)
        assert ring.require_digests is False

    def test_coexists_with_require_lock(self):
        ring = ProgressionRingModel(
            name="prd",
            environments=["prd"],
            require_lock=True,
            require_digests=True,
        )
        assert ring.require_lock is True
        assert ring.require_digests is True


# ─── VersionService.validate_sha_format ──────────────────────────────────────


class TestVersionServiceValidateShaFormat:
    """F-2: SHA format validation rules per pin target type."""

    # ── remote (git SHA) ──────────────────────────────────────────────────────

    def test_remote_valid_full_sha(self):
        sha = "a" * 40
        assert VersionService.validate_sha_format(VersionPinTargetType.REMOTE, sha) is True

    def test_remote_valid_abbreviated_sha(self):
        assert VersionService.validate_sha_format(VersionPinTargetType.REMOTE, "abc1234") is True

    def test_remote_valid_mixed_case(self):
        # case-insensitive
        assert VersionService.validate_sha_format(VersionPinTargetType.REMOTE, "ABC1234") is True

    def test_remote_invalid_too_short(self):
        assert VersionService.validate_sha_format(VersionPinTargetType.REMOTE, "abc12") is False

    def test_remote_invalid_non_hex(self):
        assert VersionService.validate_sha_format(VersionPinTargetType.REMOTE, "xyz12345") is False

    def test_remote_invalid_semver(self):
        # Tags / semver strings must not pass
        assert VersionService.validate_sha_format(VersionPinTargetType.REMOTE, "v1.2.3") is False

    def test_remote_valid_boundary_7_chars(self):
        assert VersionService.validate_sha_format(VersionPinTargetType.REMOTE, "1234567") is True

    def test_remote_invalid_41_chars(self):
        assert VersionService.validate_sha_format(VersionPinTargetType.REMOTE, "a" * 41) is False

    # ── image (OCI digest) ────────────────────────────────────────────────────

    def test_image_valid_oci_digest(self):
        sha = "sha256:" + "a" * 64
        assert VersionService.validate_sha_format(VersionPinTargetType.IMAGE, sha) is True

    def test_image_invalid_missing_prefix(self):
        sha = "a" * 64
        assert VersionService.validate_sha_format(VersionPinTargetType.IMAGE, sha) is False

    def test_image_invalid_short_hex(self):
        sha = "sha256:" + "a" * 32
        assert VersionService.validate_sha_format(VersionPinTargetType.IMAGE, sha) is False

    def test_image_invalid_tag(self):
        assert VersionService.validate_sha_format(VersionPinTargetType.IMAGE, "latest") is False

    # ── helm_chart (OCI digest, same rules as image) ──────────────────────────

    def test_helm_chart_valid_oci_digest(self):
        sha = "sha256:" + "b" * 64
        assert VersionService.validate_sha_format(VersionPinTargetType.HELM_CHART, sha) is True

    def test_helm_chart_invalid_tag(self):
        assert VersionService.validate_sha_format(VersionPinTargetType.HELM_CHART, "1.2.3") is False

    # ── tool (any non-empty string accepted) ─────────────────────────────────

    def test_tool_accepts_semver(self):
        assert VersionService.validate_sha_format(VersionPinTargetType.TOOL, "1.2.3") is True

    def test_tool_accepts_arbitrary_string(self):
        assert VersionService.validate_sha_format(VersionPinTargetType.TOOL, "some-version") is True

    def test_tool_rejects_empty_string(self):
        # empty string is falsy — validate_sha_format returns bool(sha) for TOOL
        assert VersionService.validate_sha_format(VersionPinTargetType.TOOL, "") is False


# ─── DeploymentService.check_digest_policy ───────────────────────────────────


class TestCheckDigestPolicy:
    """F-2: DeploymentService.check_digest_policy edge cases."""

    def _svc_no_versions(self):
        svc = _make_deployment_service(versions=None)
        svc.model.spec.versions = None
        return svc

    # ── early-exit guards ─────────────────────────────────────────────────────

    def test_no_model_returns_empty(self):
        from strata.services.deployment_service import DeploymentService

        svc = DeploymentService.__new__(DeploymentService)
        svc.model = None
        svc._repo_map = {}
        errors, warnings = svc.check_digest_policy("/tmp", None)
        assert errors == []
        assert warnings == []

    def test_no_versions_returns_empty(self):
        svc = self._svc_no_versions()
        errors, warnings = svc.check_digest_policy("/tmp", None)
        assert errors == []
        assert warnings == []

    def test_no_ring_require_digests_no_verify_flag_returns_empty(self):
        """When neither policy is active the method short-circuits."""

        svc = _make_deployment_service(
            versions=[
                {
                    "file": "lock.yaml",
                }
            ]
        )
        svc.model.spec.environments = []  # no environments → ring_name stays None
        svc._resolve_file_path = MagicMock(return_value="/nonexistent/lock.yaml")

        errors, warnings = svc.check_digest_policy("/tmp", None, verify_digests=False)
        assert errors == []
        assert warnings == []

    # ── ring policy (require_digests: true) ───────────────────────────────────

    def test_ring_require_digests_pin_missing_sha_is_error(self):
        """A pin without resolved_sha under require_digests: true → error."""

        svc = _make_deployment_service(
            versions=[
                {
                    "file": "lock.yaml",
                }
            ]
        )
        svc._resolve_file_path = MagicMock(return_value="/fake/lock.yaml")

        # Fake environment with ring name
        env_mock = MagicMock()
        env_mock.model.spec.promotion.ring = "prd"

        # Fake config with require_digests: true on ring "prd"
        ring_mock = MagicMock()
        ring_mock.name = "prd"
        ring_mock.require_digests = True
        prog_mock = MagicMock()
        prog_mock.rings = [ring_mock]
        config_mock = MagicMock()
        config_mock.spec.promotions.progressions = [prog_mock]
        config_mock.get_remote_map.return_value = {}

        # Fake lock model with one pin missing resolved_sha
        pin = _make_lock_pin("my-remote", VersionPinTargetType.REMOTE, resolved_sha=None)
        lock_mock = _make_lock_model([pin])

        with (
            patch("strata.services.deployment_service.EnvironmentService.load", return_value=env_mock),
            patch.object(Path, "exists", return_value=True),
            patch("strata.services.version_service.VersionService.load", return_value=lock_mock),
        ):
            svc.model.spec.environments = [MagicMock(file="env.yaml")]
            errors, warnings = svc.check_digest_policy("/tmp", config_mock, verify_digests=False)

        assert len(errors) == 1
        assert "prd" in errors[0]
        assert "my-remote" in errors[0]
        assert warnings == []

    def test_ring_require_digests_pin_has_sha_no_error(self):
        """A pin WITH a valid resolved_sha satisfies require_digests: true."""

        svc = _make_deployment_service(
            versions=[
                {
                    "file": "lock.yaml",
                }
            ]
        )
        svc._resolve_file_path = MagicMock(return_value="/fake/lock.yaml")

        env_mock = MagicMock()
        env_mock.model.spec.promotion.ring = "prd"

        ring_mock = MagicMock()
        ring_mock.name = "prd"
        ring_mock.require_digests = True
        prog_mock = MagicMock()
        prog_mock.rings = [ring_mock]
        config_mock = MagicMock()
        config_mock.spec.promotions.progressions = [prog_mock]
        config_mock.get_remote_map.return_value = {}

        pin = _make_lock_pin("my-remote", VersionPinTargetType.REMOTE, resolved_sha="abc1234")
        lock_mock = _make_lock_model([pin])

        with (
            patch("strata.services.deployment_service.EnvironmentService.load", return_value=env_mock),
            patch.object(Path, "exists", return_value=True),
            patch("strata.services.version_service.VersionService.load", return_value=lock_mock),
        ):
            svc.model.spec.environments = [MagicMock(file="env.yaml")]
            errors, warnings = svc.check_digest_policy("/tmp", config_mock, verify_digests=False)

        assert errors == []
        assert warnings == []

    # ── format check (--verify-digests) ──────────────────────────────────────

    def test_verify_digests_bad_format_emits_warning(self):
        """verify_digests=True + invalid format SHA → warning, no error."""

        svc = _make_deployment_service(
            versions=[
                {
                    "file": "lock.yaml",
                }
            ]
        )
        svc._resolve_file_path = MagicMock(return_value="/fake/lock.yaml")
        svc.model.spec.environments = []  # no ring → no require_digests

        config_mock = MagicMock()
        config_mock.spec.promotions.progressions = []
        config_mock.get_remote_map.return_value = {}

        pin = _make_lock_pin("my-remote", VersionPinTargetType.REMOTE, resolved_sha="v1.2.3")
        lock_mock = _make_lock_model([pin])

        with patch("strata.services.version_service.VersionService.load", return_value=lock_mock):
            errors, warnings = svc.check_digest_policy("/tmp", config_mock, verify_digests=True)

        assert errors == []
        assert len(warnings) == 1
        assert "my-remote" in warnings[0]

    def test_verify_digests_good_format_no_warning(self):
        """verify_digests=True + valid git SHA → no warning."""

        svc = _make_deployment_service(
            versions=[
                {
                    "file": "lock.yaml",
                }
            ]
        )
        svc._resolve_file_path = MagicMock(return_value="/fake/lock.yaml")
        svc.model.spec.environments = []

        config_mock = MagicMock()
        config_mock.spec.promotions.progressions = []
        config_mock.get_remote_map.return_value = {}

        valid_sha = "a" * 40
        pin = _make_lock_pin("my-remote", VersionPinTargetType.REMOTE, resolved_sha=valid_sha)
        lock_mock = _make_lock_model([pin])

        with patch("strata.services.version_service.VersionService.load", return_value=lock_mock):
            errors, warnings = svc.check_digest_policy("/tmp", config_mock, verify_digests=True)

        assert errors == []
        assert warnings == []

    def test_verify_digests_no_sha_no_warning(self):
        """verify_digests=True + pin has no resolved_sha (and no ring policy) → silent."""

        svc = _make_deployment_service(
            versions=[
                {
                    "file": "lock.yaml",
                }
            ]
        )
        svc._resolve_file_path = MagicMock(return_value="/fake/lock.yaml")
        svc.model.spec.environments = []

        config_mock = MagicMock()
        config_mock.spec.promotions.progressions = []
        config_mock.get_remote_map.return_value = {}

        pin = _make_lock_pin("my-remote", VersionPinTargetType.REMOTE, resolved_sha=None)
        lock_mock = _make_lock_model([pin])

        with patch("strata.services.version_service.VersionService.load", return_value=lock_mock):
            errors, warnings = svc.check_digest_policy("/tmp", config_mock, verify_digests=True)

        assert errors == []
        assert warnings == []
