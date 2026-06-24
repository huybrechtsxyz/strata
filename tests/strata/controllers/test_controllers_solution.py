"""Tests for SolutionController — in-memory operations (no real disk I/O required).

Tests that require a populated ``_solution`` manipulate it directly to avoid
full init/save round-trips with the heavy scaffold logic.
"""

from pathlib import Path
from unittest.mock import patch

from strata.controllers.solution_controller import SolutionController
from strata.models.solution_model import (
    SolutionMetaModel,
    SolutionModel,
    SolutionSpecModel,
    SolutionSpecProfileModel,
    SolutionSpecRepositoryModel,
)


def _make_solution(name: str = "test-solution") -> SolutionModel:
    """Construct a minimal valid SolutionModel for in-memory tests."""
    return SolutionModel(
        apiVersion="strata.huybrechts.xyz/v1",
        kind="solution",
        meta=SolutionMetaModel(name=name),
        spec=SolutionSpecModel(solution_id="00000000-0000-0000-0000-000000000001"),
    )


def _make_repo(name: str = "my-repo") -> SolutionSpecRepositoryModel:
    return SolutionSpecRepositoryModel(
        name=name,
        url="https://example.com/repo.git",
        path=f"repos/{name}",
        type="gitops",
        branch="main",
    )


def _make_profile(name: str = "default", active: bool = False) -> SolutionSpecProfileModel:
    return SolutionSpecProfileModel(name=name, active=active)


# ---------------------------------------------------------------------------
# Init / property access
# ---------------------------------------------------------------------------


class TestSolutionControllerInit:
    def test_solution_is_none_before_load(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        assert ctrl.solution is None

    def test_get_solution_id_empty_when_not_loaded(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        assert ctrl.get_solution_id() == ""

    def test_get_solution_id_returns_id_when_loaded(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        assert ctrl.get_solution_id() == "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Repository management
# ---------------------------------------------------------------------------


class TestSolutionControllerRepositories:
    def test_add_repository_fails_when_not_loaded(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ok, errors = ctrl.add_repository(_make_repo())
        assert ok is False
        assert any("No solution loaded" in e for e in errors)

    def test_add_repository_succeeds(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ok, errors = ctrl.add_repository(_make_repo("my-repo"))
        assert ok is True
        assert errors == []

    def test_add_duplicate_repository_fails(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_repository(_make_repo("repo-a"))
        ok, errors = ctrl.add_repository(_make_repo("repo-a"))
        assert ok is False
        assert any("already exists" in e for e in errors)

    def test_get_repositories_returns_all(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_repository(_make_repo("r1"))
        ctrl.add_repository(_make_repo("r2"))
        repos, errors = ctrl.get_repositories()
        assert errors == []
        assert len(repos) == 2

    def test_get_repositories_filtered_by_name(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_repository(_make_repo("r1"))
        ctrl.add_repository(_make_repo("r2"))
        repos, errors = ctrl.get_repositories(name="r1")
        assert errors == []
        assert len(repos) == 1
        assert str(repos[0].name) == "r1"

    def test_get_repositories_missing_name_returns_error(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        repos, errors = ctrl.get_repositories(name="nonexistent")
        assert repos == []
        assert any("not found" in e for e in errors)

    def test_remove_repository_removes_it(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_repository(_make_repo("to-remove"))
        ok, errors = ctrl.remove_repository("to-remove")
        assert ok is True
        repos, _ = ctrl.get_repositories()
        assert len(repos) == 0

    def test_remove_repository_not_found_returns_error(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ok, errors = ctrl.remove_repository("nonexistent")
        assert ok is False
        assert any("not found" in e for e in errors)

    def test_remove_repository_fails_when_not_loaded(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ok, errors = ctrl.remove_repository("any")
        assert ok is False


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------


class TestSolutionControllerProfiles:
    def test_add_profile_fails_when_not_loaded(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ok, errors = ctrl.add_profile(_make_profile())
        assert ok is False
        assert any("No solution loaded" in e for e in errors)

    def test_add_profile_succeeds(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ok, errors = ctrl.add_profile(_make_profile("dev"))
        assert ok is True

    def test_first_profile_is_set_active(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_profile(_make_profile("first", active=False))
        profiles, _ = ctrl.get_profiles()
        assert profiles[0].active is True

    def test_add_duplicate_profile_fails(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_profile(_make_profile("dev"))
        ok, errors = ctrl.add_profile(_make_profile("dev"))
        assert ok is False
        assert any("already exists" in e for e in errors)

    def test_remove_profile_removes_it(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_profile(_make_profile("stage"))
        ctrl.add_profile(_make_profile("prod"))
        # stage is active (first), activate prod first so stage can be removed
        ctrl.activate_profile("prod")
        ok, errors = ctrl.remove_profile("stage")
        assert ok is True
        profiles, _ = ctrl.get_profiles()
        names = [str(p.name) for p in profiles]
        assert "stage" not in names

    def test_remove_active_profile_fails(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_profile(_make_profile("only-one"))
        ok, errors = ctrl.remove_profile("only-one")
        assert ok is False
        assert any("active" in e for e in errors)

    def test_remove_profile_not_found_returns_error(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ok, errors = ctrl.remove_profile("nonexistent")
        assert ok is False

    def test_get_profiles_returns_all(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_profile(_make_profile("a"))
        ctrl.add_profile(_make_profile("b"))
        profiles, errors = ctrl.get_profiles()
        assert errors == []
        assert len(profiles) == 2

    def test_activate_profile_activates_only_target(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_profile(_make_profile("p1"))
        ctrl.add_profile(_make_profile("p2"))
        ok, _ = ctrl.activate_profile("p2")
        assert ok is True
        profiles, _ = ctrl.get_profiles()
        active = [p for p in profiles if p.active]
        assert len(active) == 1
        assert str(active[0].name) == "p2"

    def test_activate_profile_not_found_returns_error(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ok, errors = ctrl.activate_profile("missing")
        assert ok is False

    def test_get_active_profile_returns_active(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ctrl._solution = _make_solution()
        ctrl.add_profile(_make_profile("first"))
        ctrl.add_profile(_make_profile("second"))
        ctrl.activate_profile("second")
        profile, errors = ctrl.get_active_profile()
        assert errors == []
        assert profile is not None
        assert str(profile.name) == "second"

    def test_get_active_profile_when_not_loaded(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        profile, errors = ctrl.get_active_profile()
        assert profile is None
        assert errors != []


# ---------------------------------------------------------------------------
# get_state_dir static helper
# ---------------------------------------------------------------------------


class TestSolutionControllerStaticHelpers:
    def test_get_state_dir_returns_platform_subdir(self, tmp_path):
        state_dir = SolutionController.get_state_dir(tmp_path)
        assert state_dir == tmp_path / ".strata"

    def test_get_solution_json_path(self, tmp_path):
        path = SolutionController.get_solution_json_path(tmp_path)
        assert path == tmp_path / ".strata" / "solution.json"


# ---------------------------------------------------------------------------
# clean_solution
# ---------------------------------------------------------------------------


class TestSolutionControllerClean:
    def test_clean_solution_dry_run_no_deletion(self, tmp_path):
        logs_dir = tmp_path / ".strata" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "app.log").write_text("log content", encoding="utf-8")
        ctrl = SolutionController(tmp_path)
        ok, stats = ctrl.clean_solution(tmp_path, dry_run=True)
        assert ok is True
        assert stats["dry_run"] is True
        assert stats["logs_deleted"] == 1
        # file should still exist (dry run)
        assert (logs_dir / "app.log").exists()

    def test_clean_solution_deletes_log_files(self, tmp_path):
        logs_dir = tmp_path / ".strata" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "app.log").write_text("log content", encoding="utf-8")
        (logs_dir / "old.log").write_text("old content", encoding="utf-8")
        ctrl = SolutionController(tmp_path)
        ok, stats = ctrl.clean_solution(tmp_path, dry_run=False)
        assert ok is True
        assert stats["logs_deleted"] == 2
        remaining = list(logs_dir.iterdir())
        assert remaining == []

    def test_clean_solution_no_logs_folder_succeeds(self, tmp_path):
        ctrl = SolutionController(tmp_path)
        ok, stats = ctrl.clean_solution(tmp_path, dry_run=False)
        assert ok is True
        assert stats["logs_deleted"] == 0


# ---------------------------------------------------------------------------
# _scaffold_platform_dir — devcontainer scaffolding
# ---------------------------------------------------------------------------


class TestSolutionControllerScaffoldDevcontainer:
    """Tests for the .devcontainer scaffolding inside _scaffold_platform_dir.

    ``get_pkg_templates_path`` is patched to a controlled temp directory so
    only the devcontainer subtree is populated — all other scaffold sections
    gracefully skip missing templates.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_templates(templates_root: Path) -> None:
        """Populate templates_root/solution/dot.devcontainer/ with minimal fixtures."""
        dc_dir = templates_root / "solution" / "dot.devcontainer"
        dc_dir.mkdir(parents=True)
        (dc_dir / "devcontainer.json").write_text('{"name": "{{ SOLUTION_NAME }}"}', encoding="utf-8")
        (dc_dir / "post-create.sh").write_text("#!/bin/bash\necho hello\n", encoding="utf-8")

    @staticmethod
    def _make_ctrl(work_path: Path) -> SolutionController:
        """Return a controller with .strata/ pre-created and a loaded solution."""
        (work_path / ".strata").mkdir(exist_ok=True)
        ctrl = SolutionController(work_path)
        ctrl._solution = _make_solution("my-solution")
        return ctrl

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_devcontainer_json_is_created(self, tmp_path):
        templates_path = tmp_path / "_templates"
        self._make_templates(templates_path)
        ctrl = self._make_ctrl(tmp_path)
        with patch(
            "strata.controllers.solution_controller.get_pkg_templates_path",
            return_value=templates_path,
        ):
            ok, errors = ctrl._scaffold_platform_dir()
        assert ok is True
        assert errors == []
        assert (tmp_path / ".devcontainer" / "devcontainer.json").exists()

    def test_post_create_sh_is_created(self, tmp_path):
        templates_path = tmp_path / "_templates"
        self._make_templates(templates_path)
        ctrl = self._make_ctrl(tmp_path)
        with patch(
            "strata.controllers.solution_controller.get_pkg_templates_path",
            return_value=templates_path,
        ):
            ok, errors = ctrl._scaffold_platform_dir()
        assert ok is True
        assert (tmp_path / ".devcontainer" / "post-create.sh").exists()

    def test_solution_name_substituted_in_devcontainer_json(self, tmp_path):
        templates_path = tmp_path / "_templates"
        self._make_templates(templates_path)
        ctrl = self._make_ctrl(tmp_path)
        with patch(
            "strata.controllers.solution_controller.get_pkg_templates_path",
            return_value=templates_path,
        ):
            ctrl._scaffold_platform_dir()
        content = (tmp_path / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
        assert "my-solution" in content
        assert "{{ SOLUTION_NAME }}" not in content

    def test_post_create_sh_no_substitution(self, tmp_path):
        """post-create.sh is copied verbatim — no variable substitution."""
        templates_path = tmp_path / "_templates"
        self._make_templates(templates_path)
        ctrl = self._make_ctrl(tmp_path)
        with patch(
            "strata.controllers.solution_controller.get_pkg_templates_path",
            return_value=templates_path,
        ):
            ctrl._scaffold_platform_dir()
        content = (tmp_path / ".devcontainer" / "post-create.sh").read_text(encoding="utf-8")
        assert "#!/bin/bash" in content

    def test_existing_devcontainer_json_not_overwritten(self, tmp_path):
        templates_path = tmp_path / "_templates"
        self._make_templates(templates_path)
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        original = '{"name": "do-not-overwrite"}'
        (dc_dir / "devcontainer.json").write_text(original, encoding="utf-8")
        ctrl = self._make_ctrl(tmp_path)
        with patch(
            "strata.controllers.solution_controller.get_pkg_templates_path",
            return_value=templates_path,
        ):
            ok, errors = ctrl._scaffold_platform_dir()
        assert ok is True
        assert (dc_dir / "devcontainer.json").read_text(encoding="utf-8") == original

    def test_existing_post_create_sh_not_overwritten(self, tmp_path):
        templates_path = tmp_path / "_templates"
        self._make_templates(templates_path)
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        original = "#!/bin/bash\necho original\n"
        (dc_dir / "post-create.sh").write_text(original, encoding="utf-8")
        ctrl = self._make_ctrl(tmp_path)
        with patch(
            "strata.controllers.solution_controller.get_pkg_templates_path",
            return_value=templates_path,
        ):
            ok, errors = ctrl._scaffold_platform_dir()
        assert ok is True
        assert (dc_dir / "post-create.sh").read_text(encoding="utf-8") == original

    def test_missing_devcontainer_templates_dir_skipped_gracefully(self, tmp_path):
        """If the devcontainer/ template dir doesn't exist, scaffold returns ok with no .devcontainer/ created."""
        templates_path = tmp_path / "_templates"
        templates_path.mkdir()  # exists, but NO devcontainer/ subdir
        ctrl = self._make_ctrl(tmp_path)
        with patch(
            "strata.controllers.solution_controller.get_pkg_templates_path",
            return_value=templates_path,
        ):
            ok, errors = ctrl._scaffold_platform_dir()
        assert ok is True
        assert not (tmp_path / ".devcontainer").exists()
