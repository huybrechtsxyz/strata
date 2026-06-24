"""Tests for the GuideController."""

import json
from pathlib import Path

from strata.controllers.guide_controller import GuideController

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _make_solution_json(
    name: str = "my-platform",
    solution_id: str = "abc-00001",
    repositories: list | None = None,
    profiles: list | None = None,
) -> dict:
    return {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "solution",
        "meta": {"name": name},
        "spec": {
            "solution_id": solution_id,
            "repositories": repositories if repositories is not None else [],
            "profiles": profiles if profiles is not None else [],
        },
    }


def _make_workspace(
    tmp_path: Path,
    solution: dict | None = None,
    build_files: list | None = None,
) -> Path:
    strata_dir = tmp_path / ".strata"
    strata_dir.mkdir(parents=True, exist_ok=True)
    if solution is not None:
        (strata_dir / "solution.json").write_text(json.dumps(solution))
    if build_files is not None:
        build_dir = tmp_path / "build"
        build_dir.mkdir(exist_ok=True)
        for rel in build_files:
            p = build_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("placeholder")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests: basic lifecycle
# ---------------------------------------------------------------------------


class TestGuideControllerLifecycle:
    def test_load_no_solution(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        assert ctrl.solution is None
        assert ctrl.solution_exists is False

    def test_load_with_solution(self, tmp_path):
        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        assert ctrl.solution is not None
        assert ctrl.solution_exists is True
        assert ctrl.workspace_name == "my-platform"

    def test_reload_picks_up_changes(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        assert ctrl.solution is None

        # Now create solution
        solution = _make_solution_json(name="updated")
        (tmp_path / ".strata" / "solution.json").write_text(json.dumps(solution))
        ctrl.reload()
        assert ctrl.workspace_name == "updated"


# ---------------------------------------------------------------------------
# Tests: workspace checklist evaluation
# ---------------------------------------------------------------------------


class TestGuideControllerEvaluate:
    def test_uninitialized_all_pending(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        checklist = ctrl.evaluate()
        assert len(checklist) == 8
        assert checklist[0].status == "pending"
        assert checklist[0].label == "Workspace initialized"

    def test_initialized_no_repos(self, tmp_path):
        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        checklist = ctrl.evaluate()
        assert checklist[0].status == "ok"
        assert checklist[1].status == "pending"  # no repos

    def test_fully_initialized(self, tmp_path):
        repo_dir = tmp_path / "repos" / "xyz-svc-app"
        repo_dir.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[
                {"name": "xyz-svc-app", "url": "https://x.git", "path": str(repo_dir), "type": "git", "branch": "main"}
            ],
            profiles=[
                {
                    "name": "prd",
                    "active": True,
                    "configfile_paths": [{"name": "c", "path": "@repo/x.yaml"}],
                    "envfile_paths": [],
                    "datafile_paths": [],
                    "secretfile_paths": [],
                }
            ],
        )
        _make_workspace(tmp_path, solution=solution, build_files=["platform.json"])
        # Add sbom
        sbom_path = tmp_path / "build" / "sbom.json"
        sbom_path.write_text(json.dumps({"components": [{"type": "lib", "name": "x", "version": "1.0"}]}))

        ctrl = GuideController(tmp_path)
        ctrl.load()
        checklist = ctrl.evaluate()
        assert all(item.status == "ok" for item in checklist)
        assert ctrl.is_complete is True


# ---------------------------------------------------------------------------
# Tests: next step
# ---------------------------------------------------------------------------


class TestGuideControllerNextStep:
    def test_next_step_when_uninitialized(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        ctrl.evaluate()
        step = ctrl.find_next_step()
        assert step is not None
        assert step.phase == 1
        assert "strata sln init" in step.hint

    def test_next_step_none_when_complete(self, tmp_path):
        repo_dir = tmp_path / "repos" / "xyz-svc-app"
        repo_dir.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[
                {"name": "xyz-svc-app", "url": "https://x.git", "path": str(repo_dir), "type": "git", "branch": "main"}
            ],
            profiles=[
                {
                    "name": "prd",
                    "active": True,
                    "configfile_paths": [{"name": "c", "path": "@repo/x.yaml"}],
                    "envfile_paths": [],
                    "datafile_paths": [],
                    "secretfile_paths": [],
                }
            ],
        )
        _make_workspace(tmp_path, solution=solution, build_files=["platform.json"])
        sbom_path = tmp_path / "build" / "sbom.json"
        sbom_path.write_text(json.dumps({"components": [{"type": "lib", "name": "x", "version": "1.0"}]}))

        ctrl = GuideController(tmp_path)
        ctrl.load()
        ctrl.evaluate()
        step = ctrl.find_next_step()
        assert step is None


# ---------------------------------------------------------------------------
# Tests: file evaluation
# ---------------------------------------------------------------------------


class TestGuideControllerFileEval:
    def test_file_not_found(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        checklist, kind, name = ctrl.evaluate_file(tmp_path / "missing.yaml")
        assert checklist[0].status == "pending"
        assert kind is None
        assert name is None

    def test_valid_yaml_all_ok(self, tmp_path):
        _make_workspace(tmp_path)
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: my-cfg\nspec:\n  key: val\n"
        )
        ctrl = GuideController(tmp_path)
        ctrl.load()
        checklist, kind, name = ctrl.evaluate_file(yaml_file)
        assert all(item.status == "ok" for item in checklist)
        assert kind == "configuration"
        assert name == "my-cfg"
