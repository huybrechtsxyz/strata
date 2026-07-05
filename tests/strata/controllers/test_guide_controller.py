"""Tests for the GuideController."""

import json
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# Tests: workflow model
# ---------------------------------------------------------------------------


class TestWorkflowModel:
    def test_default_workflow_has_8_steps(self, tmp_path):
        from strata.models.workflow_model import get_default_workflow

        wf = get_default_workflow()
        assert len(wf.steps) == 8

    def test_default_workflow_step_ids_are_unique(self, tmp_path):
        from strata.models.workflow_model import get_default_workflow

        wf = get_default_workflow()
        ids = [s.id for s in wf.steps]
        assert len(ids) == len(set(ids))

    def test_workflow_load_yaml_valid(self, tmp_path):
        from strata.models.workflow_model import WorkflowDefinition

        yaml_content = """
steps:
  - id: step_one
    name: Step One
    check: solution_exists
    command: "strata sln init {name}"
    hint: "Do the first thing"
    see_also: "docs/guide.md"
  - id: step_two
    name: Step Two
    check: repos_registered
    depends_on: [step_one]
    hint: "Register repos"
"""
        wf = WorkflowDefinition.load_yaml(yaml_content)
        assert len(wf.steps) == 2
        assert wf.steps[0].id == "step_one"
        assert wf.steps[0].command == "strata sln init {name}"
        assert wf.steps[1].depends_on == ["step_one"]

    def test_workflow_load_yaml_invalid_raises(self, tmp_path):
        from strata.models.workflow_model import WorkflowDefinition

        with pytest.raises(ValueError):
            WorkflowDefinition.load_yaml("steps:\n  - missing_id: true\n")

    def test_workflow_to_yaml_roundtrip(self, tmp_path):
        from strata.models.workflow_model import get_default_workflow

        wf = get_default_workflow()
        yaml_str = wf.to_yaml()
        from strata.models.workflow_model import WorkflowDefinition

        wf2 = WorkflowDefinition.load_yaml(yaml_str)
        assert len(wf2.steps) == len(wf.steps)
        assert [s.id for s in wf2.steps] == [s.id for s in wf.steps]


# ---------------------------------------------------------------------------
# Tests: evaluate_from_workflow
# ---------------------------------------------------------------------------


class TestGuideControllerWorkflowEvaluate:
    def test_uninitialized_first_step_pending(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        checklist = ctrl.evaluate_from_workflow()
        assert len(checklist) == 8
        assert checklist[0].status == "pending"
        assert checklist[0].label == "Workspace initialized"

    def test_dependent_steps_also_pending_when_first_is_pending(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        checklist = ctrl.evaluate_from_workflow()
        # All steps that depend on workspace_init (which is pending) should also be pending
        for item in checklist[1:]:
            assert item.status == "pending"

    def test_initialized_first_step_ok(self, tmp_path):
        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        checklist = ctrl.evaluate_from_workflow()
        assert checklist[0].status == "ok"

    def test_caches_checklist(self, tmp_path):
        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        ctrl.evaluate_from_workflow()
        assert len(ctrl.checklist) == 8

    def test_checklist_items_have_step_id(self, tmp_path):
        """Each ChecklistItem produced by evaluate_from_workflow should carry a step_id."""
        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        checklist = ctrl.evaluate_from_workflow()
        ids = [item.step_id for item in checklist]
        assert all(sid is not None for sid in ids), "All items must have a step_id"
        assert "workspace_init" in ids
        assert "inventory_generated" in ids
        assert len(ids) == len(set(ids)), "step_ids must be unique"

    def test_fully_initialized_all_ok(self, tmp_path):
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
        checklist = ctrl.evaluate_from_workflow()
        assert all(item.status == "ok" for item in checklist)

    def test_custom_workflow_yaml_loaded_from_workspace(self, tmp_path):
        _make_workspace(tmp_path)
        custom_yaml = """
steps:
  - id: workspace_init
    name: Workspace initialized
    check: solution_exists
    hint: "Custom hint for workspace init"
"""
        (tmp_path / ".strata" / "workflow.yaml").write_text(custom_yaml)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        assert ctrl.workflow is not None
        assert len(ctrl.workflow.steps) == 1
        assert ctrl.workflow.steps[0].hint == "Custom hint for workspace init"

    def test_builtin_workflow_used_when_no_custom(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        assert ctrl.workflow is not None
        assert len(ctrl.workflow.steps) == 8


# ---------------------------------------------------------------------------
# Tests: find_next_step_from_workflow
# ---------------------------------------------------------------------------


class TestGuideControllerWorkflowNextStep:
    def test_returns_first_pending_actionable_step(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        step = ctrl.find_next_step_from_workflow()
        assert step is not None
        assert step.phase == 1
        assert step.label == "Workspace initialized"
        assert step.command == "strata sln init {name}"

    def test_hint_and_command_both_present(self, tmp_path):
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        step = ctrl.find_next_step_from_workflow()
        assert step is not None
        assert step.hint  # description text
        assert step.command  # concrete CLI command

    def test_returns_none_when_complete(self, tmp_path):
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
        step = ctrl.find_next_step_from_workflow()
        assert step is None

    def test_find_next_step_uses_cached_checklist(self, tmp_path):
        """find_next_step_from_workflow reuses the cached checklist without re-running checks."""

        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        ctrl.evaluate_from_workflow()  # populate cache

        call_count = {"n": 0}
        original = ctrl._check_repos_registered  # type: ignore[attr-defined]

        def counting_check():
            call_count["n"] += 1
            return original()

        ctrl._workflow_checks["repos_registered"] = counting_check  # type: ignore[index]
        ctrl.find_next_step_from_workflow()

        # Check function must NOT have been called (cached checklist was used)
        assert call_count["n"] == 0

    def test_find_next_step_evaluates_when_no_cache(self, tmp_path):
        """find_next_step_from_workflow evaluates checks when the checklist is empty."""
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        # Do NOT call evaluate_from_workflow() first
        step = ctrl.find_next_step_from_workflow()
        assert step is not None
        assert step.phase == 1        # workspace_init is pending → repos_registered and profile_created should be skipped
        _make_workspace(tmp_path)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        step = ctrl.find_next_step_from_workflow()
        assert step is not None
        # Should only surface step 1 (workspace_init), not step 2 (repos_registered) which depends on it
        assert step.phase == 1

    def test_active_profile_substituted_in_hint(self, tmp_path):
        solution = _make_solution_json(
            profiles=[
                {
                    "name": "prd",
                    "active": True,
                    "configfile_paths": [],
                    "envfile_paths": [],
                    "datafile_paths": [],
                    "secretfile_paths": [],
                }
            ],
        )
        _make_workspace(tmp_path, solution=solution)
        ctrl = GuideController(tmp_path)
        ctrl.load()
        step = ctrl.find_next_step_from_workflow()
        # files_registered step has {active} in command — if that step is next, it should be substituted
        if step and "{active}" in (step.command or ""):
            assert "prd" in (step.command or "")
