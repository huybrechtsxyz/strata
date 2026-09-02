"""Cross-manifest deployment scope overlap detection."""

from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

import yaml

from strata.controllers.base_controller import BaseController
from strata.logger import get_logger
from strata.models.common_models import ProvisionerType
from strata.models.namespace_model import NamespaceType
from strata.services.configuration_service import ConfigurationService
from strata.services.namespace_service import NamespaceService
from strata.services.workspace_service import WorkspaceService

logger = get_logger(__name__)


class OverlapError:
    """A single overlap collision found by the controller."""

    def __init__(self, check: int, message: str, files: List[str], is_warning: bool = False) -> None:
        self.check = check
        self.message = message
        self.files = files
        self.is_warning = is_warning

    def to_dict(self) -> dict:
        return {"check": self.check, "message": self.message, "files": self.files, "warning": self.is_warning}


class OverlapController(BaseController):
    """Orchestrate cross-manifest overlap checks for a set of deployment files.

    Checks performed:
      #1 — artifact_path + workspace uniqueness (all provisioners, CRITICAL)
      #2 — terraform state backend collision (terraform only, CRITICAL)
      #3 — namespace shared across layers (namespace-aware provisioners, WARNING)

    Usage::

        controller = OverlapController(configuration_service, repo_map, work_path)
        success = controller.run(manifest_paths)
        errors   = controller.get_overlap_errors()
        warnings = controller.get_overlap_warnings()
    """

    def __init__(self, configuration_service: Optional[ConfigurationService], repo_map: dict, work_path: Path) -> None:
        super().__init__()
        self._configuration_service = configuration_service
        self._repo_map = repo_map
        self._work_path = work_path
        self._overlap_errors: List[OverlapError] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, manifest_paths: List[Path]) -> bool:
        """Run all overlap checks against *manifest_paths*.

        Returns True when no critical overlaps were found (warnings don't fail).
        """
        self._overlap_errors.clear()

        if not manifest_paths:
            self.logger.debug("No manifests to check for overlap")
            return True

        # Fast pass — extract layers from raw YAML, compute artifact_paths
        fast_results = self._fast_pass(manifest_paths)

        # Check #1 — artifact_path + workspace uniqueness
        self._check_artifact_path_uniqueness(fast_results)

        # Check #2 — terraform state backend collision (deep pass, collisions only)
        self._check_terraform_backend(fast_results)

        # Check #3 — namespace overlap across layers (deep pass)
        self._check_namespace_overlap(fast_results)

        critical = [e for e in self._overlap_errors if not e.is_warning]
        return len(critical) == 0

    def get_overlap_errors(self) -> List[OverlapError]:
        """Return critical overlap errors (exit code 3)."""
        return [e for e in self._overlap_errors if not e.is_warning]

    def get_overlap_warnings(self) -> List[OverlapError]:
        """Return non-critical overlap warnings (exit code 0)."""
        return [e for e in self._overlap_errors if e.is_warning]

    # ------------------------------------------------------------------
    # Fast pass — raw YAML extraction
    # ------------------------------------------------------------------

    def _fast_pass(self, manifest_paths: List[Path]) -> List[dict]:
        """Load each manifest with yaml.safe_load, extract the fields we need.

        Returns a list of dicts with keys:
          path, layers, workspaces, artifact_path
        Skips files that cannot be parsed (adds a warning).
        """
        results = []
        config_model = self._configuration_service.model if self._configuration_service else None

        for path in manifest_paths:
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                self.logger.warning("Cannot parse manifest — skipping", path=str(path), error=str(exc))
                self._overlap_errors.append(
                    OverlapError(0, f"Cannot parse '{path.name}': {exc}", [str(path)], is_warning=True)
                )
                continue

            if raw.get("kind") != "deployment":
                continue

            spec = raw.get("spec") or {}
            layers_raw: Dict[str, Any] = spec.get("layers") or {}
            # workspace is a single object with a 'file' key
            ws_raw = spec.get("workspace") or {}
            ws_file: Optional[str] = None
            if isinstance(ws_raw, dict):
                ws_file = ws_raw.get("file")
            elif isinstance(ws_raw, str):
                ws_file = ws_raw

            artifact_path, resolved_keys = self._compute_artifact_path(layers_raw, config_model, str(path))

            # Check #3 groups manifests by "which layers identify this deployment's
            # scope". Prefer the RESOLVED segment names (ADR-0072 — includes values
            # derived from the file's own path, not just explicitly declared ones),
            # but fall back to the explicitly-declared segment names whenever
            # resolution yields nothing: no configuration loaded, no resolves: layers
            # convention declared, or an unresolvable/ambiguous match. Without this
            # fallback every manifest would collapse to an identical empty key set and
            # Check #3 would silently never fire — which is exactly what the raw
            # `spec.layers.keys()` proxy used to guarantee, config or no config.
            explicit_segments = layers_raw.get("segments") or {} if isinstance(layers_raw, dict) else {}
            layer_key_set = resolved_keys or frozenset(explicit_segments.keys())

            results.append(
                {
                    "path": path,
                    "layers": layers_raw,
                    "layer_key_set": layer_key_set,
                    "workspace_file": ws_file,
                    "artifact_path": artifact_path,
                    "configurations_raw": spec.get("configurations") or [],
                }
            )

        return results

    def _compute_artifact_path(
        self, layers_raw: Dict[str, Any], config_model, manifest_path: str = ""
    ) -> Tuple[str, FrozenSet[str]]:
        """Call the shared resolve_layers() from raw layer data (ADR-0072).

        Constructs a cheap ``LayersModel(**layers_raw)`` from the already-extracted
        raw dict — this controller deliberately avoids full ``DeploymentModel``
        validation across a fleet-wide scan for performance, but ``layers`` is a
        tiny sub-model (``follows`` + ``segments``), so validating just it is
        negligible next to validating the whole deployment graph (see ADR-0072's
        "Decided: resolve_layers() always takes a typed LayersModel").

        Returns ``(artifact_path, resolved_segment_names)``. An empty key set means
        "resolution produced nothing" (no config, no ``resolves: layers`` convention,
        or an unresolvable match) — callers must fall back to the explicitly-declared
        segment names rather than treating it as a real, comparable value.
        """
        from strata.models.deployment_model import LayersModel
        from strata.utils.path_convention import resolve_layers

        if not config_model or not layers_raw:
            return "", frozenset()

        try:
            layers_model = LayersModel(**layers_raw)
        except Exception:
            return "", frozenset()

        try:
            rel_path = Path(manifest_path).relative_to(self._work_path).as_posix()
        except (ValueError, TypeError):
            rel_path = Path(manifest_path).name

        resolution = resolve_layers(rel_path, layers_model, config_model.spec.paths or [])
        if resolution.error or resolution.convention is None:
            return "", frozenset()

        components: List[str] = []
        for segment in resolution.convention.segments or []:
            value = resolution.values.get(segment.name)
            if value is None:
                break  # "not applicable" — stop here, don't pad with later segments
            components.append(str(value))

        return "/".join(components), frozenset(resolution.values.keys())

    # ------------------------------------------------------------------
    # Check #1 — artifact_path + workspace uniqueness
    # ------------------------------------------------------------------

    def _check_artifact_path_uniqueness(self, fast_results: List[dict]) -> None:
        """Flag any (artifact_path, workspace_file) pair claimed by 2+ manifests."""
        key_to_files: Dict[Tuple[str, str], List[str]] = {}

        for item in fast_results:
            apath = item["artifact_path"]
            ws_file = item["workspace_file"]
            if not apath or not ws_file:
                continue
            key = (apath, ws_file)
            key_to_files.setdefault(key, []).append(str(item["path"]))

        for (apath, ws_file), files in key_to_files.items():
            if len(files) > 1:
                self._overlap_errors.append(
                    OverlapError(
                        check=1,
                        message=f"Artifact path '{apath}' + workspace '{ws_file}' claimed by multiple manifests",
                        files=files,
                        is_warning=False,
                    )
                )

    # ------------------------------------------------------------------
    # Check #2 — terraform state backend collision
    # ------------------------------------------------------------------

    def _check_terraform_backend(self, fast_results: List[dict]) -> None:
        """Flag manifests that share the same workspace + terraform backend type + artifact_path."""
        key_to_files: Dict[Tuple[str, str, str], List[str]] = {}

        for item in fast_results:
            apath = item["artifact_path"]
            ws_file = item["workspace_file"]
            if not apath or not ws_file:
                continue
            ws_path = self._resolve_ref(ws_file)
            if ws_path is None or not ws_path.exists():
                continue
            try:
                ws_svc = WorkspaceService.load(str(ws_path))
                ws_model = ws_svc.model
                if not ws_model:
                    continue
                for iac in ws_model.spec.provisioners or []:
                    if iac.provisioner != ProvisionerType.TERRAFORM:
                        continue
                    if not iac.backend:
                        continue
                    backend_type = str(iac.backend.type) if iac.backend.type else "unknown"
                    composite = (ws_file, backend_type, apath)
                    key_to_files.setdefault(composite, []).append(str(item["path"]))
            except Exception as exc:
                self.logger.debug("Cannot load workspace for backend check", ws=ws_file, error=str(exc))

        for (ws_file, btype, apath), files in key_to_files.items():
            if len(files) > 1:
                self._overlap_errors.append(
                    OverlapError(
                        check=2,
                        message=(
                            f"Terraform state backend collision: workspace '{ws_file}' "
                            f"backend '{btype}' artifact_path '{apath}'"
                        ),
                        files=files,
                        is_warning=False,
                    )
                )

    # ------------------------------------------------------------------
    # Check #3 — namespace overlap across layers
    # ------------------------------------------------------------------

    def _check_namespace_overlap(self, fast_results: List[dict]) -> None:
        """Warn when the same namespace name is claimed by manifests in different layers."""
        # ns_name → [(manifest_path_str, layer_key_set), ...]
        ns_owners: Dict[str, List[Tuple[str, FrozenSet[str]]]] = {}

        for item in fast_results:
            layer_key_set: FrozenSet[str] = item["layer_key_set"]
            ws_file = item["workspace_file"]
            if not ws_file:
                continue
            ws_path = self._resolve_ref(ws_file)
            if ws_path is None or not ws_path.exists():
                continue
            try:
                ws_svc = WorkspaceService.load(str(ws_path))
                ws_model = ws_svc.model
                if not ws_model:
                    continue
                for ns_ref in ws_model.spec.namespaces or []:
                    ns_file_path = self._resolve_ref(ns_ref.file)
                    if ns_file_path is None or not ns_file_path.exists():
                        continue
                    # Check if namespace declares itself as shared
                    try:
                        ns_svc = NamespaceService.load(str(ns_file_path))
                        ns_model = ns_svc.model
                        if ns_model and ns_model.spec.type == NamespaceType.SHARED:
                            continue  # intentionally shared — skip
                        ns_name = str(ns_ref.name)
                    except Exception:
                        ns_name = str(ns_ref.name)

                    ns_owners.setdefault(ns_name, []).append((str(item["path"]), layer_key_set))
            except Exception as exc:
                self.logger.debug("Cannot load workspace for namespace check", ws=ws_file, error=str(exc))

        for ns_name, owners in ns_owners.items():
            key_sets: Set[FrozenSet[str]] = {ks for _, ks in owners}
            if len(key_sets) > 1:
                self._overlap_errors.append(
                    OverlapError(
                        check=3,
                        message=(
                            f"Namespace '{ns_name}' is claimed by manifests in different layers. "
                            f"If intentional, set 'spec.type: shared' in the namespace file."
                        ),
                        files=[f for f, _ in owners],
                        is_warning=True,
                    )
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_ref(self, ref: str) -> Optional[Path]:
        """Resolve a file reference (plain path or @repo/path) to an absolute Path."""
        try:
            from strata.utils.system import resolve_path

            resolved = resolve_path(str(self._work_path), ref, repo_map=self._repo_map)
            return resolved
        except Exception:
            candidate = Path(ref)
            if not candidate.is_absolute():
                candidate = self._work_path / candidate
            return candidate.resolve()
