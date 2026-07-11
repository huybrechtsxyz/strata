"""Business logic for the strata promote command group (ADR-0011 Phase 2)."""

from __future__ import annotations

import getpass
import json
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from strata.controllers.base_controller import BaseController
from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.promotion_record_model import (
    ActivityLogEventModel,
    ActivityLogModel,
    PromotionCommitModel,
    PromotionGateResultModel,
    PromotionOutcome,
    PromotionRecordMetaModel,
    PromotionRecordModel,
    PromotionRecordSpecModel,
    PromotionRecordTargetModel,
    PromotionRingWaveSummaryModel,
)
from strata.utils.config import SOLUTION_DIR, SOLUTION_FILE

_API_VERSION = "strata.huybrechts.xyz/v1"
_PROMOTIONS_DIR = "promotions"
_RECORDS_DIR = "records"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(s: str) -> str:
    """Convert to lowercase slug suitable for filenames."""
    return re.sub(r"[^a-z0-9._-]", "-", s.lower())


class PromoteController(BaseController):
    """Orchestrates all promotion operations.

    Methods
    -------
    run_start      — Initiate or advance a promotion wave.
    run_rollback   — Reverse a completed promotion.
    get_status     — List in-flight promotions from activity logs.
    get_matrix     — Version matrix across all rings.
    get_history    — Query completed promotion records.
    get_log        — Show activity log for a specific promotion.
    """

    # ── helpers ───────────────────────────────────────────────────────────────

    def _load_config_model(self, work_path: Path):
        """Load and return the ConfigurationModel, or None if unavailable."""
        from strata.models.configuration_model import ConfigurationModel

        config_file = work_path / ".strata" / "configuration.yaml"
        if not config_file.exists():
            # Try bare name at work_path root
            config_file = work_path / "configuration.yaml"
        if not config_file.exists():
            self._add_error(f"configuration.yaml not found under {work_path}. Run 'strata sln init' first.")
            return None
        try:
            raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            return ConfigurationModel.model_validate(raw)
        except Exception as exc:
            self._add_error(f"Failed to load configuration: {exc}")
            return None

    def _load_solution(self, work_path: Path):
        """Return SolutionModel loaded from solution.json, or None."""
        from strata.services.solution_service import SolutionService

        solution_path = work_path / SOLUTION_DIR / SOLUTION_FILE
        if not solution_path.exists():
            return None
        try:
            svc = SolutionService()
            return svc.load_from_json(solution_path)
        except Exception:
            return None

    def _promotions_dir(self, work_path: Path) -> Path:
        d = work_path / SOLUTION_DIR / _PROMOTIONS_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _records_dir(self, work_path: Path) -> Path:
        d = work_path / SOLUTION_DIR / _PROMOTIONS_DIR / _RECORDS_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _activity_log_path(self, work_path: Path, target: str, version: str, ring: str) -> Path:
        fname = f"{_slug(target)}-{_slug(version)}-{_slug(ring)}.yaml"
        return self._promotions_dir(work_path) / fname

    def _load_activity_log(self, path: Path) -> Optional[ActivityLogModel]:
        if not path.exists():
            return None
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            return ActivityLogModel.model_validate(raw)
        except Exception:
            return None

    def _save_activity_log(self, log: ActivityLogModel, path: Path) -> None:
        path.write_text(
            yaml.dump(log.model_dump(exclude_none=True), default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _append_event(self, log: ActivityLogModel, path: Path, event: ActivityLogEventModel) -> None:
        log.events.append(event)
        self._save_activity_log(log, path)

    def _lock_file_path(self, work_path: Path, ring: str) -> Path:
        return work_path / "versions" / f"{ring}.yaml"

    def _scoped_lock_file_path(self, work_path: Path, ring: str, scope_selector: str) -> Path:
        return work_path / "versions" / f"{ring}.{scope_selector}.yaml"

    def _read_lock_file(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_lock_file(self, path: Path, ring: str, pins: List[dict],
                          scope: Optional[str] = None, scope_selector: Optional[str] = None) -> None:
        """Write a version-lock YAML to path."""
        meta_name = f"{ring}.{scope_selector}" if scope_selector else ring
        spec: dict = {"ring": ring, "pins": pins}
        if scope:
            spec["scope"] = scope
        if scope_selector:
            spec["scope_selector"] = scope_selector
        doc = {
            "apiVersion": _API_VERSION,
            "kind": PlatformKind.VERSION_LOCK.value,
            "meta": {"name": meta_name},
            "spec": spec,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(doc, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _pin_version_in_lock(self, existing_pins: List[dict], target_type: str,
                              target_name: str, version: str) -> List[dict]:
        """Upsert a pin in an existing pins list. Returns the new list."""
        new_pins = [
            p for p in existing_pins
            if not (p.get("target", {}).get("type") == target_type
                    and p.get("target", {}).get("name") == target_name)
        ]
        new_pins.append({"target": {"type": target_type, "name": target_name}, "version": version})
        return new_pins

    def _get_current_pin_version(self, path: Path, target_type: str, target_name: str) -> Optional[str]:
        """Read the currently pinned version from a lock file, or None."""
        lock = self._read_lock_file(path)
        if not lock:
            return None
        for pin in lock.get("spec", {}).get("pins", []):
            t = pin.get("target", {})
            if t.get("type") == target_type and t.get("name") == target_name:
                return pin.get("version")
        return None

    # ── git helpers ───────────────────────────────────────────────────────────

    def _run_git(self, args: List[str], cwd: Path) -> Tuple[bool, str, str]:
        """Run a git command. Returns (success, stdout, stderr)."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=str(cwd),
                capture_output=True,
                text=True,
            )
            return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
        except FileNotFoundError:
            return False, "", "git not found in PATH"
        except Exception as exc:
            return False, "", str(exc)

    def _git_create_or_checkout_branch(self, branch: str, work_path: Path) -> Tuple[bool, str]:
        """Create branch if it doesn't exist, or checkout if it does."""
        ok, _, _ = self._run_git(["checkout", "-b", branch], work_path)
        if not ok:
            # Branch may already exist — try switching to it
            ok, out, err = self._run_git(["checkout", branch], work_path)
            if not ok:
                return False, f"Could not create or checkout branch '{branch}': {err}"
        return True, branch

    def _git_add_and_commit(self, files: List[Path], message: str,
                             work_path: Path) -> Tuple[bool, str]:
        """Stage files and commit. Returns (success, sha_or_error)."""
        for f in files:
            ok, _, err = self._run_git(["add", str(f)], work_path)
            if not ok:
                return False, f"git add failed for {f}: {err}"
        ok, _, err = self._run_git(["commit", "-m", message], work_path)
        if not ok:
            return False, f"git commit failed: {err}"
        ok, sha, _ = self._run_git(["rev-parse", "HEAD"], work_path)
        if not ok:
            sha = "unknown"
        return True, sha

    def _git_current_branch(self, work_path: Path) -> str:
        ok, branch, _ = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], work_path)
        return branch if ok else "unknown"

    def _git_merge_base_file_content(self, file_path: Path, work_path: Path) -> Optional[str]:
        """Read a file's content at the merge base of HEAD and main/master."""
        for base_branch in ("main", "master"):
            ok, base, _ = self._run_git(["merge-base", "HEAD", base_branch], work_path)
            if ok and base:
                rel = file_path.relative_to(work_path)
                ok2, content, _ = self._run_git(["show", f"{base}:{rel}"], work_path)
                if ok2:
                    return content
        return None

    # ── strategy / progression lookup ────────────────────────────────────────

    def _find_strategy_for_ring(self, config_model, ring_name: str):
        """Return (strategy, progression, ring_model) for the given ring, or (None, None, None)."""
        if not config_model or not config_model.spec or not config_model.spec.promotions:
            return None, None, None
        promotions = config_model.spec.promotions
        if not promotions.strategies or not promotions.progressions:
            return None, None, None
        prog_map = {p.name: p for p in promotions.progressions}
        for strategy in promotions.strategies:
            prog = prog_map.get(strategy.progression)
            if not prog:
                continue
            for ring in prog.rings:
                if ring.name == ring_name:
                    return strategy, prog, ring
        return None, None, None

    def _get_previous_ring(self, progression, ring_name: str):
        """Return the ring model that precedes ring_name in the progression, or None."""
        rings = progression.rings
        for i, ring in enumerate(rings):
            if ring.name == ring_name:
                if i == 0:
                    return None
                return rings[i - 1]
        return None

    # ── gate checking ─────────────────────────────────────────────────────────

    def _check_progression_order_gate(
        self, strategy, progression, ring_model, target_type: str, target_name: str,
        version: str, work_path: Path
    ) -> Tuple[bool, PromotionGateResultModel]:
        """Check that the previous ring's quorum is satisfied."""
        prev_ring = self._get_previous_ring(progression, ring_model.name)
        if prev_ring is None:
            # First ring — no gate to check
            result = PromotionGateResultModel(
                gate="require_progression_order",
                ring=ring_model.name,
                require=None,
                checked_at=_now_iso(),
                passed=True,
                detail="First ring — no inbound requirement",
            )
            return True, result

        require = prev_ring.require or "any_one"
        lock_path = self._lock_file_path(work_path, prev_ring.name)

        if not lock_path.exists():
            result = PromotionGateResultModel(
                gate="require_progression_order",
                ring=ring_model.name,
                require=require,
                checked_at=_now_iso(),
                passed=False,
                detail=f"Ring '{prev_ring.name}' has no lock file — version not yet promoted there",
            )
            return False, result

        pinned = self._get_current_pin_version(lock_path, target_type, target_name)
        if pinned != version:
            result = PromotionGateResultModel(
                gate="require_progression_order",
                ring=ring_model.name,
                require=require,
                checked_at=_now_iso(),
                passed=False,
                detail=(
                    f"Ring '{prev_ring.name}' quorum not satisfied ({require}): "
                    f"'{target_name}' is at '{pinned or '(not pinned)'}', need '{version}'"
                ),
            )
            return False, result

        result = PromotionGateResultModel(
            gate="require_progression_order",
            ring=ring_model.name,
            require=require,
            checked_at=_now_iso(),
            passed=True,
            detail=f"Ring '{prev_ring.name}' quorum satisfied ({require}): '{target_name}' has '{version}'",
        )
        return True, result

    # ── deployment discovery & wave assignment ────────────────────────────────

    def _load_registered_deployments(self, work_path: Path) -> List[Any]:
        """Return list of DeploymentModel for all solution-registered deployments."""
        from strata.services.deployment_service import DeploymentService

        solution = self._load_solution(work_path)
        if not solution or not solution.spec.deployments:
            return []
        models = []
        for entry in solution.spec.deployments:
            dep_path = Path(entry.path)
            if not dep_path.is_absolute():
                dep_path = work_path / dep_path
            try:
                svc = DeploymentService.load(str(dep_path))
                if svc.is_validated() and svc.model:
                    models.append(svc.model)
            except Exception:
                pass
        return models

    def _filter_deployments_by_environments(
        self, deployments: List[Any], target_env_names: List[str]
    ) -> List[Any]:
        """Return deployments that reference at least one of the target environments."""
        target_set = set(target_env_names)
        result = []
        for dep in deployments:
            env_names = self._deployment_env_names(dep)
            if env_names & target_set:
                result.append(dep)
        return result

    def _deployment_env_names(self, deployment) -> set:
        """Return set of environment names referenced by a deployment (filename stems)."""
        names = set()
        if not deployment.spec.environments:
            return names
        for env_ref in deployment.spec.environments:
            p = Path(env_ref.file)
            names.add(p.stem)
        return names

    def _assign_deployment_wave(self, deployment, strategy) -> int:
        """Return the 1-based wave index for this deployment (last wave = default)."""
        waves = strategy.waves or []
        n_waves = len(waves)
        if n_waves == 0:
            return 1

        promotion = getattr(deployment.spec, "promotion", None)
        if not promotion or not promotion.wave:
            return n_waves  # default: last wave

        wave_cfg = promotion.wave
        if wave_cfg.iteration is not None:
            return max(1, min(wave_cfg.iteration, n_waves))

        if wave_cfg.match_labels and deployment.meta.labels:
            dep_labels = dict(deployment.meta.labels) if deployment.meta.labels else {}
            for i, wave in enumerate(waves, start=1):
                # Deployment wave name is used as match key in this simplified impl
                if wave.name in dep_labels.get("tier", wave.name):
                    return i

        return n_waves

    def _scope_filter(self, deployments: List[Any], scope: Optional[str]) -> Tuple[List[Any], List[Any]]:
        """Split into (scoped_deployments, unscoped_deployments) based on strategy scope."""
        if not scope:
            return [], deployments
        scoped, unscoped = [], []
        for dep in deployments:
            layers = dict(dep.spec.layers) if dep.spec.layers else {}
            if scope in layers:
                scoped.append(dep)
            else:
                unscoped.append(dep)
        return scoped, unscoped

    def _get_scope_selector(self, deployment) -> str:
        """Return a selector string for a scoped deployment (tenant name or deployment name)."""
        layers = dict(deployment.spec.layers) if deployment.spec.layers else {}
        for val in layers.values():
            if val:
                return str(val)
        return str(deployment.meta.name)

    # ── ring wave environment filtering ──────────────────────────────────────

    def _filter_ring_environments_by_wave(self, ring_model, wave_int: Optional[int]) -> List[str]:
        """Return environment names for the given ring wave number (or all if None)."""
        from strata.models.promotion_model import ProgressionRingEnvironmentModel

        env_names = []
        for env in ring_model.environments:
            if isinstance(env, ProgressionRingEnvironmentModel):
                if wave_int is None or env.wave == wave_int or (wave_int == 1 and env.wave is None):
                    env_names.append(env.name)
        return env_names

    def _is_last_ring_wave(self, ring_model, wave_int: Optional[int], target_env_names: List[str]) -> bool:
        """True if target_env_names covers all environments in the ring."""
        all_names = ring_model.environment_names()
        return set(target_env_names) >= set(all_names)

    # ── run_start ─────────────────────────────────────────────────────────────

    def run_start(
        self,
        target_type: str,
        target_name: str,
        version: str,
        to_ring: str,
        wave: Optional[str],
        work_path: Path,
        dry_run: bool = False,
    ) -> dict:
        """Initiate or advance a promotion wave.

        Returns a result dict on success, {} on error.
        """
        # ── 1. load config ──────────────────────────────────────────────────
        config = self._load_config_model(work_path)
        if not config:
            return {}

        strategy, progression, ring_model = self._find_strategy_for_ring(config, to_ring)
        if not strategy:
            self._add_error(
                f"No promotion strategy found for ring '{to_ring}'. "
                "Ensure configuration.spec.promotions is configured."
            )
            return {}

        # ── 2. parse wave argument ──────────────────────────────────────────
        wave_int: Optional[int] = None
        wave_name: Optional[str] = None
        if wave is not None:
            try:
                wave_int = int(wave)
            except ValueError:
                wave_name = wave

        # ── 3. determine target environments ───────────────────────────────
        target_envs = self._filter_ring_environments_by_wave(ring_model, wave_int)
        if not target_envs:
            # No wave int specified or wave 1 — use all environments
            target_envs = ring_model.environment_names()
        if not target_envs:
            self._add_error(f"Ring '{to_ring}' has no environments defined.")
            return {}

        # ── 4. gate check ───────────────────────────────────────────────────
        gate_results: List[PromotionGateResultModel] = []
        if strategy.gates and strategy.gates.require_progression_order:
            gate_passed, gate_result = self._check_progression_order_gate(
                strategy, progression, ring_model, target_type, target_name, version, work_path
            )
            gate_results.append(gate_result)
            if not gate_passed:
                self._add_error(f"Gate 'require_progression_order' failed: {gate_result.detail}")
                return {}

        # ── 5. load and filter deployments ─────────────────────────────────
        all_deployments = self._load_registered_deployments(work_path)
        relevant_deployments = self._filter_deployments_by_environments(all_deployments, target_envs)
        scoped_deps, unscoped_deps = self._scope_filter(relevant_deployments, strategy.scope)

        # ── 6. determine lock files to write ───────────────────────────────
        is_last_wave = self._is_last_ring_wave(ring_model, wave_int, target_envs)
        ring_lock_path = self._lock_file_path(work_path, to_ring)

        # Previous version (from ring lock before this promotion)
        previous_version = self._get_current_pin_version(ring_lock_path, target_type, target_name)

        branch = f"promote/{_slug(target_name)}-{_slug(version)}-{_slug(to_ring)}"
        commit_message = (
            f"promote {target_name} {version} → {to_ring}"
            + (f" ring-wave {wave_int}" if wave_int else "")
            + (f" ({wave_name})" if wave_name else "")
        )

        # Build plan
        files_to_write: List[Tuple[Path, Optional[str], Optional[str]]] = []
        files_to_delete: List[Path] = []

        if is_last_wave:
            # Final wave: write ring lock, delete any scoped overlays
            files_to_write.append((ring_lock_path, None, None))
            # Find existing scoped overlays to delete
            versions_dir = work_path / "versions"
            if versions_dir.exists():
                for f in versions_dir.glob(f"{to_ring}.*.yaml"):
                    files_to_delete.append(f)
        elif scoped_deps:
            # Canary/early wave: write scoped overlays per unique selector
            selectors = {}
            for dep in scoped_deps:
                wave_idx = self._assign_deployment_wave(dep, strategy)
                if wave_int is None or wave_idx == wave_int:
                    sel = self._get_scope_selector(dep)
                    selectors[sel] = True
            for sel in selectors:
                files_to_write.append(
                    (self._scoped_lock_file_path(work_path, to_ring, sel), strategy.scope, sel)
                )
            if not files_to_write:
                # Fallback: write ring lock directly
                self._add_message("No scoped deployments matched — falling back to ring lock (all-at-once)")
                files_to_write.append((ring_lock_path, None, None))
        else:
            # No scope configured — all-at-once ring lock
            files_to_write.append((ring_lock_path, None, None))

        deployment_names = [str(d.meta.name) for d in relevant_deployments] or "all"

        plan = {
            "target_type": target_type,
            "target_name": target_name,
            "version": version,
            "previous_version": previous_version,
            "ring": to_ring,
            "environments": target_envs,
            "is_last_wave": is_last_wave,
            "ring_wave": wave_int or 1,
            "deployment_wave": wave_name,
            "deployments": deployment_names,
            "files_to_write": files_to_write,
            "files_to_delete": files_to_delete,
            "branch": branch,
            "commit_message": commit_message,
            "strategy": strategy,
            "progression": progression,
            "gate_results": gate_results,
        }

        if dry_run:
            return self._format_dry_run(plan)

        # ── 7. execute ──────────────────────────────────────────────────────
        return self._execute_start(plan, work_path)

    def _format_dry_run(self, plan: dict) -> dict:
        """Format a dry-run plan result dict."""
        write_paths = [str(p) for p, _, _ in plan["files_to_write"]]
        delete_paths = [str(p) for p in plan["files_to_delete"]]
        return {
            "dry_run": True,
            "branch": plan["branch"],
            "commit_message": plan["commit_message"],
            "ring": plan["ring"],
            "environments": plan["environments"],
            "deployments": plan["deployments"],
            "files_to_write": write_paths,
            "files_to_delete": delete_paths,
            "is_last_wave": plan["is_last_wave"],
            "gates": [g.model_dump() for g in plan["gate_results"]],
        }

    def _execute_start(self, plan: dict, work_path: Path) -> dict:
        """Execute a planned promotion: branch, write files, commit, log."""
        target_type = plan["target_type"]
        target_name = plan["target_name"]
        version = plan["version"]
        to_ring = plan["ring"]
        branch = plan["branch"]
        strategy = plan["strategy"]
        progression = plan["progression"]

        started_at = _now_iso()

        # ── git branch ──────────────────────────────────────────────────────
        ok, err = self._git_create_or_checkout_branch(branch, work_path)
        if not ok:
            self._add_error(err)
            return {}

        # ── update lock files ───────────────────────────────────────────────
        written_files: List[Path] = []
        for lock_path, scope, scope_selector in plan["files_to_write"]:
            existing_lock = self._read_lock_file(lock_path)
            existing_pins = existing_lock.get("spec", {}).get("pins", []) if existing_lock else []
            new_pins = self._pin_version_in_lock(existing_pins, target_type, target_name, version)
            self._write_lock_file(lock_path, to_ring, new_pins, scope, scope_selector)
            written_files.append(lock_path)

        # ── delete folded overlays ───────────────────────────────────────────
        removed_files: List[str] = []
        all_staged: List[Path] = list(written_files)
        for del_path in plan["files_to_delete"]:
            if del_path.exists():
                del_path.unlink()
                removed_files.append(str(del_path))
                all_staged.append(del_path)

        # ── git commit ──────────────────────────────────────────────────────
        ok, sha = self._git_add_and_commit(all_staged, plan["commit_message"], work_path)
        if not ok:
            self._add_error(sha)
            return {}

        committed_at = _now_iso()
        ring_wave_num = plan["ring_wave"]

        # ── activity log ────────────────────────────────────────────────────
        log_path = self._activity_log_path(work_path, target_name, version, to_ring)
        activity_log = self._load_activity_log(log_path) or ActivityLogModel(
            target=target_name,
            version=version,
            previous_version=plan["previous_version"],
            ring=to_ring,
            environments=plan["environments"],
            strategy=strategy.name,
            progression=progression.name,
            rings=progression.ring_names(),
            branch=branch,
        )
        activity_log.branch = branch
        self._append_event(
            activity_log,
            log_path,
            ActivityLogEventModel(
                timestamp=committed_at,
                action="committed",
                ring_wave=ring_wave_num,
                environments=plan["environments"],
                deployment_wave=plan["deployment_wave"],
                initiated_by=self._get_current_user(),
                deployments=plan["deployments"],
                files_modified=[str(f) for f in written_files],
                fields_removed=removed_files or None,
                commit=sha,
            ),
        )
        if plan["is_last_wave"]:
            self._append_event(
                activity_log,
                log_path,
                ActivityLogEventModel(timestamp=_now_iso(), action="completed", outcome="completed"),
            )

        # ── promotion record (last wave) ────────────────────────────────────
        record_path: Optional[str] = None
        if plan["is_last_wave"]:
            record_path = self._write_promotion_record(
                target_type=target_type,
                target_name=target_name,
                version=version,
                previous_version=plan["previous_version"],
                strategy=strategy,
                progression=progression,
                ring=to_ring,
                outcome=PromotionOutcome.COMPLETED,
                branch=branch,
                commits=[PromotionCommitModel(
                    ring_wave=ring_wave_num,
                    sha=sha,
                    message=plan["commit_message"],
                    committed_at=committed_at,
                )],
                gates=plan["gate_results"],
                ring_waves=[PromotionRingWaveSummaryModel(
                    ring_wave=ring_wave_num,
                    environments=plan["environments"],
                    deployment_wave=plan["deployment_wave"],
                    deployments=plan["deployments"],
                    files_modified=[str(f) for f in written_files],
                    fields_removed=removed_files or None,
                    committed_at=committed_at,
                )],
                started_at=started_at,
                completed_at=committed_at,
                rollback_of=None,
                work_path=work_path,
            )

        pr_hint = (
            f"gh pr create --head {branch} --title '{plan['commit_message']}' "
            f"--body 'Promotion: {target_name} → {to_ring}'"
        )

        return {
            "dry_run": False,
            "branch": branch,
            "ring": to_ring,
            "environments": plan["environments"],
            "deployments": plan["deployments"],
            "files_modified": [str(f) for f in written_files],
            "files_removed": removed_files,
            "commit_sha": sha,
            "commit_message": plan["commit_message"],
            "is_last_wave": plan["is_last_wave"],
            "promotion_record": record_path,
            "pr_suggestion": pr_hint,
        }

    # ── run_rollback ──────────────────────────────────────────────────────────

    def run_rollback(
        self,
        target_type: str,
        target_name: str,
        to_ring: str,
        from_version: Optional[str],
        work_path: Path,
        dry_run: bool = False,
    ) -> dict:
        """Reverse a promotion. Returns result dict or {} on error."""
        config = self._load_config_model(work_path)
        if not config:
            return {}

        strategy, progression, ring_model = self._find_strategy_for_ring(config, to_ring)
        if not strategy:
            self._add_error(f"No promotion strategy found for ring '{to_ring}'.")
            return {}

        # ── resolve previous version (3-tier fallback) ──────────────────────
        previous_version = from_version

        if not previous_version:
            # Tier 1: activity log
            ring_lock_path = self._lock_file_path(work_path, to_ring)
            current_version = self._get_current_pin_version(ring_lock_path, target_type, target_name)
            if current_version:
                log_path = self._activity_log_path(work_path, target_name, current_version, to_ring)
                log = self._load_activity_log(log_path)
                if log and log.previous_version:
                    previous_version = log.previous_version

        if not previous_version:
            # Tier 2: git merge base
            ring_lock_path = self._lock_file_path(work_path, to_ring)
            content = self._git_merge_base_file_content(ring_lock_path, work_path)
            if content:
                try:
                    old_lock = yaml.safe_load(content)
                    for pin in old_lock.get("spec", {}).get("pins", []):
                        t = pin.get("target", {})
                        if t.get("type") == target_type and t.get("name") == target_name:
                            previous_version = pin.get("version")
                            break
                except Exception:
                    pass

        if not previous_version:
            self._add_error(
                f"Could not determine previous version for '{target_name}' in ring '{to_ring}'. "
                "Use --from-version to specify it explicitly."
            )
            return {}

        current_version = self._get_current_pin_version(
            self._lock_file_path(work_path, to_ring), target_type, target_name
        )

        branch = f"rollback/{_slug(target_name)}-{_slug(to_ring)}"
        commit_message = f"rollback {target_name} {current_version} → {previous_version} in {to_ring}"

        if dry_run:
            return {
                "dry_run": True,
                "branch": branch,
                "commit_message": commit_message,
                "ring": to_ring,
                "target_name": target_name,
                "current_version": current_version,
                "rollback_to_version": previous_version,
            }

        # ── git branch & write ──────────────────────────────────────────────
        started_at = _now_iso()
        ok, err = self._git_create_or_checkout_branch(branch, work_path)
        if not ok:
            self._add_error(err)
            return {}

        ring_lock_path = self._lock_file_path(work_path, to_ring)
        existing_lock = self._read_lock_file(ring_lock_path)
        existing_pins = existing_lock.get("spec", {}).get("pins", []) if existing_lock else []
        new_pins = self._pin_version_in_lock(existing_pins, target_type, target_name, previous_version)
        self._write_lock_file(ring_lock_path, to_ring, new_pins)

        ok, sha = self._git_add_and_commit([ring_lock_path], commit_message, work_path)
        if not ok:
            self._add_error(sha)
            return {}

        committed_at = _now_iso()

        # ── promotion record ────────────────────────────────────────────────
        record_path = self._write_promotion_record(
            target_type=target_type,
            target_name=target_name,
            version=previous_version,
            previous_version=current_version,
            strategy=strategy,
            progression=progression,
            ring=to_ring,
            outcome=PromotionOutcome.ROLLED_BACK,
            branch=branch,
            commits=[PromotionCommitModel(
                ring_wave=1, sha=sha, message=commit_message, committed_at=committed_at
            )],
            gates=[],
            ring_waves=[PromotionRingWaveSummaryModel(
                ring_wave=1,
                environments=ring_model.environment_names(),
                deployment_wave=None,
                deployments="all",
                files_modified=[str(ring_lock_path)],
                committed_at=committed_at,
            )],
            started_at=started_at,
            completed_at=committed_at,
            rollback_of=None,
            work_path=work_path,
        )

        return {
            "dry_run": False,
            "branch": branch,
            "ring": to_ring,
            "target_name": target_name,
            "rolled_back_from": current_version,
            "rolled_back_to": previous_version,
            "commit_sha": sha,
            "promotion_record": record_path,
            "pr_suggestion": (
                f"gh pr create --head {branch} --title '{commit_message}' "
                f"--body 'Rollback: {target_name} in {to_ring}'"
            ),
        }

    # ── promotion record writing ──────────────────────────────────────────────

    def _write_promotion_record(
        self,
        target_type: str,
        target_name: str,
        version: str,
        previous_version: Optional[str],
        strategy,
        progression,
        ring: str,
        outcome: PromotionOutcome,
        branch: str,
        commits: List[PromotionCommitModel],
        gates: List[PromotionGateResultModel],
        ring_waves: List[PromotionRingWaveSummaryModel],
        started_at: str,
        completed_at: Optional[str],
        rollback_of: Optional[str],
        work_path: Path,
    ) -> str:
        """Write a promotion-record YAML to .strata/promotions/records/. Returns file path."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"prom-{ts}-{_slug(ring)}"
        hostname = socket.gethostname()
        initiated_by = self._get_current_user()

        duration: Optional[int] = None
        if completed_at and started_at:
            try:
                fmt = "%Y-%m-%dT%H:%M:%SZ"
                d = datetime.strptime(completed_at, fmt) - datetime.strptime(started_at, fmt)
                duration = int(d.total_seconds())
            except Exception:
                pass

        record = PromotionRecordModel(
            meta=PromotionRecordMetaModel(
                name=name,
                labels={"target": target_name, "ring": ring, "outcome": outcome.value},
            ),
            spec=PromotionRecordSpecModel(
                target=PromotionRecordTargetModel(
                    type=target_type,
                    name=target_name,
                    from_version=previous_version,
                    to_version=version,
                ),
                strategy=strategy.name,
                progression=progression.name,
                rings=progression.ring_names(),
                outcome=outcome,
                rollback_of=rollback_of,
                initiated_by=initiated_by,
                hostname=hostname,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                branch=branch,
                commits=commits,
                gates=gates,
                ring_waves=ring_waves,
                deployment_manifests=None,
            ),
        )

        records_dir = self._records_dir(work_path)
        record_path = records_dir / f"{name}.yaml"
        doc = json.loads(record.model_dump_json(exclude_none=True))
        record_path.write_text(
            yaml.dump(doc, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
        return str(record_path)

    # ── get_status ────────────────────────────────────────────────────────────

    def get_status(self, work_path: Path) -> List[dict]:
        """Return list of in-flight promotions from .strata/promotions/ activity logs."""
        promo_dir = work_path / SOLUTION_DIR / _PROMOTIONS_DIR
        if not promo_dir.exists():
            return []
        results = []
        for log_file in sorted(promo_dir.glob("*.yaml")):
            log = self._load_activity_log(log_file)
            if not log:
                continue
            # Determine status from last event
            last_action = log.events[-1].action if log.events else "unknown"
            if last_action in ("completed", "rolled_back"):
                status = last_action
            else:
                status = "in-progress"
            results.append({
                "target": log.target,
                "version": log.version,
                "previous_version": log.previous_version,
                "ring": log.ring,
                "strategy": log.strategy,
                "progression": log.progression,
                "branch": log.branch,
                "status": status,
                "event_count": len(log.events),
            })
        return results

    # ── get_matrix ────────────────────────────────────────────────────────────

    def get_matrix(self, work_path: Path, target_name: Optional[str] = None) -> dict:
        """Read versions/*.yaml lock files and build a version matrix per ring."""
        # Config is optional for matrix — missing config just means no ring ordering,
        # but we can still show whatever lock files exist.
        try:
            config = self._load_config_model(work_path)
            # Clear any config-load error — matrix is still useful without config.
            self._errors.clear()
        except Exception:
            config = None
        promotions = config.spec.promotions if config and config.spec else None
        progressions = {p.name: p for p in (promotions.progressions or [])} if promotions else {}

        versions_dir = work_path / "versions"
        if not versions_dir.exists():
            return {"rings": []}

        # Load all ring locks (not scoped overlays)
        ring_locks: Dict[str, dict] = {}
        for f in versions_dir.glob("*.yaml"):
            lock = self._read_lock_file(f)
            if not lock:
                continue
            spec = lock.get("spec", {})
            if spec.get("scope"):
                continue  # skip scoped overlays in matrix
            ring = spec.get("ring", f.stem)
            ring_locks[ring] = spec

        # Build matrix
        matrix_rings = []
        for prog in progressions.values():
            for ring in prog.rings:
                ring_data = ring_locks.get(ring.name, {})
                pins = ring_data.get("pins", [])
                version_map: Dict[str, str] = {}
                for pin in pins:
                    t = pin.get("target", {})
                    if target_name and t.get("name") != target_name:
                        continue
                    version_map[f"{t.get('type', '?')}/{t.get('name', '?')}"] = pin.get("version", "?")
                matrix_rings.append({
                    "ring": ring.name,
                    "environments": ring.environment_names(),
                    "require": ring.require,
                    "versions": version_map,
                })

        return {"rings": matrix_rings}

    # ── get_history ───────────────────────────────────────────────────────────

    def get_history(
        self,
        work_path: Path,
        ring: Optional[str] = None,
        target_name: Optional[str] = None,
        last: int = 10,
    ) -> List[dict]:
        """Return completed promotion records, newest first, filtered optionally."""
        records_dir = work_path / SOLUTION_DIR / _PROMOTIONS_DIR / _RECORDS_DIR
        if not records_dir.exists():
            return []
        entries = []
        for f in sorted(records_dir.glob("*.yaml"), reverse=True)[:last * 3]:
            try:
                raw = yaml.safe_load(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            spec = raw.get("spec", {})
            target = spec.get("target", {})
            if ring:
                meta_labels = raw.get("meta", {}).get("labels", {})
                if meta_labels.get("ring") != ring:
                    continue
            if target_name and target.get("name") != target_name:
                continue
            entries.append({
                "name": raw.get("meta", {}).get("name"),
                "target": f"{target.get('type', '?')}/{target.get('name', '?')}",
                "from_version": target.get("from_version"),
                "to_version": target.get("to_version"),
                "ring": raw.get("meta", {}).get("labels", {}).get("ring"),
                "outcome": spec.get("outcome"),
                "initiated_by": spec.get("initiated_by"),
                "started_at": spec.get("started_at"),
                "completed_at": spec.get("completed_at"),
                "branch": spec.get("branch"),
            })
            if len(entries) >= last:
                break
        return entries

    # ── get_log ───────────────────────────────────────────────────────────────

    def get_log(self, work_path: Path, target_name: str, to_ring: str, version: Optional[str] = None) -> Optional[dict]:
        """Return the raw activity log dict for a specific promotion."""
        if version:
            log_path = self._activity_log_path(work_path, target_name, version, to_ring)
            log = self._load_activity_log(log_path)
            if log:
                return log.model_dump(exclude_none=True)
            return None

        # Find the most recent log for this target+ring
        promo_dir = work_path / SOLUTION_DIR / _PROMOTIONS_DIR
        if not promo_dir.exists():
            return None
        pattern = f"{_slug(target_name)}-*-{_slug(to_ring)}.yaml"
        candidates = sorted(promo_dir.glob(pattern), reverse=True)
        for f in candidates:
            log = self._load_activity_log(f)
            if log:
                return log.model_dump(exclude_none=True)
        return None

    # ── misc ──────────────────────────────────────────────────────────────────

    def _get_current_user(self) -> str:
        try:
            return os.environ.get("CI_ACTOR") or os.environ.get("GITHUB_ACTOR") or getpass.getuser()
        except Exception:
            return "unknown"
