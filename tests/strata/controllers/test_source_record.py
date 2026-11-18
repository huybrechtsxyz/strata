#!/usr/bin/env python3
"""Tests for `SourceRecord` (docs/work/remote-commit-provenance.md, "The build record (Phase 2)")."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from strata.controllers.source_record import SourceRecord, check_build_record, read_build_record
from strata.models.solution_model import SolutionRemoteModel
from strata.utils.diagnostics import Diagnostics

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run([*_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _repository(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "first")
    return _git(path, "rev-parse", "HEAD")


def _remote(name: str, **fields: object) -> SolutionRemoteModel:
    data: dict[str, object] = {"name": name, "type": "git", "url": f"https://git.example.com/{name}.git"}
    data.update(fields)
    return SolutionRemoteModel.model_validate(data)


def _codes(diagnostics: Diagnostics) -> list[str | None]:
    return [d.code for d in diagnostics.warnings]


@pytest.fixture
def solution(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "solution"
    head = _repository(root, {"strata.yaml": "kind: solution\n", ".gitignore": "build/\n.strata/\n"})
    return root, head


def test_a_path_remote_is_recorded_with_no_ref(tmp_path: Path, solution: tuple[Path, str]):
    root, head = solution
    infra_head = _repository(tmp_path / "infra", {"modules/main.tf": "# v1\n"})
    record = SourceRecord()
    record.resolved("infra", _remote("infra", path="../infra"), tmp_path / "infra")
    record.took(tmp_path / "infra", "modules")
    diagnostics = Diagnostics()

    provenance = record.provenance(root, root / "build", head, diagnostics)

    assert provenance["remotes"] == {
        "infra": {"url": "https://git.example.com/infra.git", "ref": None, "commit": infra_head, "dirty": False}
    }
    assert provenance["solution"] == {"commit": head, "dirty": False}
    assert diagnostics.warnings == []


def test_a_ref_mode_remote_records_its_ref(tmp_path: Path, solution: tuple[Path, str]):
    root, head = solution
    infra_head = _repository(tmp_path / "clone", {"main.tf": "# v1\n"})
    record = SourceRecord()
    record.resolved("infra", _remote("infra", reference="v1.2.0"), tmp_path / "clone")
    record.took(tmp_path / "clone", "main.tf")

    provenance = record.provenance(root, root / "build", head, Diagnostics())

    assert provenance["remotes"]["infra"]["ref"] == "v1.2.0"
    assert provenance["remotes"]["infra"]["commit"] == infra_head


def test_a_local_remote_is_left_to_the_solution_record(solution: tuple[Path, str]):
    root, head = solution
    record = SourceRecord()
    record.resolved("config", _remote("config", type="local", url="."), root)
    record.took(root, "strata.yaml")

    assert record.provenance(root, root / "build", head, Diagnostics())["remotes"] == {}


def test_a_remote_nothing_was_taken_from_is_not_recorded(tmp_path: Path, solution: tuple[Path, str]):
    root, head = solution
    _repository(tmp_path / "infra", {"main.tf": "# v1\n"})
    record = SourceRecord()
    record.resolved("infra", _remote("infra", path="../infra"), tmp_path / "infra")

    assert record.provenance(root, root / "build", head, Diagnostics())["remotes"] == {}


def test_a_path_taken_from_an_unregistered_checkout_is_ignored(tmp_path: Path, solution: tuple[Path, str]):
    root, head = solution
    record = SourceRecord()
    record.took(root, "strata.yaml")

    assert record.provenance(root, root / "build", head, Diagnostics())["remotes"] == {}


def test_dirty_covers_only_the_paths_taken(tmp_path: Path, solution: tuple[Path, str]):
    """D4, and one `checkout_state()` with every path taken from the remote."""
    root, head = solution
    _repository(tmp_path / "infra", {"a/main.tf": "# a\n", "b/main.tf": "# b\n", "docs/README.md": "# r\n"})
    record = SourceRecord()
    record.resolved("infra", _remote("infra", path="../infra"), tmp_path / "infra")
    record.took(tmp_path / "infra", "a")
    record.took(tmp_path / "infra", "b")
    (tmp_path / "infra" / "docs" / "README.md").write_text("# changed\n", encoding="utf-8")

    assert record.provenance(root, root / "build", head, Diagnostics())["remotes"]["infra"]["dirty"] is False

    (tmp_path / "infra" / "b" / "main.tf").write_text("# changed\n", encoding="utf-8")
    diagnostics = Diagnostics()
    assert record.provenance(root, root / "build", head, diagnostics)["remotes"]["infra"]["dirty"] is True
    assert _codes(diagnostics) == ["remote_checkout_dirty"]
    assert diagnostics.warnings[0].hint is not None and "Commit the change" in diagnostics.warnings[0].hint


def test_a_glob_path_matches_within_one_segment(tmp_path: Path, solution: tuple[Path, str]):
    """`:(glob)`, as `pathlib.glob()` copied it: `*.json` does not reach into `nested/`."""
    root, head = solution
    _repository(tmp_path / "assets", {"api/a.json": "{}\n", "api/nested/b.json": "{}\n"})
    record = SourceRecord()
    record.resolved("assets", _remote("assets", path="../assets"), tmp_path / "assets")
    record.took(tmp_path / "assets", "api/*.json", glob=True)
    (tmp_path / "assets" / "api" / "nested" / "b.json").write_text('{"x": 1}\n', encoding="utf-8")

    assert record.provenance(root, root / "build", head, Diagnostics())["remotes"]["assets"]["dirty"] is False


def test_a_folder_that_is_not_a_checkout_warns_and_records_no_commit(tmp_path: Path, solution: tuple[Path, str]):
    root, head = solution
    (tmp_path / "copy").mkdir()
    (tmp_path / "copy" / "main.tf").write_text("# v1\n", encoding="utf-8")
    record = SourceRecord()
    record.resolved("infra", _remote("infra", path="../copy"), tmp_path / "copy")
    record.took(tmp_path / "copy", "main.tf")
    diagnostics = Diagnostics()

    remotes = record.provenance(root, root / "build", head, diagnostics)["remotes"]

    assert remotes["infra"]["commit"] is None and remotes["infra"]["dirty"] is None
    assert _codes(diagnostics) == ["audit_remote_commit_unavailable"]
    assert "Remote 'infra'" in diagnostics.warnings[0].message


def test_an_asset_location_is_recorded_under_its_own_key(tmp_path: Path, solution: tuple[Path, str]):
    root, head = solution
    _repository(tmp_path / "loc", {"openapi.json": "{}\n"})
    record = SourceRecord()
    record.resolved("asset:api", _remote("api", reference="main"), tmp_path / "loc")
    record.took(tmp_path / "loc", "openapi.json")
    (tmp_path / "loc" / "openapi.json").write_text('{"x": 1}\n', encoding="utf-8")
    diagnostics = Diagnostics()

    assert record.provenance(root, root / "build", head, diagnostics)["remotes"]["asset:api"]["dirty"] is True
    assert "Asset 'api'" in diagnostics.warnings[0].message


def test_the_solution_is_dirty_with_an_edited_document(solution: tuple[Path, str]):
    root, head = solution
    (root / "strata.yaml").write_text("kind: solution\n# edited\n", encoding="utf-8")
    diagnostics = Diagnostics()

    assert record_solution(root, head, diagnostics) == {"commit": head, "dirty": True}
    assert _codes(diagnostics) == ["remote_checkout_dirty"]
    assert "solution repository" in diagnostics.warnings[0].message


def test_the_build_output_and_strata_folder_do_not_make_the_solution_dirty(solution: tuple[Path, str]):
    """Excluded even when they are not ignored: here a build path outside `.gitignore`."""
    root, head = solution
    (root / "out" / "dev").mkdir(parents=True)
    (root / "out" / "dev" / "resolved.yaml").write_text("{}\n", encoding="utf-8")
    (root / ".strata").mkdir()
    (root / ".strata" / "state").write_text("x\n", encoding="utf-8")
    diagnostics = Diagnostics()

    provenance = SourceRecord().provenance(root, root / "out" / "dev", head, diagnostics)

    assert provenance["solution"] == {"commit": head, "dirty": False}
    assert diagnostics.warnings == []


def test_a_path_remote_checked_out_inside_the_solution_does_not_make_it_dirty(solution: tuple[Path, str]):
    root, head = solution
    _repository(root / "external" / "infra", {"main.tf": "# v1\n"})
    record = SourceRecord()
    record.resolved("infra", _remote("infra", path="external/infra"), root / "external" / "infra")
    record.took(root / "external" / "infra", "main.tf")

    assert record.provenance(root, root / "build", head, Diagnostics())["solution"]["dirty"] is False


def test_the_solution_root_may_be_a_folder_of_its_repository(tmp_path: Path):
    head = _repository(tmp_path / "repo", {"platform/strata.yaml": "kind: solution\n", "other/x.txt": "x\n"})
    (tmp_path / "repo" / "other" / "x.txt").write_text("changed\n", encoding="utf-8")

    assert record_solution(tmp_path / "repo" / "platform", head, Diagnostics()) == {"commit": head, "dirty": False}


def test_a_solution_outside_git_records_nulls_without_a_warning(tmp_path: Path):
    """As the SBOM: the default case for an example or a test. Phase 4's option makes it an error."""
    (tmp_path / "plain").mkdir()
    diagnostics = Diagnostics()

    assert record_solution(tmp_path / "plain", None, diagnostics) == {"commit": None, "dirty": None}
    assert diagnostics.warnings == []


# ---------------------------------------------------------------------------
# Reading the record back: read_build_record() (Phase 3) and check_build_record() (Phase 4, D14)
# ---------------------------------------------------------------------------

_CLEAN = {"commit": "5d0e7a9c31aa5d0e7a9c31aa", "dirty": False}


def _build(tmp_path: Path, record: dict[str, object] | None) -> Path:
    build_path = tmp_path / "build"
    build_path.mkdir()
    (build_path / "resolved.yaml").write_text(yaml.safe_dump({"deployment": "app", **(record or {})}), "utf-8")
    return build_path


def _record(solution: dict[str, object] = _CLEAN, **remotes: dict[str, object]) -> dict[str, object]:
    return {"solution": solution, "remotes": {key.replace("__", ":"): value for key, value in remotes.items()}}


def _errors(diagnostics: Diagnostics) -> list[tuple[str | None, str]]:
    return [(d.code, d.message) for d in diagnostics.errors]


def test_the_build_record_reads_back_what_provenance_wrote(tmp_path: Path, solution: tuple[Path, str]):
    root, head = solution
    infra_head = _repository(tmp_path / "infra", {"main.tf": "# v1\n"})
    record = SourceRecord()
    record.resolved("infra", _remote("infra", path="../infra"), tmp_path / "infra")
    record.took(tmp_path / "infra", "main.tf")
    build_path = _build(tmp_path, record.provenance(root, root / "build", head, Diagnostics()))

    read = read_build_record(build_path)

    assert read is not None
    assert (read.solution_commit, read.solution_dirty) == (head, False)
    assert read.remotes["infra"].commit == infra_head and read.remotes["infra"].dirty is False
    assert read.remotes["infra"].ref is None


@pytest.mark.parametrize(
    "record",
    [
        None,
        {"remotes": {}},
        {"solution": _CLEAN},
        {"solution": "5d0e", "remotes": {}},
        {"solution": {"commit": 5, "dirty": False}, "remotes": {}},
        {"solution": {"commit": "5d0e", "dirty": "no"}, "remotes": {}},
        {"solution": _CLEAN, "remotes": {"infra": {"url": "u", "unknown": 1}}},
    ],
    ids=["no-keys", "no-solution", "no-remotes", "solution-not-a-map", "bad-commit", "bad-dirty", "bad-entry"],
)
def test_a_resolved_yaml_without_a_readable_record_is_an_older_build(tmp_path: Path, record):
    assert read_build_record(_build(tmp_path, record)) is None


def test_no_resolved_yaml_is_an_older_build(tmp_path: Path):
    assert read_build_record(tmp_path) is None


def test_a_clean_record_passes(tmp_path: Path):
    build_path = _build(tmp_path, _record(infra={"url": "u", "commit": "3f9c2e1d4b", "dirty": False}))

    assert _errors(check_build_record(build_path)) == []


def test_a_build_without_the_record_fails(tmp_path: Path):
    diagnostics = check_build_record(_build(tmp_path, None))

    [(code, message)] = _errors(diagnostics)
    assert code == "build_remote_provenance_missing"
    assert "require_remote_commits" in message
    assert diagnostics.errors[0].hint is not None and "build run" in diagnostics.errors[0].hint


def test_a_solution_with_no_commit_fails(tmp_path: Path):
    diagnostics = check_build_record(_build(tmp_path, _record({"commit": None, "dirty": None})))

    assert _errors(diagnostics) == [
        (
            "audit_remote_commit_unavailable",
            "The solution repository: the build recorded no commit, and spec.audit.require_remote_commits is on.",
        )
    ]
    assert diagnostics.errors[0].hint is not None


def test_a_dirty_solution_fails(tmp_path: Path):
    diagnostics = check_build_record(_build(tmp_path, _record({"commit": "5d0e7a9c31aa5d0e", "dirty": True})))

    [(code, message)] = _errors(diagnostics)
    assert code == "remote_checkout_dirty"
    assert "uncommitted changes at commit 5d0e7a9c31aa," in message


def test_each_remote_gap_is_its_own_error(tmp_path: Path):
    build_path = _build(
        tmp_path,
        _record(
            infra={"url": "u", "commit": None},
            modules={"url": "u", "commit": "8a71b0c55e", "dirty": True},
            asset__api={"url": "u", "commit": "1b2c3d", "dirty": None},
            clean={"url": "u", "commit": "77aa", "dirty": False},
        ),
    )

    errors = _errors(check_build_record(build_path))

    assert [code for code, _ in errors] == [
        "remote_checkout_dirty",
        "audit_remote_commit_unavailable",
        "remote_checkout_dirty",
    ]
    assert errors[0][1].startswith("Asset 'api' (spec.location): the files the build took could not be checked")
    assert errors[1][1].startswith("Remote 'infra': the build recorded no commit")
    assert errors[2][1].startswith("Remote 'modules': the files the build took had uncommitted changes")


def record_solution(root: Path, commit: str | None, diagnostics: Diagnostics) -> dict[str, object]:
    solution: dict[str, object] = SourceRecord().provenance(root, root / "build", commit, diagnostics)["solution"]
    return solution
