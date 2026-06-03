"""Base class for deployment builders."""

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from strata.logger import get_logger
from strata.services.deployment_service import DeploymentService
from strata.utils.templater import TemplateProcessor

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController


class BaseBuilder(ABC):
    """Abstract base class for workspace builders."""

    def __init__(self, verbose: bool = False) -> None:
        self.logger = get_logger(self.__class__.__module__)
        self.verbose = verbose
        self._messages: List[str] = []
        self._errors: List[str] = []

    def has_errors(self) -> bool:
        return len(self._errors) > 0

    def has_messages(self) -> bool:
        return len(self._messages) > 0

    def get_messages(self) -> List[str]:
        return self._messages

    def drain_messages(self) -> List[str]:
        """Return accumulated messages and clear the internal list."""
        msgs = list(self._messages)
        self._messages.clear()
        return msgs

    def get_errors(self) -> List[str]:
        return self._errors

    @abstractmethod
    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Build the workspace according to the builder's logic."""
        raise NotImplementedError

    @abstractmethod
    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Hook executed before the build process starts."""
        raise NotImplementedError

    @abstractmethod
    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Hook executed after the build process completes."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Template substitution helpers (available to all builders)
    # ------------------------------------------------------------------

    def _build_template_context(self, deployment_service: DeploymentService) -> Dict[str, str]:
        """Build STRATA_* substitution context from deployment, workspace, and provider data.

        Keys follow the pattern ``STRATA_<SCOPE>_<NAME>_<FIELD>`` (uppercase).
        Example: provider ``xyz_dc_eu_fr`` with ``engine = hetznercloud/hcloud`` becomes
        ``STRATA_PROVIDER_XYZ_DC_EU_FR_ENGINE``.

        Available keys:
          STRATA_DEPLOYMENT_NAME
          STRATA_WORKSPACE_NAME
          STRATA_PROVIDER_{NAME}_ENGINE
          STRATA_PROVIDER_{NAME}_VERSION
          STRATA_PROVIDER_{NAME}_ORGANIZATION
          STRATA_PROVIDER_{NAME}_TYPE
          STRATA_PROVIDER_{NAME}_REGION
          STRATA_PROVIDER_{NAME}_LOCATION
        """
        ctx: Dict[str, str] = {}

        if deployment_service.model:
            ctx["STRATA_DEPLOYMENT_NAME"] = str(deployment_service.model.meta.name)

        ws_service = deployment_service.get_workspace_service()
        if ws_service and ws_service.model:
            ctx["STRATA_WORKSPACE_NAME"] = str(ws_service.model.meta.name)

            provider_services = deployment_service.get_provider_services() or {}
            for prov_svc in provider_services.values():
                if not prov_svc.model:
                    continue
                name_key = str(prov_svc.model.meta.name).upper().replace("-", "_")
                props = prov_svc.model.spec.properties
                prefix = f"STRATA_PROVIDER_{name_key}"
                for field in ("engine", "version", "organization", "type", "region", "location"):
                    val = getattr(props, field, None)
                    if val is not None:
                        ctx[f"{prefix}_{field.upper()}"] = str(val)

        self.logger.debug(
            "Built template context",
            keys=sorted(ctx.keys()),
        )
        return ctx

    def _apply_templates_to_dir(self, dest_dir: Path, context: Dict[str, str]) -> None:
        """Apply STRATA_* template substitution to all text files under *dest_dir*.

        Binary files and unreadable files are silently skipped.
        Only files whose content changes after rendering are written back.
        """
        if not context:
            return

        changed = 0
        for path in sorted(dest_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
                rendered = TemplateProcessor.render(content, context)
                if rendered != content:
                    path.write_text(rendered, encoding="utf-8")
                    changed += 1
                    self.logger.debug(
                        "Applied template substitution",
                        file=str(path.relative_to(dest_dir)),
                    )
            except (UnicodeDecodeError, PermissionError):
                pass  # skip binary or unreadable files

        if changed:
            self.logger.info(
                "Template substitution complete",
                files_changed=changed,
                directory=str(dest_dir),
            )

    def _apply_template_to_file(self, path: Path, context: Dict[str, str]) -> None:
        """Apply STRATA_* template substitution to a single text file.

        Binary files and unreadable files are silently skipped.
        """
        if not context:
            return
        try:
            content = path.read_text(encoding="utf-8")
            rendered = TemplateProcessor.render(content, context)
            if rendered != content:
                path.write_text(rendered, encoding="utf-8")
                self.logger.debug("Applied template substitution", file=str(path))
        except (UnicodeDecodeError, PermissionError):
            pass

    def _copy_module_files(
        self,
        files: List[Any],
        work_path: Path,
        dest_dir: Path,
        template_context: Dict[str, str],
        module_label: str,
        dry_run: bool = False,
    ) -> bool:
        """Copy extra module files (with glob and template support) into *dest_dir*.

        Each entry in *files* is a ``ModuleFileModel`` with ``source`` and ``target``
        fields.  ``source`` may contain glob characters (``*``, ``?``, ``[``).
        When it does, ``target`` must end with ``/`` to indicate a destination directory.
        Template substitution is applied to every copied text file.

        Args:
            files: List of ``ModuleFileModel`` instances describing what to copy.
            work_path: Workspace root used to resolve plain and ``@repo/`` paths.
            dest_dir: Root output directory for this module's build artifacts.
            template_context: STRATA_* substitution variables.
            module_label: Human-readable label used in error messages.
            dry_run: When True, log what would happen but skip all file I/O.

        Returns:
            bool: True on success, False when any file operation fails.
        """
        from strata.utils.system import resolve_path

        for file_spec in files:
            source: str = file_spec.source
            target: str = file_spec.target
            is_glob = any(c in source for c in ("*", "?", "["))

            try:
                if is_glob:
                    # Split source into base-dir part and glob pattern.
                    # Works for both plain paths and @repo/ references.
                    # Example: "@repo/services/traefik/*" → base="@repo/services/traefik", pattern="*"
                    # Example: "scripts/**/*.sh"           → base="scripts",                pattern="**/*.sh"
                    normalized = source.replace("\\", "/")
                    parts = normalized.split("/")
                    glob_idx = next(i for i, p in enumerate(parts) if any(c in p for c in ("*", "?", "[")))
                    base_ref = "/".join(parts[:glob_idx]) if glob_idx > 0 else "."
                    glob_pattern = "/".join(parts[glob_idx:])

                    if base_ref == ".":
                        base_dir = work_path
                    else:
                        base_dir = resolve_path(str(work_path), base_ref)

                    matched = sorted(f for f in base_dir.glob(glob_pattern) if f.is_file())

                    if not matched:
                        self._errors.append(f"{module_label}: glob '{source}' matched no files")
                        return False

                    target_dir = dest_dir / target.rstrip("/")

                    if dry_run:
                        if self.verbose:
                            for m in matched:
                                self._messages.append(f"[DRY-RUN] Would copy: {m} → {target_dir / m.name}")
                        continue

                    target_dir.mkdir(parents=True, exist_ok=True)
                    for m in matched:
                        dest_file = target_dir / m.name
                        shutil.copy2(m, dest_file)
                        self._apply_template_to_file(dest_file, template_context)
                        if self.verbose:
                            self._messages.append(f"Copied file: {m} → {dest_file}")

                else:
                    # Single file — resolve exact path
                    src_file = resolve_path(str(work_path), source)

                    if not src_file.exists():
                        self._errors.append(f"{module_label}: source file not found: '{src_file}'")
                        return False

                    if target.endswith("/"):
                        dest_file = dest_dir / target.rstrip("/") / src_file.name
                    else:
                        dest_file = dest_dir / target

                    if dry_run:
                        if self.verbose:
                            self._messages.append(f"[DRY-RUN] Would copy: {src_file} → {dest_file}")
                        continue

                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)
                    self._apply_template_to_file(dest_file, template_context)
                    if self.verbose:
                        self._messages.append(f"Copied file: {src_file} → {dest_file}")

            except (ValueError, Exception) as exc:
                self._errors.append(f"{module_label}: failed to copy '{source}': {exc}")
                return False

        return True
