"""Controller for workspace readiness evaluation and guided onboarding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import yaml

from strata.controllers.base_controller import BaseController
from strata.controllers.solution_controller import SolutionController
from strata.exceptions.service_exception import PlatformFileNotFoundError
from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.solution_model import SolutionModel
from strata.models.workflow_model import (
    CheckResult,
    CheckStatus,
    WorkflowDefinition,
    WorkflowStep,
    get_default_workflow,
)
from strata.utils.system import get_pkg_data_path, is_cross_repo_ref, resolve_path


@dataclass
class ChecklistItem:
    phase: int
    label: str
    status: Literal["ok", "warn", "pending"]
    detail: Optional[str] = None
    step_id: Optional[str] = None  # Workflow step ID; None for legacy 8-phase checklist


@dataclass
class NextStepItem:
    phase: int
    label: str
    hint: str
    see_also: Optional[str] = None
    command: Optional[str] = None  # Concrete strata command to run (from workflow.yaml)


class GuideController(BaseController):
    """Manages workspace readiness state for the console and guide commands."""

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = work_path
        self._solution_controller = SolutionController(work_path)
        self._hints: Dict[str, Any] = {}
        self._checklist: List[ChecklistItem] = []
        self._solution: Optional[SolutionModel] = None
        self._solution_exists: bool = False
        self._workflow: Optional[WorkflowDefinition] = None
        self._workflow_checks: Dict[str, Callable[..., CheckResult]] = {}

    @property
    def work_path(self) -> Path:
        return self._work_path

    @property
    def solution(self) -> Optional[SolutionModel]:
        return self._solution

    @property
    def solution_exists(self) -> bool:
        return self._solution_exists

    @property
    def checklist(self) -> List[ChecklistItem]:
        return self._checklist

    @property
    def is_complete(self) -> bool:
        return all(item.status == "ok" for item in self._checklist)

    @property
    def active_profile_name(self) -> Optional[str]:
        if self._solution is None:
            return None
        active_profile, _ = self._solution_controller.get_active_profile()
        if active_profile:
            return str(active_profile.name)
        return None

    @property
    def workspace_name(self) -> Optional[str]:
        if self._solution is not None:
            return str(self._solution.meta.name)
        return None

    @property
    def solution_id(self) -> Optional[str]:
        if self._solution is not None:
            return self._solution_controller.get_solution_id()
        return None

    @property
    def workflow(self) -> Optional[WorkflowDefinition]:
        """Return the loaded workflow, or None if not loaded."""
        return self._workflow

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load solution state and hints. Safe to call multiple times (reload)."""
        self._load_solution()
        self._hints = self._load_hints()
        self._load_workflow()
        self._register_workflow_checks()

    def reload(self) -> None:
        """Re-read workspace state from disk."""
        self._solution_controller = SolutionController(self._work_path)
        self.load()

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self) -> List[ChecklistItem]:
        """Evaluate the 8-phase workspace checklist. Returns and caches the result."""
        self._checklist = self._evaluate_checklist(self._solution, self._solution_exists)
        return self._checklist

    def find_next_step(self) -> Optional[NextStepItem]:
        """Return the first incomplete step with its hint, or None if all complete."""
        return self._find_next_step(self._checklist, self._solution, self._hints, self.active_profile_name)

    def evaluate_file(self, file_path: Path) -> tuple[List[ChecklistItem], Optional[str], Optional[str]]:
        """Run file inspection phases. Returns (checklist, detected_kind, detected_name)."""
        return self._evaluate_file_checklist(file_path)

    def find_file_next_steps(
        self,
        kind: Optional[str],
        resolved_path: Path,
    ) -> List[NextStepItem]:
        """Generate next-step actions for file inspection mode."""
        return self._find_file_next_steps(kind, self.active_profile_name, resolved_path, self._hints)

    def resolve_file_path(self, raw: str) -> Path:
        """Resolve relative, absolute, and @repo/ paths."""
        if is_cross_repo_ref(raw):
            if self._solution is None:
                raise ValueError("@repo reference requires an initialized workspace")
            repo_map = self._solution_controller.get_repo_map()
            return resolve_path("", raw, repo_map=repo_map)
        path = Path(raw)
        if path.is_absolute():
            return path
        return Path.cwd() / path

    @property
    def hints(self) -> Dict[str, Any]:
        return self._hints

    # ------------------------------------------------------------------
    # Private: solution loading
    # ------------------------------------------------------------------

    def _load_solution(self) -> None:
        solution_path = self._work_path / ".strata" / "solution.json"
        if not solution_path.exists():
            self._solution = None
            self._solution_exists = False
            return
        self._solution_exists = True
        try:
            success, _ = self._solution_controller.load()
            if success:
                self._solution = self._solution_controller.solution
            else:
                self._solution = None
        except Exception:
            self._solution = None

    # ------------------------------------------------------------------
    # Private: hints
    # ------------------------------------------------------------------

    def _load_hints(self) -> Dict[str, Any]:
        """Load built-in hints then shallow-merge project overrides from .strata/guide.yaml."""
        hints_path = get_pkg_data_path() / "guide-hints.yaml"
        if not hints_path.exists():
            raise PlatformFileNotFoundError(str(hints_path), file_type="guide-hints.yaml")

        with open(hints_path, "r", encoding="utf-8") as fh:
            hints: Dict[str, Any] = yaml.safe_load(fh) or {}

        override_path = SolutionController.get_guide_path(self._work_path)
        if not override_path.exists():
            return hints

        try:
            with open(override_path, "r", encoding="utf-8") as fh:
                overrides: Dict[str, Any] = yaml.safe_load(fh) or {}

            for key, value in overrides.items():
                if key in ("header", "complete"):
                    hints[key] = value
                elif key == "phases":
                    hints.setdefault("phases", {})
                    for phase_num, phase_data in (value or {}).items():
                        hints["phases"].setdefault(phase_num, {})
                        for sub_key, sub_val in (phase_data or {}).items():
                            hints["phases"][phase_num][sub_key] = sub_val
                elif key == "kinds":
                    hints.setdefault("kinds", {})
                    for kind_name, kind_data in (value or {}).items():
                        hints["kinds"].setdefault(kind_name, {})
                        for sub_key, sub_val in (kind_data or {}).items():
                            hints["kinds"][kind_name][sub_key] = sub_val
        except Exception as e:
            self.logger.warning("Could not load guide overrides", path=str(override_path), error=str(e))

        return hints

    # ------------------------------------------------------------------
    # Private: token substitution
    # ------------------------------------------------------------------

    def _apply_tokens(
        self,
        hint: Optional[str],
        active_profile: Optional[str],
        file_name: Optional[str],
        resolved_path: Optional[Path],
    ) -> str:
        if not hint:
            return ""
        result = hint
        result = result.replace("<active>", active_profile or "<active>")
        result = result.replace("<name>", file_name or "<name>")
        if resolved_path is not None:
            result = result.replace("<path>", str(resolved_path))
        return result

    # ------------------------------------------------------------------
    # Private: workspace checklist evaluation
    # ------------------------------------------------------------------

    def _evaluate_checklist(self, solution: Optional[SolutionModel], solution_exists: bool) -> List[ChecklistItem]:
        checklist: List[ChecklistItem] = []

        # Phase 1 — Workspace initialized
        if not solution_exists:
            phase1 = ChecklistItem(1, "Workspace initialized", "pending")
        elif solution is None:
            phase1 = ChecklistItem(1, "Workspace initialized", "warn", "solution.json could not be parsed")
        elif not str(solution.meta.name).strip():
            phase1 = ChecklistItem(1, "Workspace initialized", "warn", "workspace name is empty")
        else:
            phase1 = ChecklistItem(1, "Workspace initialized", "ok")
        checklist.append(phase1)

        # Phase 2 — Repositories registered
        if solution is None:
            checklist.append(ChecklistItem(2, "Repositories registered", "pending"))
        else:
            repos = solution.spec.repositories or []
            if not repos:
                checklist.append(ChecklistItem(2, "Repositories registered", "pending"))
            else:
                checklist.append(ChecklistItem(2, "Repositories registered", "ok", str(len(repos))))
        phase2 = checklist[1]

        # Phase 3 — Repositories on disk
        if phase2.status == "pending":
            checklist.append(ChecklistItem(3, "Repositories on disk", "pending"))
        else:
            repos = (solution.spec.repositories or []) if solution else []
            missing_names: List[str] = []
            for repo in repos:
                repo_path = Path(repo.path)
                if not repo_path.is_absolute():
                    repo_path = self._work_path / repo_path
                if not repo_path.exists():
                    missing_names.append(str(repo.name))
            if not missing_names:
                checklist.append(ChecklistItem(3, "Repositories on disk", "ok"))
            else:
                found = len(repos) - len(missing_names)
                detail = f"{found}/{len(repos)} cloned — {', '.join(missing_names)} not found"
                checklist.append(ChecklistItem(3, "Repositories on disk", "warn", detail))

        # Phase 4 — Profile created
        if solution is None:
            checklist.append(ChecklistItem(4, "Profile created", "pending"))
        else:
            profiles = solution.spec.profiles or []
            if not profiles:
                checklist.append(ChecklistItem(4, "Profile created", "pending"))
            else:
                detail = ", ".join(str(p.name) for p in profiles)
                checklist.append(ChecklistItem(4, "Profile created", "ok", detail))
        phase4 = checklist[3]

        # Phase 5 — Profile activated
        if phase4.status == "pending":
            checklist.append(ChecklistItem(5, "Profile activated", "pending"))
        else:
            profiles = (solution.spec.profiles or []) if solution else []
            active_list = [p for p in profiles if p.active]
            if not active_list:
                checklist.append(ChecklistItem(5, "Profile activated", "pending"))
            else:
                checklist.append(ChecklistItem(5, "Profile activated", "ok", str(active_list[0].name)))
        phase5 = checklist[4]

        # Phase 6 — File references registered
        if phase5.status == "pending":
            checklist.append(ChecklistItem(6, "File references registered", "pending"))
        else:
            profiles = (solution.spec.profiles or []) if solution else []
            active_list = [p for p in profiles if p.active]
            if not active_list:
                checklist.append(ChecklistItem(6, "File references registered", "pending"))
            else:
                profile = active_list[0]
                profile_name = str(profile.name)
                config_count = len(profile.configfile_paths or [])
                env_count = len(profile.envfile_paths or [])
                secret_count = len(profile.secretfile_paths or [])
                data_count = len(profile.datafile_paths or [])
                total = config_count + env_count + secret_count + data_count
                if total == 0:
                    detail = f"0 registered on active profile '{profile_name}'"
                    checklist.append(ChecklistItem(6, "File references registered", "warn", detail))
                else:
                    type_parts: List[str] = []
                    if config_count:
                        type_parts.append(f"config: {config_count}")
                    if env_count:
                        type_parts.append(f"env: {env_count}")
                    if secret_count:
                        type_parts.append(f"secret: {secret_count}")
                    if data_count:
                        type_parts.append(f"data: {data_count}")
                    detail = f"{total} registered on active profile '{profile_name}' ({', '.join(type_parts)})"
                    checklist.append(ChecklistItem(6, "File references registered", "ok", detail))

        # Phase 7 — Build artifact exists
        build_path = self._work_path / "build"
        if not build_path.exists():
            checklist.append(ChecklistItem(7, "Build artifact exists", "pending"))
        elif not any(f for f in build_path.rglob("*") if f.is_file()):
            checklist.append(ChecklistItem(7, "Build artifact exists", "warn", "directory is empty"))
        else:
            checklist.append(ChecklistItem(7, "Build artifact exists", "ok"))
        phase7 = checklist[6]

        # Phase 8 — Platform inventory generated
        if phase7.status == "pending":
            checklist.append(ChecklistItem(8, "Platform inventory generated", "pending"))
        else:
            sbom_files = list(build_path.rglob("sbom.json")) if build_path.exists() else []
            if not sbom_files:
                checklist.append(ChecklistItem(8, "Platform inventory generated", "pending"))
            else:
                total_components = 0
                for sbom_path in sbom_files:
                    try:
                        with open(sbom_path, encoding="utf-8") as fh:
                            sbom_data = json.load(fh)
                        total_components += len(sbom_data.get("components", []))
                    except Exception:
                        pass
                if total_components > 0:
                    checklist.append(
                        ChecklistItem(8, "Platform inventory generated", "ok", f"{total_components} components")
                    )
                else:
                    checklist.append(
                        ChecklistItem(8, "Platform inventory generated", "warn", "sbom.json present but empty")
                    )

        return checklist

    # ------------------------------------------------------------------
    # Private: next step selection
    # ------------------------------------------------------------------

    def _find_next_step(
        self,
        checklist: List[ChecklistItem],
        solution: Optional[SolutionModel],
        hints: Dict[str, Any],
        active_profile_name: Optional[str],
    ) -> Optional[NextStepItem]:
        phases = hints.get("phases", {}) or {}

        for item in checklist:
            if item.status == "ok":
                continue

            phase_data = phases.get(item.phase, {}) or {}
            see_also = phase_data.get("see_also")

            if item.phase == 3 and solution is not None:
                repos = solution.spec.repositories or []
                missing_repos = [
                    r
                    for r in repos
                    if not (Path(r.path) if Path(r.path).is_absolute() else self._work_path / r.path).exists()
                ]
                hint_lines: List[str] = []
                for repo in missing_repos:
                    url_str = str(repo.url).strip() if repo.url else ""
                    if url_str:
                        hint_lines.append(f"git clone {repo.url} {repo.path}")
                    else:
                        hint_lines.append(f"# local repo not found: {repo.path}")
                hint = "\n".join(hint_lines) if hint_lines else (phase_data.get("hint") or "")
            else:
                raw_hint = phase_data.get("hint") or ""
                hint = self._apply_tokens(raw_hint, active_profile_name, None, None)

            return NextStepItem(
                phase=item.phase,
                label=item.label,
                hint=hint,
                see_also=see_also,
            )

        return None

    # ------------------------------------------------------------------
    # Private: file checklist evaluation
    # ------------------------------------------------------------------

    def _evaluate_file_checklist(self, path: Path) -> tuple[List[ChecklistItem], Optional[str], Optional[str]]:
        checklist: List[ChecklistItem] = []
        detected_kind: Optional[str] = None
        detected_name: Optional[str] = None

        blocked_labels = {
            2: "Kind recognized",
            3: "apiVersion present",
            4: "Name present",
            5: "Spec present",
        }

        # Phase 1 — File readable
        if not path.exists():
            checklist.append(ChecklistItem(1, "File readable", "pending", f"{path} not found"))
            for phase, label in blocked_labels.items():
                checklist.append(ChecklistItem(phase, label, "pending"))
            return checklist, None, None

        doc: Optional[dict] = None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            if not isinstance(doc, dict):
                raise ValueError("Document is not a YAML mapping")
            checklist.append(ChecklistItem(1, "File readable", "ok"))
        except Exception as e:
            checklist.append(ChecklistItem(1, "File readable", "warn", str(e)))
            for phase, label in blocked_labels.items():
                checklist.append(ChecklistItem(phase, label, "pending"))
            return checklist, None, None

        # Phase 2 — Kind recognized
        kind_raw = doc.get("kind")
        valid_kinds = {k.value for k in PlatformKind if k != PlatformKind.PLATFORM_MODEL}
        if kind_raw is None:
            checklist.append(ChecklistItem(2, "Kind recognized", "pending"))
        elif kind_raw in valid_kinds:
            detected_kind = kind_raw
            checklist.append(ChecklistItem(2, "Kind recognized", "ok", kind_raw))
        else:
            kind_list = ", ".join(sorted(valid_kinds))
            checklist.append(
                ChecklistItem(
                    2,
                    "Kind recognized",
                    "warn",
                    f'not recognized ("{kind_raw}") — expected one of: {kind_list}',
                )
            )

        # Phase 3 — apiVersion present
        api_version = doc.get("apiVersion")
        valid_versions = {v.value for v in PlatformVersion}
        if api_version is None:
            checklist.append(ChecklistItem(3, "apiVersion present", "pending"))
        elif api_version in valid_versions:
            checklist.append(ChecklistItem(3, "apiVersion present", "ok", api_version))
        else:
            checklist.append(ChecklistItem(3, "apiVersion present", "warn", f'wrong value: "{api_version}"'))

        # Phase 4 — Name present
        meta = doc.get("meta") or {}
        name_val = meta.get("name") if isinstance(meta, dict) else None
        if name_val is None:
            checklist.append(ChecklistItem(4, "Name present", "pending"))
        elif not str(name_val).strip():
            checklist.append(ChecklistItem(4, "Name present", "warn", "empty string"))
        else:
            detected_name = str(name_val)
            checklist.append(ChecklistItem(4, "Name present", "ok", detected_name))

        # Phase 5 — Spec present
        spec = doc.get("spec")
        if spec is None:
            checklist.append(ChecklistItem(5, "Spec present", "pending"))
        elif not isinstance(spec, dict) or len(spec) == 0:
            checklist.append(ChecklistItem(5, "Spec present", "warn", "present but empty"))
        else:
            checklist.append(ChecklistItem(5, "Spec present", "ok"))

        return checklist, detected_kind, detected_name

    # ------------------------------------------------------------------
    # Private: file next steps
    # ------------------------------------------------------------------

    def _find_file_next_steps(
        self,
        kind: Optional[str],
        active_profile: Optional[str],
        resolved_path: Path,
        hints: Dict[str, Any],
    ) -> List[NextStepItem]:
        next_steps: List[NextStepItem] = []

        validate_hint = f"strata validate -f {resolved_path}"
        next_steps.append(NextStepItem(phase=0, label="Validate", hint=validate_hint, see_also=None))

        if kind is None:
            valid_kinds = sorted(k.value for k in PlatformKind if k != PlatformKind.PLATFORM_MODEL)
            kind_list = ", ".join(valid_kinds)
            next_steps.append(
                NextStepItem(
                    phase=0,
                    label="Kind list",
                    hint=f"Known kinds: {kind_list}",
                    see_also="strata help --topic quickstart",
                )
            )
            return next_steps

        kinds_data = hints.get("kinds", {}) or {}
        kind_data = kinds_data.get(kind, {}) or {}
        register_hint = kind_data.get("register")
        see_also = kind_data.get("see_also")

        if register_hint:
            register_hint = self._apply_tokens(register_hint, active_profile, resolved_path.stem, resolved_path)
            next_steps.append(NextStepItem(phase=0, label="Register", hint=register_hint, see_also=see_also))

        return next_steps

    # ------------------------------------------------------------------
    # Private: workflow loading
    # ------------------------------------------------------------------

    def _load_workflow(self) -> None:
        """Load .strata/workflow.yaml from workspace; fall back to built-in default."""
        workspace_workflow = self._work_path / ".strata" / "workflow.yaml"
        if workspace_workflow.exists():
            try:
                with open(workspace_workflow, "r", encoding="utf-8") as fh:
                    content = fh.read()
                self._workflow = WorkflowDefinition.load_yaml(content)
                self.logger.debug("Loaded custom workflow", path=str(workspace_workflow))
                return
            except Exception as e:
                self.logger.warning("Could not load workflow.yaml, using built-in default", error=str(e))

        # Fall back to built-in data file
        builtin_path = get_pkg_data_path() / "workflow.yaml"
        if builtin_path.exists():
            try:
                with open(builtin_path, "r", encoding="utf-8") as fh:
                    content = fh.read()
                self._workflow = WorkflowDefinition.load_yaml(content)
                return
            except Exception as e:
                self.logger.warning("Could not load built-in workflow.yaml, using code default", error=str(e))

        self._workflow = get_default_workflow()

    def _register_workflow_checks(self) -> None:
        """Register the built-in check functions keyed by name."""
        self._workflow_checks = {
            "solution_exists": self._check_solution_exists,
            "repos_registered": self._check_repos_registered,
            "repos_cloned": self._check_repos_cloned,
            "profile_exists": self._check_profile_exists,
            "profile_active": self._check_profile_active,
            "files_registered": self._check_files_registered,
            "build_exists": self._check_build_exists,
            "sbom_exists": self._check_sbom_exists,
        }

    # ------------------------------------------------------------------
    # Private: workflow check functions
    # ------------------------------------------------------------------

    def _check_solution_exists(self) -> CheckResult:
        if not self._solution_exists:
            return CheckResult("pending")
        if self._solution is None:
            return CheckResult("warn", "solution.json could not be parsed")
        if not str(self._solution.meta.name).strip():
            return CheckResult("warn", "workspace name is empty")
        return CheckResult("ok")

    def _check_repos_registered(self) -> CheckResult:
        if self._solution is None:
            return CheckResult("pending")
        repos = self._solution.spec.repositories or []
        if not repos:
            return CheckResult("pending")
        return CheckResult("ok", str(len(repos)))

    def _check_repos_cloned(self) -> CheckResult:
        if self._solution is None:
            return CheckResult("pending")
        repos = self._solution.spec.repositories or []
        missing: List[str] = []
        for repo in repos:
            repo_path = Path(repo.path)
            if not repo_path.is_absolute():
                repo_path = self._work_path / repo_path
            if not repo_path.exists():
                missing.append(str(repo.name))
        if missing:
            found = len(repos) - len(missing)
            return CheckResult("warn", f"{found}/{len(repos)} cloned — {', '.join(missing)} not found")
        return CheckResult("ok")

    def _check_profile_exists(self) -> CheckResult:
        if self._solution is None:
            return CheckResult("pending")
        profiles = self._solution.spec.profiles or []
        if not profiles:
            return CheckResult("pending")
        detail = ", ".join(str(p.name) for p in profiles)
        return CheckResult("ok", detail)

    def _check_profile_active(self) -> CheckResult:
        if self._solution is None:
            return CheckResult("pending")
        profiles = self._solution.spec.profiles or []
        active_list = [p for p in profiles if p.active]
        if not active_list:
            return CheckResult("pending")
        return CheckResult("ok", str(active_list[0].name))

    def _check_files_registered(self) -> CheckResult:
        if self._solution is None:
            return CheckResult("pending")
        profiles = self._solution.spec.profiles or []
        active_list = [p for p in profiles if p.active]
        if not active_list:
            return CheckResult("pending")
        profile = active_list[0]
        profile_name = str(profile.name)
        config_count = len(profile.configfile_paths or [])
        env_count = len(profile.envfile_paths or [])
        secret_count = len(profile.secretfile_paths or [])
        data_count = len(profile.datafile_paths or [])
        total = config_count + env_count + secret_count + data_count
        if total == 0:
            return CheckResult("warn", f"0 registered on active profile '{profile_name}'")
        type_parts: List[str] = []
        if config_count:
            type_parts.append(f"config: {config_count}")
        if env_count:
            type_parts.append(f"env: {env_count}")
        if secret_count:
            type_parts.append(f"secret: {secret_count}")
        if data_count:
            type_parts.append(f"data: {data_count}")
        detail = f"{total} registered on active profile '{profile_name}' ({', '.join(type_parts)})"
        return CheckResult("ok", detail)

    def _check_build_exists(self) -> CheckResult:
        build_path = self._work_path / "build"
        if not build_path.exists():
            return CheckResult("pending")
        if not any(f for f in build_path.rglob("*") if f.is_file()):
            return CheckResult("warn", "directory is empty")
        return CheckResult("ok")

    def _check_sbom_exists(self) -> CheckResult:
        build_path = self._work_path / "build"
        if not build_path.exists():
            return CheckResult("pending")
        sbom_files = list(build_path.rglob("sbom.json"))
        if not sbom_files:
            return CheckResult("pending")
        total_components = 0
        for sbom_path in sbom_files:
            try:
                with open(sbom_path, encoding="utf-8") as fh:
                    sbom_data = json.load(fh)
                total_components += len(sbom_data.get("components", []))
            except Exception:
                pass
        if total_components > 0:
            return CheckResult("ok", f"{total_components} components")
        return CheckResult("warn", "sbom.json present but empty")

    # ------------------------------------------------------------------
    # Public: workflow-driven evaluation
    # ------------------------------------------------------------------

    def evaluate_from_workflow(self) -> List[ChecklistItem]:
        """Evaluate the workspace checklist driven by workflow.yaml steps.

        Converts workflow step check results to ChecklistItem objects, respecting
        depends_on ordering. Steps whose dependencies are not satisfied are shown
        as pending. Results are cached to ``self._checklist``.
        """
        if self._workflow is None:
            return self.evaluate()

        step_results: Dict[str, CheckStatus] = {}
        checklist: List[ChecklistItem] = []

        for idx, step in enumerate(self._workflow.steps):
            phase = idx + 1

            deps_satisfied = all(step_results.get(dep) == "ok" for dep in step.depends_on)
            if not deps_satisfied:
                checklist.append(ChecklistItem(phase, step.name, "pending", step_id=step.id))
                step_results[step.id] = "pending"
                continue

            check_fn = self._workflow_checks.get(step.check)
            if check_fn is None:
                self.logger.warning("Unknown check function in workflow", check=step.check, step=step.id)
                checklist.append(
                    ChecklistItem(phase, step.name, "warn", f"unknown check: {step.check}", step_id=step.id)
                )
                step_results[step.id] = "warn"
                continue

            result = check_fn()
            checklist.append(ChecklistItem(phase, step.name, result.status, result.detail, step_id=step.id))
            step_results[step.id] = result.status

        self._checklist = checklist
        return checklist

    def find_next_step_from_workflow(self) -> Optional[NextStepItem]:
        """Walk workflow steps; return first incomplete step whose dependencies are satisfied.

        Uses the cached checklist from ``evaluate_from_workflow()`` when available
        to avoid re-running check functions. Falls back to ``find_next_step()`` if
        no workflow is loaded.
        """
        if self._workflow is None:
            return self.find_next_step()

        steps = self._workflow.steps

        # Use the cached checklist when it's consistent with the current workflow
        # (same number of steps). Otherwise do a lightweight re-evaluation.
        if self._checklist and len(self._checklist) == len(steps):
            step_status: Dict[str, CheckStatus] = {
                item.step_id: item.status  # type: ignore[misc]
                for item in self._checklist
                if item.step_id is not None
            }
        else:
            step_status = self._evaluate_step_statuses()

        active_profile = self.active_profile_name
        for idx, step in enumerate(steps):
            status = step_status.get(step.id, "pending")
            if status == "ok":
                continue

            deps_satisfied = all(step_status.get(dep) == "ok" for dep in step.depends_on)
            if not deps_satisfied:
                continue  # skip — not yet actionable

            hint = self._get_dynamic_hint(step) or step.hint
            if active_profile and "{active}" in hint:
                hint = hint.replace("{active}", active_profile)

            return NextStepItem(
                phase=idx + 1,
                label=step.name,
                hint=hint,
                see_also=step.see_also,
                command=step.command,
            )

        return None

    # ------------------------------------------------------------------
    # Private: workflow step evaluation
    # ------------------------------------------------------------------

    def _evaluate_step_statuses(self) -> Dict[str, CheckStatus]:
        """Run check functions for all workflow steps; return step-id → status map.

        Respects ``depends_on`` ordering: a step whose dependency is not ``ok``
        is marked ``pending`` without running its check function.
        """
        if self._workflow is None:
            return {}
        results: Dict[str, CheckStatus] = {}
        for step in self._workflow.steps:
            deps_ok = all(results.get(dep) == "ok" for dep in step.depends_on)
            if not deps_ok:
                results[step.id] = "pending"
                continue
            check_fn = self._workflow_checks.get(step.check)
            if check_fn is None:
                self.logger.warning("Unknown check function in workflow", check=step.check, step=step.id)
                results[step.id] = "warn"
                continue
            results[step.id] = check_fn().status
        return results

    def _get_dynamic_hint(self, step: WorkflowStep) -> Optional[str]:
        """Return a runtime-generated hint for steps that need live workspace data.

        Returns ``None`` when no dynamic hint is available (caller falls back to
        ``step.hint`` from the workflow YAML).

        Currently handles:
        - ``repos_cloned``: generates ``git clone`` lines for missing repositories.
        """
        if step.check == "repos_cloned" and self._solution is not None:
            repos = self._solution.spec.repositories or []
            missing = [
                r
                for r in repos
                if not (Path(r.path) if Path(r.path).is_absolute() else self._work_path / r.path).exists()
            ]
            lines: List[str] = []
            for repo in missing:
                url_str = str(repo.url).strip() if repo.url else ""
                if url_str:
                    lines.append(f"git clone {repo.url} {repo.path}")
                else:
                    lines.append(f"# local repo not found: {repo.path}")
            if lines:
                return "\n".join(lines)
        return None
