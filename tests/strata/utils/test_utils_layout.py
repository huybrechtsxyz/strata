#!/usr/bin/env python3
"""Tests for the filesystem layout module, plus the convention it enforces."""

import re
from pathlib import Path

import pytest

from strata.utils.layout import (
    DEFAULT_IGNORED_DIRS,
    MANIFEST_FILENAME,
    REMOTES_DIRNAME,
    STRATA_DIR,
    YAML_SUFFIXES,
    display_path,
    manifest_path,
    remote_checkout_path,
    remotes_dir,
    strata_dir,
)

SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "strata"
LAYOUT_FILE = SRC_ROOT / "utils" / "layout.py"


# ---------------------------------------------------------------------------
# Path construction
# ---------------------------------------------------------------------------


def test_manifest_path_is_root_relative():
    """The manifest sits directly at the solution root."""
    assert manifest_path(Path("/solution")) == Path("/solution") / MANIFEST_FILENAME


def test_strata_dir_is_under_the_root():
    """Runtime state is solution-local, never global."""
    assert strata_dir(Path("/solution")).name == STRATA_DIR


def test_remotes_dir_lives_inside_the_runtime_directory():
    """Checkouts are runtime state, so they inherit its exclusion from discovery."""
    result = remotes_dir(Path("/solution"))
    assert result.parent.name == STRATA_DIR
    assert result.name == REMOTES_DIRNAME


def test_remote_checkout_is_keyed_by_reference():
    """Two refs of one remote must not share a directory (ADR-0019)."""
    a = remote_checkout_path(Path("/solution"), "infra", "v1.0.0")
    b = remote_checkout_path(Path("/solution"), "infra", "v2.0.0")
    assert a != b
    assert a.name == "v1.0.0"
    assert b.name == "v2.0.0"


def test_remote_checkouts_are_separated_by_name():
    """Two remotes at the same ref stay apart."""
    a = remote_checkout_path(Path("/s"), "infra", "v1.0.0")
    b = remote_checkout_path(Path("/s"), "charts", "v1.0.0")
    assert a != b


def test_unpinned_remote_gets_its_own_segment():
    """A ref-less remote still lands one level deep, so depth is uniform."""
    result = remote_checkout_path(Path("/s"), "charts", None)
    assert result.name == "_unpinned"
    assert result.parent.name == "charts"


def test_unpinned_cannot_collide_with_a_real_ref_directory():
    """A remote is either pinned or not; the sentinel occupies the ref slot."""
    sentinel = remote_checkout_path(Path("/s"), "infra", None)
    literal = remote_checkout_path(Path("/s"), "infra", "_unpinned")
    assert sentinel == literal  # same slot — one remote cannot be both


def test_layout_functions_do_not_touch_the_filesystem(tmp_path):
    """Paths are computed for things that do not exist yet."""
    result = remote_checkout_path(tmp_path, "infra", "v1.0.0")
    assert not result.exists()


# ---------------------------------------------------------------------------
# Display paths
# ---------------------------------------------------------------------------


def test_display_path_is_relative_to_the_root(tmp_path):
    """Absolute paths dominate the line and differ between laptop and CI."""
    source = tmp_path / "config" / "workspaces" / "main.yaml"
    assert display_path(str(source), tmp_path) == "config/workspaces/main.yaml"


def test_display_path_outside_the_root_is_left_alone(tmp_path):
    """A path that cannot be made relative is shown as-is, not crashed on."""
    assert display_path("/elsewhere/a.yaml", tmp_path) == "/elsewhere/a.yaml"


def test_display_path_without_a_root_is_unchanged():
    """Rendering works even when no solution root is known."""
    assert display_path("a.yaml", None) == "a.yaml"


def test_display_path_of_none_is_empty():
    """A finding about the run, not a document."""
    assert display_path(None, None) == ""


# ---------------------------------------------------------------------------
# The coupling this module exists to remove
# ---------------------------------------------------------------------------


def test_runtime_directory_is_always_ignored_by_discovery():
    """Checkouts live under STRATA_DIR, so the walk must never descend into it.

    Derived rather than repeated: this is the v1 `deploy_path` failure shape,
    where two places owned one value and drifted.
    """
    assert STRATA_DIR in DEFAULT_IGNORED_DIRS


def test_manifest_suffix_is_discoverable():
    """The manifest must be a file the walk would otherwise consider."""
    assert Path(MANIFEST_FILENAME).suffix in YAML_SUFFIXES


# ---------------------------------------------------------------------------
# Convention guard
# ---------------------------------------------------------------------------

#: Literals that name a location on disk. Outside `layout.py` these must be
#: imported, not retyped — a second copy is how the value drifts.
_OWNED_LITERALS = (
    re.compile(r'"strata\.yaml"'),
    re.compile(r'"\.strata"'),
)


def _source_files_outside_layout() -> list[Path]:
    return [p for p in SRC_ROOT.rglob("*.py") if p != LAYOUT_FILE]


@pytest.mark.parametrize("pattern", _OWNED_LITERALS, ids=lambda p: p.pattern)
def test_path_literals_are_declared_only_in_layout(pattern):
    """No module outside layout.py may hardcode a path literal it owns."""
    offenders = [
        f"{path.relative_to(SRC_ROOT)}"
        for path in _source_files_outside_layout()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"{pattern.pattern} is owned by strata/utils/layout.py but is hardcoded in: "
        f"{offenders}. Import the constant instead."
    )


def test_layout_itself_declares_those_literals():
    """Guard against the guard passing because the literals moved elsewhere."""
    content = LAYOUT_FILE.read_text(encoding="utf-8")
    for pattern in _OWNED_LITERALS:
        assert pattern.search(content), f"{pattern.pattern} is no longer declared in layout.py"
