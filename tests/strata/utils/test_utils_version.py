#!/usr/bin/env python3
"""Tests for version resolution and the single-source-of-truth rule."""

from importlib.metadata import version as metadata_version
from pathlib import Path

import pytest

from strata.utils.version import (
    PACKAGE_NAME,
    UNKNOWN_VERSION,
    get_distribution_name,
    get_version,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSION_FILE = REPO_ROOT / "VERSION.txt"
SRC_ROOT = REPO_ROOT / "src" / "strata"


@pytest.fixture(autouse=True)
def _clear_version_cache():
    """get_version is cached; monkeypatching it needs a clean slate."""
    get_version.cache_clear()
    yield
    get_version.cache_clear()


# ---------------------------------------------------------------------------
# VERSION.txt is the source
# ---------------------------------------------------------------------------


def test_version_file_exists_and_is_not_empty():
    """The single source must actually exist."""
    assert VERSION_FILE.is_file()
    assert VERSION_FILE.read_text(encoding="utf-8").strip()


def test_version_file_holds_a_pep440_version():
    """An unparseable version breaks the build at package time, not here."""
    from packaging.version import InvalidVersion, Version

    raw = VERSION_FILE.read_text(encoding="utf-8").strip()
    try:
        Version(raw)
    except InvalidVersion:  # pragma: no cover - only on a bad edit
        pytest.fail(f"VERSION.txt holds a non-PEP440 version: {raw!r}")


def test_installed_version_matches_the_version_file():
    """Metadata is built from VERSION.txt, so the two must agree.

    A mismatch means the package was not reinstalled after VERSION.txt
    changed — the sharp edge documented in `strata.utils.version`.
    """
    from packaging.version import Version

    declared = Version(VERSION_FILE.read_text(encoding="utf-8").strip())
    assert Version(get_version()) == declared


# ---------------------------------------------------------------------------
# No second copy anywhere
# ---------------------------------------------------------------------------


def test_strata_version_is_not_duplicated_in_source():
    """Strata's own version must appear nowhere in code, only in VERSION.txt.

    Deliberately matches the *actual* declared version rather than any
    version-shaped string: chart versions, tool versions and IP addresses in
    docstrings are unrelated and must not trip this guard.
    """
    from packaging.version import Version

    raw = VERSION_FILE.read_text(encoding="utf-8").strip()
    candidates = {raw, str(Version(raw))}

    offenders = []
    for path in SRC_ROOT.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for candidate in candidates:
            if candidate in content:
                offenders.append(f"{path.relative_to(SRC_ROOT)} contains {candidate!r}")
    assert not offenders, f"The version must come from VERSION.txt, found: {offenders}"


def test_package_exposes_a_derived_version():
    """`strata.__version__` resolves rather than declaring."""
    import strata

    assert strata.__version__ == get_version()


def test_package_version_is_lazy():
    """Importing strata must not pay for an environment-wide metadata scan."""
    source = (SRC_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "__getattr__" in source
    assert "__version__ =" not in source


def test_package_rejects_unknown_attributes():
    """The lazy hook must not swallow genuine typos."""
    import strata

    missing = "does_not_exist"
    with pytest.raises(AttributeError):
        getattr(strata, missing)


# ---------------------------------------------------------------------------
# Distribution resolution
# ---------------------------------------------------------------------------


def test_distribution_is_resolved_from_the_import_package():
    """The permanent name is 'strata'; the distribution name is looked up."""
    assert PACKAGE_NAME == "strata"
    assert get_distribution_name() is not None


def test_temporary_distribution_name_is_not_hardcoded():
    """'strata-v2' is a parallel-development artifact, not the system's name."""
    body = "".join(
        line
        for line in (SRC_ROOT / "utils" / "version.py").read_text(encoding="utf-8").splitlines(True)
        if not line.lstrip().startswith("#")
    )
    assert '"strata-v2"' not in body
    assert "'strata-v2'" not in body


def test_version_survives_a_distribution_rename(monkeypatch):
    """Renaming the distribution must not require a code change."""
    monkeypatch.setattr(
        "strata.utils.version.packages_distributions",
        lambda: {PACKAGE_NAME: ["strata"]},
    )
    monkeypatch.setattr(
        "strata.utils.version.metadata_version",
        lambda name: "9.9.9" if name == "strata" else pytest.fail(f"asked for {name}"),
    )
    assert get_version() == "9.9.9"


def test_version_comes_from_package_metadata():
    """Metadata, not a literal, is what the runtime reports."""
    distribution = get_distribution_name()
    assert distribution is not None
    assert get_version() == metadata_version(distribution)


def test_missing_package_reports_unknown_rather_than_guessing(monkeypatch):
    """Running from a source checkout must not invent a version number."""
    monkeypatch.setattr("strata.utils.version.packages_distributions", dict)
    assert get_version() == UNKNOWN_VERSION
