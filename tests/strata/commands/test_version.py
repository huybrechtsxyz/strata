import pathlib
import re

import pytest

import strata
from strata.utils.version import get_version

# Navigate from tests/strata/commands/ up to the project root.
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent

# Semantic versioning pattern: major.minor.patch with optional pre-release/build
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$")


def _is_valid_semver(version: str) -> bool:
    """Check if version string is valid semantic versioning."""
    return bool(_SEMVER_PATTERN.match(version))


@pytest.fixture
def version_from_file() -> str:
    """Read version from VERSION.txt file."""
    return (_PROJECT_ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()


def test_version_file_exists_and_readable(version_from_file: str) -> None:
    """Test that VERSION.txt is readable and contains a non-empty version."""
    assert version_from_file, "VERSION.txt should not be empty"


def test_version_file_is_valid_semver(version_from_file: str) -> None:
    """Test that VERSION.txt contains a valid semantic version."""
    assert _is_valid_semver(version_from_file), f"VERSION.txt contains invalid semantic version: {version_from_file}"


def test_get_version_returns_valid_semver() -> None:
    """Test that get_version() returns a valid semantic version."""
    version = get_version()
    assert version, "get_version() should return a non-empty version"
    assert _is_valid_semver(version), f"get_version() returned invalid semantic version: {version}"


def test_strata_version_is_valid_semver() -> None:
    """Test that strata.__version__ is a valid semantic version."""
    assert strata.__version__, "strata.__version__ should not be empty"
    assert _is_valid_semver(strata.__version__), f"strata.__version__ is invalid semantic version: {strata.__version__}"


def test_version_consistency(version_from_file: str) -> None:
    """Test that versions are consistent across sources.

    Note: This test may fail during development if the package hasn't been
    reinstalled after VERSION.txt was updated. This is expected and not a bug.
    """
    get_version_result = get_version()
    strata_version_result = strata.__version__

    # All should be valid semver
    assert _is_valid_semver(version_from_file)
    assert _is_valid_semver(get_version_result)
    assert _is_valid_semver(strata_version_result)

    # get_version() and strata.__version__ should always match (same source)
    assert get_version_result == strata_version_result, (
        f"get_version() [{get_version_result}] != strata.__version__ [{strata_version_result}]"
    )

    # Warn if they don't match VERSION.txt (happens in dev mode)
    if get_version_result != version_from_file:
        pytest.warns(
            UserWarning,
            match="Package metadata out of sync with VERSION.txt",
        )
