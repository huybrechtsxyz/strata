"""Command to display workspace setup progress and suggest next actions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import click
import yaml

from strata.commands.base_command import BaseCommand
from strata.exceptions.service_exception import PlatformFileNotFoundError
from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.solution_model import SolutionModel


@dataclass
class ChecklistItem:
    phase: int
    label: str
    status: Literal["ok", "warn", "pending"]
    detail: Optional[str] = None


@dataclass
class NextStepItem:
    phase: int
    label: str
    hint: str
    see_also: Optional[str] = None


class GuideCommand(BaseCommand):
    """Show setup progress and suggest the next action for this workspace."""

    OPERATION = "guide"
    INIT_REQUIRED = False

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._file = file

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=True)
                return True

            if not self._before_execute():
                self._finalize(success=True)
                return True

            self._run_execution()

            if not self._after_execute():
                self._finalize(success=True)
                return True

            self._finalize(success=True)
            return True

        except Exception as e:
            error_msg = f"Failed to show guide: {e}"
            self.logger.exception(error_msg)
            if self._is_console_output():
                click.echo(f"\n⚠️  Could not complete guide analysis: {e}")
            self._finalize(success=True)
            return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _run_execution(self) -> bool:
        try:
            solution, solution_exists = self._load_solution()
            if self._file is not None:
                self._run_file_mode(solution)
            else:
                self._run_workspace_mode(solution, solution_exists)
            return True
        except Exception as e:
            self.logger.warning("Guide execution error", error=str(e))
            if self._is_console_output():
                click.echo(f"\n⚠️  Could not complete guide analysis: {e}")
            return True

    # ------------------------------------------------------------------
    # Workspace mode
    # ------------------------------------------------------------------

    def _run_workspace_mode(self, solution: Optional[SolutionModel], solution_exists: bool) -> None:
        hints = self._load_hints()
        checklist = self._evaluate_checklist(solution, solution_exists)

        active_profile_name: Optional[str] = None
        if solution is not None:
            active_profile, _ = self._solution_controller.get_active_profile()
            if active_profile:
                active_profile_name = str(active_profile.name)

        next_step = self._find_next_step(checklist, solution, hints, active_profile_name)
        workspace_name = str(solution.meta.name) if solution is not None else None
        solution_id = self._solution_controller.get_solution_id() if solution is not None else None
        complete = next_step is None

        if self._is_quiet():
            return

        if self._is_console_output():
            self._render_console(checklist, next_step, workspace_name, hints)
        else:
            self._output_data = self._render_json(checklist, next_step, workspace_name, solution_id, complete)

    # ------------------------------------------------------------------
    # File mode
    # ------------------------------------------------------------------

    def _run_file_mode(self, solution: Optional[SolutionModel]) -> None:
        hints = self._load_hints()

        try:
            resolved_path = self._resolve_file_path(self._file, solution)  # type: ignore[arg-type]
        except ValueError as e:
            if not self._is_quiet() and self._is_console_output():
                click.echo(f"\n⚠️  {e}")
            return

        checklist, detected_kind, detected_name = self._evaluate_file_checklist(resolved_path)

        active_profile_name: Optional[str] = None
        if solution is not None:
            active_profile, _ = self._solution_controller.get_active_profile()
            if active_profile:
                active_profile_name = str(active_profile.name)

        next_steps = self._find_file_next_steps(detected_kind, active_profile_name, resolved_path, hints)
        workspace_name = str(solution.meta.name) if solution is not None else None
        solution_id = self._solution_controller.get_solution_id() if solution is not None else None

        if self._is_quiet():
            return

        if self._is_console_output():
            self._render_file_console(checklist, next_steps, resolved_path, detected_kind, workspace_name)
        else:
            self._output_data = self._render_file_json(
                checklist, next_steps, resolved_path, detected_kind, detected_name, workspace_name, solution_id
            )

    # ------------------------------------------------------------------
    # Solution loading
    # ------------------------------------------------------------------

    def _load_solution(self) -> tuple[Optional[SolutionModel], bool]:
        """Return (solution_or_None, solution_file_exists_on_disk)."""
        solution_path = self._work_path / ".strata" / "solution.json"
        if not solution_path.exists():
            return None, False
        # File exists — _initialize() already attempted to load it via the controller
        return self._solution_controller.solution, True

    # ------------------------------------------------------------------
    # Workspace checklist evaluation
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

        # Phase 2 — Repositories registered (blocked when solution is None)
        if solution is None:
            checklist.append(ChecklistItem(2, "Repositories registered", "pending"))
        else:
            repos = solution.spec.repositories or []
            if not repos:
                checklist.append(ChecklistItem(2, "Repositories registered", "pending"))
            else:
                checklist.append(ChecklistItem(2, "Repositories registered", "ok", str(len(repos))))
        phase2 = checklist[1]

        # Phase 3 — Repositories on disk (blocked by phase 2 ⬜)
        if phase2.status == "pending":
            checklist.append(ChecklistItem(3, "Repositories on disk", "pending"))
        else:
            repos = (solution.spec.repositories or []) if solution else []  # type: ignore[union-attr]
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
        phase3_unused = checklist[2]  # noqa: F841 — kept for symmetry

        # Phase 4 — Profile created (blocked when solution is None)
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

        # Phase 5 — Profile activated (blocked by phase 4 ⬜)
        if phase4.status == "pending":
            checklist.append(ChecklistItem(5, "Profile activated", "pending"))
        else:
            profiles = (solution.spec.profiles or []) if solution else []  # type: ignore[union-attr]
            active_list = [p for p in profiles if p.active]
            if not active_list:
                checklist.append(ChecklistItem(5, "Profile activated", "pending"))
            else:
                checklist.append(ChecklistItem(5, "Profile activated", "ok", str(active_list[0].name)))
        phase5 = checklist[4]

        # Phase 6 — File references registered (blocked by phase 5 ⬜)
        if phase5.status == "pending":
            checklist.append(ChecklistItem(6, "File references registered", "pending"))
        else:
            profiles = (solution.spec.profiles or []) if solution else []  # type: ignore[union-attr]
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

        # Phase 7 — Build artifact exists (no prerequisite)
        build_path = self._work_path / "build"
        if not build_path.exists():
            checklist.append(ChecklistItem(7, "Build artifact exists", "pending"))
        elif not any(f for f in build_path.rglob("*") if f.is_file()):
            checklist.append(ChecklistItem(7, "Build artifact exists", "warn", "directory is empty"))
        else:
            checklist.append(ChecklistItem(7, "Build artifact exists", "ok"))
        phase7 = checklist[6]

        # Phase 8 — Platform inventory generated (blocked when build does not exist)
        if phase7.status == "pending":
            checklist.append(ChecklistItem(8, "Platform inventory generated", "pending"))
        else:
            sbom_files = list(build_path.rglob("sbom.json")) if build_path.exists() else []
            if not sbom_files:
                checklist.append(ChecklistItem(8, "Platform inventory generated", "pending"))
            else:
                # Count components across all found sbom.json files (sum for multi-deployment workspaces)
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
    # Next step selection — workspace mode
    # ------------------------------------------------------------------

    def _find_next_step(
        self,
        checklist: List[ChecklistItem],
        solution: Optional[SolutionModel],
        hints: dict,
        active_profile_name: Optional[str],
    ) -> Optional[NextStepItem]:
        """Return a NextStepItem for the first non-ok phase, or None when all are ok."""
        phases = hints.get("phases", {}) or {}
        phase_labels: Dict[int, str] = {
            1: "Workspace initialized",
            2: "Repositories registered",
            3: "Repositories on disk",
            4: "Profile created",
            5: "Profile activated",
            6: "File references registered",
            7: "Build artifact exists",
            8: "Platform inventory generated",
        }

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
                label=phase_labels.get(item.phase, item.label),
                hint=hint,
                see_also=see_also,
            )

        return None  # all phases ok

    # ------------------------------------------------------------------
    # Hints loading
    # ------------------------------------------------------------------

    def _load_hints(self) -> dict:
        """Load built-in hints then shallow-merge project overrides from .strata/guide.yaml."""
        hints_path = Path(__file__).parent.parent.parent / "data" / "guide-hints.yaml"
        if not hints_path.exists():
            raise PlatformFileNotFoundError(str(hints_path), file_type="guide-hints.yaml")

        with open(hints_path, "r", encoding="utf-8") as fh:
            hints: dict = yaml.safe_load(fh) or {}

        override_path = self._work_path / ".strata" / "guide.yaml"
        if not override_path.exists():
            return hints

        try:
            with open(override_path, "r", encoding="utf-8") as fh:
                overrides: dict = yaml.safe_load(fh) or {}

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
    # Token substitution
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
    # Console rendering — workspace mode
    # ------------------------------------------------------------------

    def _render_console(
        self,
        checklist: List[ChecklistItem],
        next_step: Optional[NextStepItem],
        workspace_name: Optional[str],
        hints: dict,
    ) -> None:
        if workspace_name:
            click.echo(f"\nWorkspace: {workspace_name}  ({self._work_path})")
        else:
            click.echo(f"\nWorkspace: (uninitialized)  ({self._work_path})")

        phase1 = checklist[0]
        # When workspace not initialized, show only phase 1 + next step (reduce noise)
        if phase1.status == "pending":
            click.echo("\nSetup progress:\n")
            click.echo(f"  ⬜ {phase1.label}")
            click.echo("")
            if next_step:
                self._render_next_step_console(next_step)
            return

        click.echo("\nSetup progress:\n")
        for item in checklist:
            suffix = f" ({item.detail})" if item.detail else ""
            if item.status == "ok":
                click.echo(f"  ✅ {item.label}{suffix}")
            elif item.status == "warn":
                click.echo(f"  ⚠️  {item.label}{suffix}")
            else:
                click.echo(f"  ⬜ {item.label}{suffix}")
        click.echo("")

        if next_step:
            self._render_next_step_console(next_step)
        else:
            complete_msg = hints.get("complete") or "All setup phases complete. Your workspace is ready to deploy."
            click.echo(f"→ {complete_msg}")
            click.echo("")

    def _render_next_step_console(self, next_step: NextStepItem) -> None:
        click.echo("→ Next step:")
        click.echo("")
        for line in next_step.hint.splitlines():
            click.echo(f"   {line}")
        if next_step.see_also:
            click.echo("")
            click.echo(f"   See: {next_step.see_also}")
        click.echo("")

    # ------------------------------------------------------------------
    # JSON rendering — workspace mode
    # ------------------------------------------------------------------

    def _render_json(
        self,
        checklist: List[ChecklistItem],
        next_step: Optional[NextStepItem],
        workspace_name: Optional[str],
        solution_id: Optional[str],
        complete: bool,
    ) -> dict:
        next_steps: List[Dict[str, Any]] = []
        if next_step:
            next_steps.append(
                {
                    "phase": next_step.phase,
                    "label": next_step.label,
                    "hint": next_step.hint,
                    "see_also": next_step.see_also,
                }
            )
        return {
            "workspace": {
                "name": workspace_name,
                "path": str(self._work_path),
                "solution_id": solution_id or None,
            },
            "checklist": [
                {
                    "phase": item.phase,
                    "label": item.label,
                    "status": item.status,
                    "detail": item.detail,
                }
                for item in checklist
            ],
            "next_steps": next_steps,
            "complete": complete,
        }

    # ------------------------------------------------------------------
    # File mode: path resolution
    # ------------------------------------------------------------------

    def _resolve_file_path(self, raw: str, solution: Optional[SolutionModel]) -> Path:
        """Resolve relative, absolute, and @repo/ paths."""
        if raw.startswith("@"):
            parts = raw[1:].split("/", 1)
            repo_name = parts[0]
            rel = parts[1] if len(parts) > 1 else ""
            if solution is None:
                raise ValueError("@repo reference requires an initialized workspace")
            repo_map = self._solution_controller.get_repo_map()
            if repo_name not in repo_map:
                raise ValueError(f"Repository '{repo_name}' not found in solution")
            return Path(repo_map[repo_name]) / rel
        path = Path(raw)
        if path.is_absolute():
            return path
        return Path.cwd() / path

    # ------------------------------------------------------------------
    # File mode: checklist evaluation
    # ------------------------------------------------------------------

    def _evaluate_file_checklist(self, path: Path) -> tuple[List[ChecklistItem], Optional[str], Optional[str]]:
        """Evaluate file inspection phases. Returns (checklist, detected_kind, detected_name)."""
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
        expected = PlatformVersion.v1.value
        if api_version is None:
            checklist.append(ChecklistItem(3, "apiVersion present", "pending"))
        elif api_version == expected:
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
    # File mode: next steps
    # ------------------------------------------------------------------

    def _find_file_next_steps(
        self,
        kind: Optional[str],
        active_profile: Optional[str],
        resolved_path: Path,
        hints: dict,
    ) -> List[NextStepItem]:
        next_steps: List[NextStepItem] = []

        # Always include validate step
        validate_hint = f"strata validate -f {resolved_path}"
        next_steps.append(NextStepItem(phase=0, label="Validate", hint=validate_hint, see_also=None))

        if kind is None:
            # Unknown or missing kind — show kind list
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
    # File mode: console rendering
    # ------------------------------------------------------------------

    def _render_file_console(
        self,
        checklist: List[ChecklistItem],
        next_steps: List[NextStepItem],
        file_path: Path,
        kind: Optional[str],
        workspace_name: Optional[str],
    ) -> None:
        kind_str = f"kind: {kind}" if kind else "kind: unknown"
        click.echo(f"\nFile: {file_path}  ({kind_str})")
        if workspace_name:
            click.echo(f"Workspace: {workspace_name}  ({self._work_path})")

        click.echo("\nFile structure:\n")
        for item in checklist:
            if item.status == "ok":
                # For kind/apiVersion/name ok cases, render as "Key: value"
                display = self._format_file_item_ok(item)
                click.echo(f"  ✅ {display}")
            elif item.status == "warn":
                if item.detail:
                    click.echo(f"  ⚠️  {item.label}: {item.detail}")
                else:
                    click.echo(f"  ⚠️  {item.label}")
            else:
                if item.detail:
                    click.echo(f"  ⬜ {item.label} — {item.detail}")
                else:
                    click.echo(f"  ⬜ {item.label}")

        click.echo("")
        for ns in next_steps:
            click.echo(f"→ {ns.label}:")
            click.echo("")
            for line in ns.hint.splitlines():
                click.echo(f"   {line}")
            if ns.see_also:
                click.echo("")
                click.echo(f"   See: {ns.see_also}")
            click.echo("")

    @staticmethod
    def _format_file_item_ok(item: ChecklistItem) -> str:
        """Format an ok file checklist item for console (shows 'Key: value' for value phases)."""
        key_map = {
            2: "Kind",
            3: "apiVersion",
            4: "Name",
        }
        prefix = key_map.get(item.phase)
        if prefix and item.detail:
            return f"{prefix}: {item.detail}"
        return item.label

    # ------------------------------------------------------------------
    # File mode: JSON rendering
    # ------------------------------------------------------------------

    def _render_file_json(
        self,
        checklist: List[ChecklistItem],
        next_steps: List[NextStepItem],
        file_path: Path,
        kind: Optional[str],
        name: Optional[str],
        workspace_name: Optional[str],
        solution_id: Optional[str],
    ) -> dict:
        return {
            "file": {
                "path": str(file_path),
                "kind": kind,
                "name": name,
            },
            "workspace": {
                "name": workspace_name,
                "path": str(self._work_path),
                "solution_id": solution_id or None,
            },
            "checklist": [
                {
                    "phase": item.phase,
                    "label": item.label,
                    "status": item.status,
                    "detail": item.detail,
                }
                for item in checklist
            ],
            "next_steps": [
                {
                    "action": ns.label.lower(),
                    "hint": ns.hint,
                    "see_also": ns.see_also,
                }
                for ns in next_steps
            ],
        }
