#!/usr/bin/env python3
"""Tests for SolutionController — root discovery, indexing and resolution."""

from pathlib import Path

import pytest

from strata.controllers.solution_controller import (
    DocumentRef,
    SolutionController,
    find_solution_root,
)
from strata.models.common_models import PlatformKind

MANIFEST = """\
apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
  configuration: configuration.yaml
"""

CONFIGURATION = """\
apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: test-config
spec:
  providers: [azure]
"""

TOPOLOGY = """\
apiVersion: strata.huybrechts.xyz/v2
kind: topology
meta:
  name: main-topology
spec:
  type: kubernetes
  components:
    - resource: web-vm
"""


def _solution(tmp_path: Path) -> Path:
    """A minimal valid solution tree."""
    (tmp_path / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (tmp_path / "configuration.yaml").write_text(CONFIGURATION, encoding="utf-8")
    nested = tmp_path / "deep" / "nested"
    nested.mkdir(parents=True)
    (nested / "topology.yaml").write_text(TOPOLOGY, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Root discovery
# ---------------------------------------------------------------------------


def test_find_solution_root_walks_up_from_nested_directory(tmp_path):
    """The root is found from any depth below it, like go.mod/Cargo.toml."""
    root = _solution(tmp_path)
    assert find_solution_root(root / "deep" / "nested") == root.resolve()


def test_find_solution_root_returns_none_outside_a_solution(tmp_path):
    """A directory with no strata.yaml above it is not inside a solution."""
    assert find_solution_root(tmp_path) is None


def test_from_directory_returns_none_outside_a_solution(tmp_path):
    """The convenience constructor mirrors find_solution_root."""
    assert SolutionController.from_directory(tmp_path) is None


# ---------------------------------------------------------------------------
# Discovery and indexing
# ---------------------------------------------------------------------------


def test_load_indexes_documents_regardless_of_folder_layout(tmp_path):
    """Kind comes from the document's own field — folders carry no meaning."""
    controller = SolutionController(_solution(tmp_path))
    is_valid, errors = controller.load()
    assert is_valid, errors
    assert controller.solution is not None
    assert controller.solution.meta.name == "test-solution"
    assert controller.index.names_of(PlatformKind.CONFIGURATION) == {"test-config"}
    assert controller.index.names_of(PlatformKind.TOPOLOGY) == {"main-topology"}


def test_load_reports_missing_manifest(tmp_path):
    """A root without strata.yaml is an error, not a crash."""
    is_valid, errors = SolutionController(tmp_path).load()
    assert not is_valid
    assert any("strata.yaml" in e for e in errors)


def test_load_skips_non_strata_yaml_silently(tmp_path):
    """Helm values, CI pipelines and k8s manifests must not break discovery."""
    root = _solution(tmp_path)
    (root / "docker-compose.yaml").write_text("services:\n  web:\n    image: nginx\n", encoding="utf-8")
    (root / "ci.yml").write_text("stages:\n  - build\n", encoding="utf-8")

    is_valid, errors = SolutionController(root).load()
    assert is_valid, errors


def test_load_errors_on_strata_document_with_unknown_kind(tmp_path):
    """A strata apiVersion with a bogus kind is a typo, not a foreign file."""
    root = _solution(tmp_path)
    (root / "typo.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: frobnicator\nmeta:\n  name: x\n", encoding="utf-8"
    )

    is_valid, errors = SolutionController(root).load()
    assert not is_valid
    assert any("unknown kind 'frobnicator'" in e for e in errors)


def test_load_indexes_multi_document_files(tmp_path):
    """A single file may hold several documents separated by '---'."""
    root = _solution(tmp_path)
    second = TOPOLOGY.replace("main-topology", "other-topology")
    (root / "both.yaml").write_text(TOPOLOGY.replace("main-topology", "a-topology") + "---\n" + second, encoding="utf-8")

    controller = SolutionController(root)
    is_valid, errors = controller.load()
    assert is_valid, errors
    assert controller.index.names_of(PlatformKind.TOPOLOGY) == {"a-topology", "other-topology", "main-topology"}


def test_load_rejects_duplicate_identity_naming_both_files(tmp_path):
    """Duplicate (kind, name) is a hard error — and must say where both are."""
    root = _solution(tmp_path)
    (root / "copy.yaml").write_text(TOPOLOGY, encoding="utf-8")

    is_valid, errors = SolutionController(root).load()
    assert not is_valid
    duplicate = [e for e in errors if "Duplicate" in e]
    assert duplicate
    assert "copy.yaml" in duplicate[0]
    assert "topology.yaml" in duplicate[0]


def test_load_stops_at_a_nested_solution_boundary(tmp_path):
    """A nested strata.yaml marks a different solution — do not index into it."""
    root = _solution(tmp_path)
    vendor = root / "vendor" / "other"
    vendor.mkdir(parents=True)
    (vendor / "strata.yaml").write_text(MANIFEST.replace("test-solution", "other-solution"), encoding="utf-8")
    (vendor / "leak.yaml").write_text(TOPOLOGY.replace("main-topology", "should-not-appear"), encoding="utf-8")

    controller = SolutionController(root)
    is_valid, errors = controller.load()
    assert is_valid, errors
    assert "should-not-appear" not in controller.index.names_of(PlatformKind.TOPOLOGY)


def test_load_skips_ignored_directories(tmp_path):
    """Tool caches and runtime state are never descended into."""
    root = _solution(tmp_path)
    for ignored in (".git", "build", ".strata"):
        directory = root / ignored
        directory.mkdir()
        (directory / "stray.yaml").write_text(TOPOLOGY.replace("main-topology", f"in-{ignored}"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    names = controller.index.names_of(PlatformKind.TOPOLOGY)
    assert names == {"main-topology"}


# ---------------------------------------------------------------------------
# spec.discovery.exclude
# ---------------------------------------------------------------------------


def _solution_excluding(tmp_path: Path, *patterns: str) -> Path:
    """A solution whose manifest declares extra exclude patterns."""
    root = _solution(tmp_path)
    rendered = "\n".join(f"      - {p!r}" for p in patterns)
    (root / "strata.yaml").write_text(
        MANIFEST + f"  discovery:\n    exclude:\n{rendered}\n", encoding="utf-8"
    )
    return root


def test_exclude_skips_a_matching_directory(tmp_path):
    """Scaffolding directories can be excluded from discovery."""
    root = _solution_excluding(tmp_path, "templates/**")
    templates = root / "templates"
    templates.mkdir()
    (templates / "scaffold.yaml").write_text(TOPOLOGY.replace("main-topology", "scaffolded"), encoding="utf-8")

    controller = SolutionController(root)
    is_valid, errors = controller.load()
    assert is_valid, errors
    assert controller.index.names_of(PlatformKind.TOPOLOGY) == {"main-topology"}


def test_exclude_matches_the_bare_directory_name_too(tmp_path):
    """'templates' works as well as 'templates/**'."""
    root = _solution_excluding(tmp_path, "templates")
    templates = root / "templates"
    templates.mkdir()
    (templates / "scaffold.yaml").write_text(TOPOLOGY.replace("main-topology", "scaffolded"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    assert controller.index.names_of(PlatformKind.TOPOLOGY) == {"main-topology"}


def test_exclude_skips_an_individual_file(tmp_path):
    """Patterns match files, not just directories."""
    root = _solution_excluding(tmp_path, "*.generated.yaml")
    (root / "thing.generated.yaml").write_text(TOPOLOGY.replace("main-topology", "generated"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    assert controller.index.names_of(PlatformKind.TOPOLOGY) == {"main-topology"}


def test_exclude_cannot_re_enable_built_in_ignores(tmp_path):
    """User patterns are additive — they never shrink the built-in floor."""
    root = _solution_excluding(tmp_path, "nothing-matching/**")
    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "stray.yaml").write_text(TOPOLOGY.replace("main-topology", "in-git"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    assert "in-git" not in controller.index.names_of(PlatformKind.TOPOLOGY)


def test_no_discovery_block_means_no_extra_exclusions(tmp_path):
    """spec.discovery is optional; omitting it changes nothing."""
    root = _solution(tmp_path)
    extra = root / "templates"
    extra.mkdir()
    (extra / "scaffold.yaml").write_text(TOPOLOGY.replace("main-topology", "scaffolded"), encoding="utf-8")

    controller = SolutionController(root)
    controller.load()
    assert controller.index.names_of(PlatformKind.TOPOLOGY) == {"main-topology", "scaffolded"}


def test_load_rejects_nested_solution_document_in_the_tree(tmp_path):
    """Only the root manifest may be a 'solution' document."""
    root = _solution(tmp_path)
    (root / "second-solution.yaml").write_text(MANIFEST.replace("test-solution", "extra"), encoding="utf-8")

    is_valid, errors = SolutionController(root).load()
    assert not is_valid
    assert any("nested 'solution' document" in e for e in errors)


def test_load_reports_invalid_document_with_its_path(tmp_path):
    """Schema errors carry the file they came from — provenance replaces `file:`."""
    root = _solution(tmp_path)
    (root / "bad.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: topology\nmeta:\n  name: broken\nspec:\n  type: kubernetes\n",
        encoding="utf-8",
    )

    is_valid, errors = SolutionController(root).load()
    assert not is_valid
    assert any("bad.yaml" in e for e in errors)


# ---------------------------------------------------------------------------
# Index API
# ---------------------------------------------------------------------------


def test_index_get_returns_none_for_unknown_name(tmp_path):
    """A miss is a None, not an exception."""
    controller = SolutionController(_solution(tmp_path))
    controller.load()
    assert controller.index.get(PlatformKind.TOPOLOGY, "nope") is None


def test_index_entry_carries_its_source_path(tmp_path):
    """Every entry knows the file it came from."""
    root = _solution(tmp_path)
    controller = SolutionController(root)
    controller.load()
    entry = controller.index.get(PlatformKind.TOPOLOGY, "main-topology")
    assert entry is not None
    assert entry.source.name == "topology.yaml"


def test_document_ref_str_includes_remote_when_set():
    """The remote slot is unused today but renders when present (ADR-0015)."""
    assert str(DocumentRef(kind=PlatformKind.TOPOLOGY, name="main")) == "topology/main"
    assert str(DocumentRef(kind=PlatformKind.TOPOLOGY, name="main", remote="shared")) == "@shared/topology/main"


# ---------------------------------------------------------------------------
# The shipped example solution
# ---------------------------------------------------------------------------


def _repo_config_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config"


@pytest.mark.skipif(not _repo_config_dir().exists(), reason="example config not present")
def test_shipped_example_solution_loads_cleanly():
    """The example solution under config/ must stay valid as models evolve."""
    controller = SolutionController(_repo_config_dir())
    is_valid, errors = controller.load()
    assert is_valid, errors
    assert controller.index.names_of(PlatformKind.WORKSPACE) == {"main"}
    assert controller.index.names_of(PlatformKind.RESOURCE) == {"storage-account", "linux-vm"}
