"""Tests for VersionService pointer lock following (ADR-0011 Layer 4)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from strata.services.version_service import VersionService
from strata.models.version_manifest_model import VersionManifestModel
from strata.models.version_lock_model import VersionLockModel
from strata.exceptions import PlatformFileNotFoundError


_VERSION_FILE = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: v2-1-0
spec:
  ring: prd
  pins:
    images:
      app: v2.1.0
      worker: v2.1.0
"""

_POINTER_LOCK = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd
spec:
  ring: prd
  source: v2.1.0.yaml
"""

_INLINE_LOCK = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd
spec:
  ring: prd
  pins:
    - target: {type: image, name: app}
      version: v1.0.0
"""


class TestVersionServicePointerFollowing:
    """VersionService.load() transparently follows pointer locks."""

    def setup_method(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "v2.1.0.yaml").write_text(_VERSION_FILE)
        (self.td / "prd.lock.yaml").write_text(_POINTER_LOCK)
        (self.td / "prd-inline.lock.yaml").write_text(_INLINE_LOCK)

    def teardown_method(self):
        self._td.cleanup()

    def test_load_version_file_returns_manifest(self):
        m = VersionService.load(str(self.td / "v2.1.0.yaml"))
        assert isinstance(m, VersionManifestModel)
        assert m.spec.pins.images["app"] == "v2.1.0"

    def test_load_pointer_lock_returns_manifest(self):
        # Loading a pointer lock should transparently return the pointed-to manifest
        m = VersionService.load(str(self.td / "prd.lock.yaml"))
        assert isinstance(m, VersionManifestModel)
        assert m.spec.pins.images["app"] == "v2.1.0"

    def test_load_inline_lock_returns_lock(self):
        # Old-style (pins) lock is returned as VersionLockModel
        m = VersionService.load(str(self.td / "prd-inline.lock.yaml"))
        assert isinstance(m, VersionLockModel)
        assert m.spec.pins[0].version == "v1.0.0"

    def test_pointer_lock_missing_source_raises(self):
        bad_lock = self.td / "bad.lock.yaml"
        bad_lock.write_text("""\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd
spec:
  ring: prd
  source: nonexistent.yaml
""")
        with pytest.raises(PlatformFileNotFoundError, match="nonexistent.yaml"):
            VersionService.load(str(bad_lock))

    def test_resolve_pins_from_pointer_lock(self):
        m = VersionService.load(str(self.td / "prd.lock.yaml"))
        pins = VersionService.resolve_pins([m])
        from strata.models.version_lock_model import VersionPinTargetType
        assert pins[VersionPinTargetType.IMAGE]["app"] == "v2.1.0"
        assert pins[VersionPinTargetType.IMAGE]["worker"] == "v2.1.0"


_VERSION_FILE_LOCKED = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: v2-1-0
spec:
  ring: prd
  pins:
    images:
      app: v2.1.0
  hash: "sha256:aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabb"
"""

_POINTER_LOCK_WITH_HASH = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd
spec:
  ring: prd
  source: v2.1.0-locked.yaml
  hash: "sha256:aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabb"
  version: v2-1-0
"""

_POINTER_LOCK_WRONG_HASH = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd
spec:
  ring: prd
  source: v2.1.0-locked.yaml
  hash: "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  version: v2-1-0
"""


class TestVersionServiceHashVerification:
    """VersionService.load() verifies spec.hash when following a pointer lock."""

    def setup_method(self):
        self._td = __import__("tempfile").TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "v2.1.0-locked.yaml").write_text(_VERSION_FILE_LOCKED)

    def teardown_method(self):
        self._td.cleanup()

    def test_matching_hash_passes(self):
        """When lock's spec.hash matches version file's spec.hash, load succeeds."""
        lock_path = self.td / "prd.lock.yaml"
        lock_path.write_text(_POINTER_LOCK_WITH_HASH)
        m = VersionService.load(str(lock_path))
        assert isinstance(m, VersionManifestModel)

    def test_mismatched_hash_raises(self):
        """When lock's spec.hash differs from version file's spec.hash, load raises ValueError."""
        lock_path = self.td / "prd-wrong.lock.yaml"
        lock_path.write_text(_POINTER_LOCK_WRONG_HASH)
        with pytest.raises(ValueError, match="hash mismatch"):
            VersionService.load(str(lock_path))

    def test_no_hash_on_lock_skips_check(self):
        """Lock without spec.hash skips verification (works for unlocked version files)."""
        (self.td / "v2.1.0.yaml").write_text(_VERSION_FILE_LOCKED)
        lock_path = self.td / "prd-nohash.lock.yaml"
        lock_path.write_text("""\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd
spec:
  ring: prd
  source: v2.1.0.yaml
""")
        m = VersionService.load(str(lock_path))
        assert isinstance(m, VersionManifestModel)

    def test_version_file_without_hash_skips_check(self):
        """Version file that has no spec.hash cannot mismatch — check is skipped."""
        (self.td / "v2.1.0-unlocked.yaml").write_text(_VERSION_FILE)  # no hash
        lock_path = self.td / "prd-unverified.lock.yaml"
        lock_path.write_text("""\
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd
spec:
  ring: prd
  source: v2.1.0-unlocked.yaml
  hash: "sha256:aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabbccdd1234aabb"
""")
        # The version file has no spec.hash, so actual_hash is None → no mismatch raised
        m = VersionService.load(str(lock_path))
        assert isinstance(m, VersionManifestModel)
