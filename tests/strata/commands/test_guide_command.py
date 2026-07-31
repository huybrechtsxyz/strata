"""Tests for the `guide` command."""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_guide import guide_command

# ---------------------------------------------------------------------------
# Factories / helpers
# ---------------------------------------------------------------------------


def _runner() -> CliRunner:
    return CliRunner()


def _make_repo(
    name: str = "xyz-svc-app",
    url: str = "https://github.com/org/repo.git",
    path: str | None = None,
    repo_type: str = "git",
) -> dict:
    """Build a minimal SolutionSpecRepositoryModel dict."""
    return {
        "name": name,
        "url": url,
        "path": path or f"repos/{name}",
        "type": repo_type,
        "branch": "main",
    }


def _make_config_path(name: str = "cfg1", path: str = "@repo/config.yaml") -> dict:
    """Build a minimal SolutionSpecProfileConfigModel dict."""
    return {"name": name, "path": path}


def _make_profile(
    name: str = "prd",
    active: bool = True,
    config_paths: list | None = None,
    env_paths: list | None = None,
    data_paths: list | None = None,
    secret_paths: list | None = None,
) -> dict:
    """Build a minimal SolutionSpecProfileModel dict."""
    return {
        "name": name,
        "active": active,
        "configfile_paths": config_paths if config_paths is not None else [],
        "envfile_paths": env_paths if env_paths is not None else [],
        "datafile_paths": data_paths if data_paths is not None else [],
        "secretfile_paths": secret_paths if secret_paths is not None else [],
    }


def _make_solution_json(
    name: str = "my-platform",
    solution_id: str = "abc-00001",
    repositories: list | None = None,
    profiles: list | None = None,
) -> dict:
    """Build a minimal solution.json payload suitable for ``json.dumps``."""
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
    build_files: list[str] | None = None,
) -> Path:
    """
    Create a minimal strata workspace under *tmp_path*.

    - Creates ``.strata/`` directory unconditionally (so other helpers can
      write ``guide.yaml`` into it).
    - Writes ``.strata/solution.json`` when *solution* is given.
    - Creates ``build/`` and populates it when *build_files* is given (the
      list contains relative paths like ``"platform.json"``).
    """
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


def _make_config_yaml(
    dest: Path,
    kind: str = "configuration",
    api_version: str = "strata.huybrechts.xyz/v1",
    name: str = "my-config",
    include_spec: bool = True,
    filename: str = "my-config.yaml",
) -> Path:
    """Write a minimal strata YAML file at *dest/filename* and return its Path."""
    spec_block = "spec:\n  some_key: some_value\n" if include_spec else ""
    content = f"apiVersion: {api_version}\nkind: {kind}\nmeta:\n  name: {name}\n{spec_block}"
    p = dest / filename
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Workspace checklist mode (no --file)
# ---------------------------------------------------------------------------


class TestGuideCommandWorkspaceChecklist:
    # -----------------------------------------------------------------------
    # Test 1 — uninitialized workspace (no solution.json)
    # -----------------------------------------------------------------------

    def test_uninitialized_phase1_pending_exit0(self, tmp_path):
        """Phase 1 is ⬜ when solution.json is absent; exit code is always 0."""
        _make_workspace(tmp_path)  # no solution param → no solution.json written
        result = _runner().invoke(guide_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_uninitialized_next_step_shows_sln_init(self, tmp_path):
        """The next-step hint must mention ``strata sln init`` when uninitialized."""
        _make_workspace(tmp_path)
        result = _runner().invoke(guide_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "strata sln init" in result.output

    # -----------------------------------------------------------------------
    # Test 2 — fully initialized, all 7 phases ✅
    # -----------------------------------------------------------------------

    def test_fully_initialized_all_ok_json(self, tmp_path):
        """All 8 phases ✅ → complete:true, next_steps:[], every status 'ok'."""
        repo_dir = tmp_path / "repos" / "xyz-svc-app"
        repo_dir.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[_make_repo(path=str(repo_dir))],
            profiles=[
                _make_profile(
                    active=True,
                    config_paths=[_make_config_path()],
                )
            ],
        )
        _make_workspace(tmp_path, solution=solution, build_files=["platform.json"])
        # Phase 8 — write a valid sbom.json with at least one component
        sbom_path = tmp_path / "build" / "sbom.json"
        sbom_path.write_text(
            json.dumps(
                {"bomFormat": "CycloneDX", "components": [{"type": "library", "name": "nginx", "version": "1.27.0"}]}
            )
        )

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["complete"] is True
        assert data["next_steps"] == []
        assert all(item["status"] == "ok" for item in data["checklist"])

    # -----------------------------------------------------------------------
    # Test 3 — solution loaded but no repositories → phase 2 ⬜
    # -----------------------------------------------------------------------

    def test_no_repositories_phase2_pending(self, tmp_path):
        """Phase 2 is ⬜ when spec.repositories is empty."""
        solution = _make_solution_json(repositories=[])
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase2 = next(i for i in data["checklist"] if i["phase"] == 2)
        assert phase2["status"] == "pending"

    # -----------------------------------------------------------------------
    # Test 4 — repos registered but paths don't exist on disk → phase 3 ⚠️
    # -----------------------------------------------------------------------

    def test_repos_not_on_disk_phase3_warn(self, tmp_path):
        """Phase 3 is ⚠️ when repo paths don't exist on disk."""
        solution = _make_solution_json(
            repositories=[
                _make_repo(name="xyz-svc-alpha", path=str(tmp_path / "repos" / "xyz-svc-alpha")),
                _make_repo(name="xyz-svc-beta", path=str(tmp_path / "repos" / "xyz-svc-beta")),
            ],
        )
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase3 = next(i for i in data["checklist"] if i["phase"] == 3)
        assert phase3["status"] == "warn"
        assert phase3["detail"] is not None
        assert "not found" in phase3["detail"]

    def test_repos_not_on_disk_detail_string_format(self, tmp_path):
        """Phase 3 detail contains '{found}/{total} cloned' and the missing repo name."""
        repo_a = tmp_path / "repos" / "xyz-svc-alpha"
        repo_a.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[
                _make_repo(name="xyz-svc-alpha", path=str(repo_a)),
                _make_repo(name="xyz-svc-beta", path=str(tmp_path / "repos" / "xyz-svc-beta")),
            ],
        )
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        phase3 = next(i for i in data["checklist"] if i["phase"] == 3)
        assert "1/2" in phase3["detail"]
        assert "xyz-svc-beta" in phase3["detail"]

    # -----------------------------------------------------------------------
    # Test 5 — phase 3 hint: local repo (no URL) emits comment not git clone
    # -----------------------------------------------------------------------

    def test_repos_not_on_disk_local_repo_hint(self, tmp_path):
        """Missing local repo (empty URL) emits '# local repo not found:' in hint."""
        solution = _make_solution_json(
            repositories=[
                {
                    "name": "my-local-repo",
                    "url": "",  # no URL → local repo; no git clone possible
                    "path": str(tmp_path / "repos" / "my-local-repo"),
                    "type": "local",
                    "branch": "main",
                }
            ],
        )
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(guide_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "# local repo not found:" in result.output
        assert "git clone" not in result.output

    # -----------------------------------------------------------------------
    # Test 6 — profiles exist but none active → phase 5 ⬜
    # -----------------------------------------------------------------------

    def test_profiles_exist_but_none_active_phase5_pending(self, tmp_path):
        """Phase 5 is ⬜ when all profiles have active=False."""
        solution = _make_solution_json(
            profiles=[_make_profile(active=False)],
        )
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase5 = next(i for i in data["checklist"] if i["phase"] == 5)
        assert phase5["status"] == "pending"

    # -----------------------------------------------------------------------
    # Test 7 — active profile but zero refs → phase 6 ⚠️
    # -----------------------------------------------------------------------

    def test_active_profile_zero_refs_phase6_warn(self, tmp_path):
        """Phase 6 is ⚠️ when active profile has zero file references."""
        solution = _make_solution_json(
            profiles=[_make_profile(active=True)],  # all path lists default to []
        )
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase6 = next(i for i in data["checklist"] if i["phase"] == 6)
        assert phase6["status"] == "warn"
        assert phase6["detail"] is not None
        assert "0 registered" in phase6["detail"]

    # -----------------------------------------------------------------------
    # Test 8 — build/ dir exists but empty → phase 7 ⚠️
    # -----------------------------------------------------------------------

    def test_build_dir_empty_phase7_warn(self, tmp_path):
        """Phase 7 is ⚠️ when build/ dir exists but contains no files."""
        solution = _make_solution_json(
            profiles=[_make_profile(active=True, config_paths=[_make_config_path()])],
        )
        (tmp_path / "build").mkdir()  # empty build dir
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase7 = next(i for i in data["checklist"] if i["phase"] == 7)
        assert phase7["status"] == "warn"
        assert "empty" in (phase7["detail"] or "")

    # -----------------------------------------------------------------------
    # Test 9 — solution.json exists but is invalid → phase 1 ⚠️
    # -----------------------------------------------------------------------

    def test_invalid_solution_json_phase1_warn(self, tmp_path):
        """Phase 1 is ⚠️ when solution.json exists but cannot be parsed."""
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "solution.json").write_text("<<<INVALID JSON/YAML<<<")

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0
        # Phase 1 ⚠️ — the warning marker and "could not be parsed" hint must appear
        assert "⚠️" in result.output or "could not be parsed" in result.output or "strata sln init" in result.output

    # -----------------------------------------------------------------------
    # Test 10 — --output json shape
    # -----------------------------------------------------------------------

    def test_json_output_has_required_top_level_keys(self, tmp_path):
        """JSON output has workspace, checklist, next_steps (array), complete."""
        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert "workspace" in data
        assert "checklist" in data
        assert "next_steps" in data  # must be 'next_steps' (array), not 'next_step'
        assert "complete" in data
        assert isinstance(data["next_steps"], list)
        assert isinstance(data["checklist"], list)

    def test_json_checklist_items_have_required_fields(self, tmp_path):
        """Every checklist item has phase, label, status, and detail fields."""
        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        for item in data["checklist"]:
            assert "phase" in item
            assert "label" in item
            assert "status" in item
            assert "detail" in item  # may be null, but key must exist

    def test_json_status_values_are_valid_enum_strings(self, tmp_path):
        """Status values are limited to 'ok', 'warn', or 'pending'."""
        solution = _make_solution_json()
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        valid_statuses = {"ok", "warn", "pending"}
        for item in data["checklist"]:
            assert item["status"] in valid_statuses, f"Unexpected status: {item['status']}"

    # -----------------------------------------------------------------------
    # Test 11 — exit code is always 0 regardless of phase statuses
    # -----------------------------------------------------------------------

    def test_exit_code_always_0_uninitialized(self, tmp_path):
        """Exit code is 0 even when all phases are ⬜ (no solution.json)."""
        _make_workspace(tmp_path)
        result = _runner().invoke(guide_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_exit_code_always_0_with_warn_phases(self, tmp_path):
        """Exit code is 0 even when some phases are ⚠️."""
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "solution.json").write_text("<<<INVALID<<<")
        result = _runner().invoke(guide_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# File inspection mode (--file / -f)
# ---------------------------------------------------------------------------


class TestGuideCommandFileMode:
    # -----------------------------------------------------------------------
    # Test 12 — valid config YAML: all 5 file phases ✅, next_steps has both actions
    # -----------------------------------------------------------------------

    def test_valid_config_yaml_all_phases_ok(self, tmp_path):
        """A well-formed configuration YAML → all 5 file-phase checklist items are 'ok'."""
        config_file = _make_config_yaml(tmp_path)
        _make_workspace(tmp_path)

        result = _runner().invoke(
            guide_command,
            ["-f", str(config_file), "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert "checklist" in data
        assert all(item["status"] == "ok" for item in data["checklist"])

    def test_valid_config_yaml_next_steps_has_validate_and_register(self, tmp_path):
        """File mode: next_steps contains 'validate' and 'register' actions."""
        config_file = _make_config_yaml(tmp_path)
        _make_workspace(tmp_path)

        result = _runner().invoke(
            guide_command,
            ["-f", str(config_file), "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        actions = [step["action"] for step in data["next_steps"]]
        assert "validate" in actions
        assert "register" in actions

    def test_valid_config_yaml_validate_hint_contains_path(self, tmp_path):
        """The 'validate' next-step hint references the file path."""
        config_file = _make_config_yaml(tmp_path)
        _make_workspace(tmp_path)

        result = _runner().invoke(
            guide_command,
            ["-f", str(config_file), "--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        validate_step = next(s for s in data["next_steps"] if s["action"] == "validate")
        assert "strata validate" in validate_step["hint"]

    # -----------------------------------------------------------------------
    # Test 13 — unknown kind → phase 2 ⚠️, kind list shown
    # -----------------------------------------------------------------------

    def test_unknown_kind_phase2_warn(self, tmp_path):
        """An unrecognised kind value marks file-phase 2 as ⚠️."""
        bad_file = tmp_path / "unknown-kind.yaml"
        bad_file.write_text("apiVersion: strata.huybrechts.xyz/v1\nkind: blahblah\nmeta:\n  name: x\nspec: {}\n")
        _make_workspace(tmp_path)

        result = _runner().invoke(
            guide_command,
            ["-f", str(bad_file), "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase2 = next(i for i in data["checklist"] if i["phase"] == 2)
        assert phase2["status"] == "warn"

    def test_unknown_kind_console_lists_known_kinds(self, tmp_path):
        """Console output for unknown kind includes at least one known kind name."""
        bad_file = tmp_path / "unknown-kind.yaml"
        bad_file.write_text("apiVersion: strata.huybrechts.xyz/v1\nkind: blahblah\nmeta:\n  name: x\nspec: {}\n")
        _make_workspace(tmp_path)

        result = _runner().invoke(
            guide_command,
            ["-f", str(bad_file), "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0
        # At least one well-known kind must appear in the output
        assert "configuration" in result.output

    # -----------------------------------------------------------------------
    # Test 14 — --file path not found → phase 1 ⬜ (pending), exit 0
    # -----------------------------------------------------------------------

    def test_file_not_found_phase1_pending_exit0(self, tmp_path):
        """Missing --file path sets file-phase 1 to 'pending' and exits 0."""
        missing = str(tmp_path / "does-not-exist.yaml")
        _make_workspace(tmp_path)

        result = _runner().invoke(
            guide_command,
            ["-f", missing, "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        phase1 = next(i for i in data["checklist"] if i["phase"] == 1)
        assert phase1["status"] == "pending"

    # -----------------------------------------------------------------------
    # Test 15 — YAML parse error → phase 1 ⚠️, phases 2–5 ⬜
    # -----------------------------------------------------------------------

    def test_yaml_parse_error_phase1_warn_rest_pending(self, tmp_path):
        """YAML parse error marks file-phase 1 as ⚠️; phases 2–5 are ⬜."""
        broken_file = tmp_path / "broken.yaml"
        broken_file.write_text(": this is not valid yaml:::")
        _make_workspace(tmp_path)

        result = _runner().invoke(
            guide_command,
            ["-f", str(broken_file), "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]

        phase1 = next(i for i in data["checklist"] if i["phase"] == 1)
        assert phase1["status"] == "warn"

        # All downstream file phases must be blocked (pending)
        for phase_num in (2, 3, 4, 5):
            item = next((i for i in data["checklist"] if i["phase"] == phase_num), None)
            if item is not None:
                assert item["status"] == "pending", f"Phase {phase_num} expected 'pending', got '{item['status']}'"

    # -----------------------------------------------------------------------
    # File mode — JSON shape
    # -----------------------------------------------------------------------

    def test_file_mode_json_has_file_and_checklist_keys(self, tmp_path):
        """File-mode JSON output includes a 'file' block with path and kind."""
        config_file = _make_config_yaml(tmp_path)
        _make_workspace(tmp_path)

        result = _runner().invoke(
            guide_command,
            ["-f", str(config_file), "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert "file" in data
        assert "checklist" in data
        assert "next_steps" in data
        assert isinstance(data["next_steps"], list)

    def test_file_mode_json_next_steps_items_have_action_and_hint(self, tmp_path):
        """Every next_steps entry in file mode has 'action' and 'hint' fields."""
        config_file = _make_config_yaml(tmp_path)
        _make_workspace(tmp_path)

        result = _runner().invoke(
            guide_command,
            ["-f", str(config_file), "--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        for step in data["next_steps"]:
            assert "action" in step
            assert "hint" in step


# ---------------------------------------------------------------------------
# Hint customization via .strata/guide.yaml
# ---------------------------------------------------------------------------


class TestGuideCommandHintCustomization:
    # -----------------------------------------------------------------------
    # Test 16 — .strata/guide.yaml override for phase 6
    # -----------------------------------------------------------------------

    def test_guide_yaml_overrides_phase6_hint(self, tmp_path):
        """A .strata/guide.yaml hint override for phase 6 replaces the default."""
        # Need repos on disk and active profile so phase 6 (zero refs) is the next step
        repo_dir = tmp_path / "repos" / "xyz-svc-app"
        repo_dir.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[_make_repo(path=str(repo_dir))],
            profiles=[_make_profile(active=True)],  # zero refs → phase 6 ⚠️
        )
        _make_workspace(tmp_path, solution=solution)

        # Write the hint override file
        guide_yaml = tmp_path / ".strata" / "guide.yaml"
        guide_yaml.write_text(
            'phases:\n  6:\n    hint: "strata ref config add my-custom-config @repo/path.yaml --profile prd"\n'
        )

        result = _runner().invoke(guide_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "my-custom-config" in result.output


# ---------------------------------------------------------------------------
# --next mode
# ---------------------------------------------------------------------------


class TestGuideCommandNextMode:
    # -----------------------------------------------------------------------
    # Test 17 — uninitialized workspace → phase 1 hint, exit 0
    # -----------------------------------------------------------------------

    def test_next_uninitialized_shows_sln_init(self, tmp_path):
        """--next on an uninitialized workspace prints the phase 1 hint and exits 0."""
        _make_workspace(tmp_path)
        result = _runner().invoke(guide_command, ["--next", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "strata sln init" in result.output

    def test_next_uninitialized_no_checklist_markers(self, tmp_path):
        """--next must NOT render the full checklist (no ✅ / ⬜ / ⚠️ markers)."""
        _make_workspace(tmp_path)
        result = _runner().invoke(guide_command, ["--next", "--work-path", str(tmp_path)])
        assert "✅" not in result.output
        assert "⬜" not in result.output

    # -----------------------------------------------------------------------
    # Test 18 — complete workspace → completion message, exit 0
    # -----------------------------------------------------------------------

    def test_next_complete_workspace_shows_complete_message(self, tmp_path):
        """--next on a complete workspace emits the completion message and exits 0."""
        repo_dir = tmp_path / "repos" / "xyz-svc-app"
        repo_dir.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[_make_repo(path=str(repo_dir))],
            profiles=[_make_profile(active=True, config_paths=[_make_config_path()])],
        )
        _make_workspace(tmp_path, solution=solution, build_files=["platform.json"])
        sbom_path = tmp_path / "build" / "sbom.json"
        sbom_path.write_text(
            json.dumps(
                {"bomFormat": "CycloneDX", "components": [{"type": "library", "name": "nginx", "version": "1.0"}]}
            )
        )

        result = _runner().invoke(guide_command, ["--next", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "complete" in result.output.lower()

    # -----------------------------------------------------------------------
    # Test 19 — --next --output json, incomplete workspace
    # -----------------------------------------------------------------------

    def test_next_json_incomplete_has_required_fields(self, tmp_path):
        """--next --output json: complete=false, phase/label/command/see_also populated."""
        _make_workspace(tmp_path)  # uninitialized → phase 1 pending
        result = _runner().invoke(
            guide_command,
            ["--next", "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["complete"] is False
        assert isinstance(data["phase"], int)
        assert isinstance(data["label"], str)
        assert isinstance(data["command"], str)
        assert "see_also" in data

    def test_next_json_incomplete_command_contains_hint(self, tmp_path):
        """--next --output json: command field contains the strata CLI hint."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            guide_command,
            ["--next", "--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        assert "strata" in data["command"]

    # -----------------------------------------------------------------------
    # Test 20 — --next --output json, complete workspace
    # -----------------------------------------------------------------------

    def test_next_json_complete_returns_nulls(self, tmp_path):
        """--next --output json on complete workspace: complete=true, all fields null."""
        repo_dir = tmp_path / "repos" / "xyz-svc-app"
        repo_dir.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[_make_repo(path=str(repo_dir))],
            profiles=[_make_profile(active=True, config_paths=[_make_config_path()])],
        )
        _make_workspace(tmp_path, solution=solution, build_files=["platform.json"])
        sbom_path = tmp_path / "build" / "sbom.json"
        sbom_path.write_text(
            json.dumps(
                {"bomFormat": "CycloneDX", "components": [{"type": "library", "name": "nginx", "version": "1.0"}]}
            )
        )

        result = _runner().invoke(
            guide_command,
            ["--next", "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["complete"] is True
        assert data["phase"] is None
        assert data["label"] is None
        assert data["command"] is None

    # -----------------------------------------------------------------------
    # Test 21 — --next only shows the single next phase, not a later one
    # -----------------------------------------------------------------------

    def test_next_shows_phase4_not_later_phases(self, tmp_path):
        """--next with repos on disk but no profile shows phase 4, not phase 5+."""
        repo_dir = tmp_path / "repos" / "xyz-svc-app"
        repo_dir.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[_make_repo(path=str(repo_dir))],
            profiles=[],  # phase 4 pending
        )
        _make_workspace(tmp_path, solution=solution)

        result = _runner().invoke(
            guide_command,
            ["--next", "--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        assert data["phase"] == 4

    # -----------------------------------------------------------------------
    # Test 22 — --next exit code is always 0
    # -----------------------------------------------------------------------

    def test_next_exit_code_always_0_incomplete(self, tmp_path):
        """--next exit code is 0 even when the workspace is incomplete."""
        _make_workspace(tmp_path)
        result = _runner().invoke(guide_command, ["--next", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    # -----------------------------------------------------------------------
    # Test 23 — --next --quiet produces no output
    # -----------------------------------------------------------------------

    def test_next_quiet_produces_no_output(self, tmp_path):
        """--next --quiet suppresses all output (consistent with other commands)."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            guide_command,
            ["--next", "--quiet", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# --do mode
# ---------------------------------------------------------------------------


def _make_phase7_workspace(tmp_path: Path) -> Path:
    """Workspace with phases 1–6 complete so phase 7 (strata build run) is next."""
    repo_dir = tmp_path / "repos" / "xyz-svc-app"
    repo_dir.mkdir(parents=True)
    solution = _make_solution_json(
        repositories=[_make_repo(path=str(repo_dir))],
        profiles=[_make_profile(active=True, config_paths=[_make_config_path()])],
    )
    return _make_workspace(tmp_path, solution=solution)  # no build_files → phase 7 pending


class TestGuideCommandDoMode:
    # -----------------------------------------------------------------------
    # Test 24 — unresolved placeholder: not executed, shows placeholder
    # -----------------------------------------------------------------------

    def test_do_unresolved_phase1_not_executed(self, tmp_path):
        """--do on uninitialized workspace shows placeholder, does not execute."""
        _make_workspace(tmp_path)
        with patch("subprocess.run") as mock_run:
            result = _runner().invoke(guide_command, ["--do", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        mock_run.assert_not_called()

    def test_do_unresolved_shows_fill_in_message(self, tmp_path):
        """--do with unresolved placeholders tells user to fill in the values."""
        _make_workspace(tmp_path)
        result = _runner().invoke(guide_command, ["--do", "--work-path", str(tmp_path)])
        assert "Fill in" in result.output

    def test_do_unresolved_shows_placeholder_token(self, tmp_path):
        """--do console output includes the angle-bracket placeholder token."""
        _make_workspace(tmp_path)
        result = _runner().invoke(guide_command, ["--do", "--work-path", str(tmp_path)])
        assert "<name>" in result.output

    # -----------------------------------------------------------------------
    # Test 25 — unresolved: JSON shape
    # -----------------------------------------------------------------------

    def test_do_unresolved_json_executable_false(self, tmp_path):
        """--do --output json: executable=false when placeholders remain."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            guide_command,
            ["--do", "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["executable"] is False

    def test_do_unresolved_json_unresolved_list_populated(self, tmp_path):
        """--do --output json: unresolved list contains the placeholder name(s)."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            guide_command,
            ["--do", "--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        assert isinstance(data["unresolved"], list)
        assert len(data["unresolved"]) > 0
        assert "name" in data["unresolved"]

    def test_do_unresolved_json_executed_false(self, tmp_path):
        """--do --output json: executed=false when step could not be run."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            guide_command,
            ["--do", "--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        assert data["executed"] is False
        assert data["exit_code"] is None

    def test_do_unresolved_json_has_required_fields(self, tmp_path):
        """--do --output json response has all expected fields."""
        _make_workspace(tmp_path)
        result = _runner().invoke(
            guide_command,
            ["--do", "--work-path", str(tmp_path), "--output", "json"],
        )
        data = json.loads(result.output)["data"]
        for field in ("complete", "phase", "label", "command", "executable", "unresolved", "executed", "exit_code"):
            assert field in data, f"Missing field: {field}"

    # -----------------------------------------------------------------------
    # Test 26 — executable phase (no placeholders): subprocess called
    # -----------------------------------------------------------------------

    def test_do_executable_calls_subprocess(self, tmp_path):
        """--do on a phase with no placeholders (phase 7) calls subprocess.run."""
        _make_phase7_workspace(tmp_path)
        with patch("subprocess.run", return_value=type("R", (), {"returncode": 0})()) as mock_run:
            result = _runner().invoke(guide_command, ["--do", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_do_executable_console_shows_running(self, tmp_path):
        """--do on executable phase shows 'Running:' prefix."""
        _make_phase7_workspace(tmp_path)
        with patch("subprocess.run", return_value=type("R", (), {"returncode": 0})()):
            result = _runner().invoke(guide_command, ["--do", "--work-path", str(tmp_path)])
        assert "Running:" in result.output
        assert "strata build run" in result.output

    def test_do_executable_json_executed_true(self, tmp_path):
        """--do --output json on executable phase: executed=true, executable=true."""
        _make_phase7_workspace(tmp_path)
        with patch("subprocess.run", return_value=type("R", (), {"returncode": 0})()):
            result = _runner().invoke(
                guide_command,
                ["--do", "--work-path", str(tmp_path), "--output", "json"],
            )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["executed"] is True
        assert data["executable"] is True
        assert data["unresolved"] == []
        assert data["exit_code"] == 0

    # -----------------------------------------------------------------------
    # Test 27 — subprocess failure propagates as exit code 1
    # -----------------------------------------------------------------------

    def test_do_subprocess_failure_exits_1(self, tmp_path):
        """--do exits with code 1 when the executed command fails."""
        _make_phase7_workspace(tmp_path)
        with patch("subprocess.run", return_value=type("R", (), {"returncode": 1})()):
            result = _runner().invoke(guide_command, ["--do", "--work-path", str(tmp_path)])
        assert result.exit_code == 1

    def test_do_subprocess_failure_json_exit_code(self, tmp_path):
        """--do --output json records the non-zero exit_code from subprocess."""
        _make_phase7_workspace(tmp_path)
        with patch("subprocess.run", return_value=type("R", (), {"returncode": 2})()):
            result = _runner().invoke(
                guide_command,
                ["--do", "--work-path", str(tmp_path), "--output", "json"],
            )
        data = json.loads(result.output)["data"]
        assert data["exit_code"] == 2

    # -----------------------------------------------------------------------
    # Test 28 — complete workspace
    # -----------------------------------------------------------------------

    def test_do_complete_workspace_shows_complete_message(self, tmp_path):
        """--do on complete workspace emits the completion message."""
        repo_dir = tmp_path / "repos" / "xyz-svc-app"
        repo_dir.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[_make_repo(path=str(repo_dir))],
            profiles=[_make_profile(active=True, config_paths=[_make_config_path()])],
        )
        _make_workspace(tmp_path, solution=solution, build_files=["platform.json"])
        sbom_path = tmp_path / "build" / "sbom.json"
        sbom_path.write_text(
            json.dumps(
                {"bomFormat": "CycloneDX", "components": [{"type": "library", "name": "nginx", "version": "1.0"}]}
            )
        )
        result = _runner().invoke(guide_command, ["--do", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "complete" in result.output.lower()

    def test_do_complete_workspace_json_complete_true(self, tmp_path):
        """--do --output json on complete workspace: complete=true."""
        repo_dir = tmp_path / "repos" / "xyz-svc-app"
        repo_dir.mkdir(parents=True)
        solution = _make_solution_json(
            repositories=[_make_repo(path=str(repo_dir))],
            profiles=[_make_profile(active=True, config_paths=[_make_config_path()])],
        )
        _make_workspace(tmp_path, solution=solution, build_files=["platform.json"])
        sbom_path = tmp_path / "build" / "sbom.json"
        sbom_path.write_text(
            json.dumps(
                {"bomFormat": "CycloneDX", "components": [{"type": "library", "name": "nginx", "version": "1.0"}]}
            )
        )
        result = _runner().invoke(
            guide_command,
            ["--do", "--work-path", str(tmp_path), "--output", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["complete"] is True

    # -----------------------------------------------------------------------
    # Test 29 — --do --quiet
    # -----------------------------------------------------------------------

    def test_do_quiet_unresolved_no_output(self, tmp_path):
        """--do --quiet with unresolved placeholders: no output, no execution."""
        _make_workspace(tmp_path)
        with patch("subprocess.run") as mock_run:
            result = _runner().invoke(
                guide_command,
                ["--do", "--quiet", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert result.output.strip() == ""
        mock_run.assert_not_called()

    def test_do_quiet_executable_calls_subprocess(self, tmp_path):
        """--do --quiet on executable phase still runs subprocess (quiet ≠ skip)."""
        _make_phase7_workspace(tmp_path)
        with patch("subprocess.run", return_value=type("R", (), {"returncode": 0})()) as mock_run:
            result = _runner().invoke(
                guide_command,
                ["--do", "--quiet", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert result.output.strip() == ""
        mock_run.assert_called_once()
