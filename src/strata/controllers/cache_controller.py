"""Controller for the resolved-model cache (ADR-0026).

Wraps :class:`~strata.services.cache_service.CacheService` with the
deployment-loading and platform-resolution logic needed to warm, inspect,
clear, and export cache entries.  This is the integration point used by both
the ``strata cache`` command group and (in a follow-up) individual commands
such as ``build run``.

Cache-key scope: the deployment file, its workspace file, its top-level
environment files, its deployment-level configuration file references, its tenant
file (``tenants/<code>.yaml``, if any), and every file the workspace itself
references directly (providers, resources, modules, namespaces, firewalls, DNS
zones, networks) plus one level of recursion into namespace files' own
``spec.modules[].file`` references. Not covered: module ``source`` paths (app
code/templates, often a directory or glob — out of scope for hash-based cache-key
purposes). ``--refresh-cache`` remains the escape hatch for any gap here.
"""

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.builders.platform_builder import PlatformBuilder
from strata.controllers.base_controller import BaseController
from strata.controllers.repository_controller import RepositoryController
from strata.controllers.solution_controller import SolutionController
from strata.services.cache_service import CacheService, CacheStatus
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService
from strata.services.environment_service import EnvironmentService
from strata.services.namespace_service import NamespaceService
from strata.services.workspace_service import WorkspaceService
from strata.utils.config import DEFAULT_BUILD_PATH
from strata.utils.merge_provenance import MergeProvenance
from strata.utils.system import resolve_path

# NOTE on scope: a full resolve (warm) requires the same ConfigurationService
# (active profile's configfile_paths) that 'build run'/'build plan' require —
# load_deploy_services() depends on ConfigurationService.get_instance() being
# initialised and validated. This mirrors BaseBuildCommand._load_configuration_service.
#
# Remote checkout (RepositoryController.ensure_remote_refs) IS replicated in
# _load_deployment_full, gated by sync_remotes (default True) — matching build run's
# fidelity for explicit 'strata cache warm' invocations. It performs a real
# 'git checkout --detach <ref>' on gitops remotes, so callers that warm silently in
# the background (e.g. the VS Code extension's auto-warm-on-save) should pass
# sync_remotes=False to avoid surprising git operations outside of an explicit
# operator action.


class CacheController(BaseController):
    """Warm, inspect, clear, and export the resolved-model cache."""

    KIND = "deployment"
    # Second cache kind (ADR-0026 Path B): the merged EnvironmentModel + merge
    # provenance only — no workspace/provider/resource/module resolution. Serves
    # commands that only need declared variables/secrets/features (store type +
    # reference, never resolved values — see get_or_resolve_environment docstring).
    KIND_RESOLVED_ENVIRONMENT = "resolved_environment"

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = Path(work_path)
        self._cache = CacheService(self._work_path)
        self._solution_controller = SolutionController(self._work_path)
        # Loaded eagerly (mirrors BaseCommand._initialize_session) — needed by
        # get_repo_map()/get_active_profile()/get_deployments() below. A missing or
        # unreadable solution.json is not fatal here; callers surface the relevant
        # error only when an operation actually needs solution state.
        self._solution_controller.load()

    def collect_input_paths(self, deployment_service: DeploymentService) -> List[str]:
        """Public wrapper around ``_collect_input_paths`` for reuse by other callers

        (e.g. ``RunBuildCommand`` warming the cache with a model it already built).
        """
        return self._collect_input_paths(deployment_service)

    @property
    def cache(self) -> CacheService:
        return self._cache

    # ------------------------------------------------------------------
    # Deployment loading / resolution
    # ------------------------------------------------------------------

    def _load_deployment(self, file_path: str) -> Tuple[bool, Optional[DeploymentService]]:
        """Load and structurally (Phase 1) validate a deployment file.

        Sufficient for cache-key computation (see ``_collect_input_paths``) — does
        NOT load workspace/environment services. Use ``_load_deployment_full`` when
        an actual resolve (cache miss/warm) is required.
        """
        try:
            deployment_service = DeploymentService.load(str(file_path), validate=True)
        except Exception as exc:
            self._add_error(f"Failed to load deployment '{file_path}': {exc}")
            return False, None

        if not deployment_service.is_validated():
            errors = (
                deployment_service.get_validation_errors()
                if hasattr(deployment_service, "get_validation_errors")
                else []
            )
            self._add_error(f"Deployment '{file_path}' failed validation: {'; '.join(errors) or 'unknown error'}")
            return False, None

        return True, deployment_service

    def _load_configuration_service(self) -> Optional[ConfigurationService]:
        """Load ConfigurationService from the active profile's configfile_paths.

        Mirrors ``BaseBuildCommand._load_configuration_service``. Returns ``None``
        (with an error appended) when there is no initialised workspace, no active
        profile, or no configfile_paths resolve to existing files — the same
        preconditions ``build run`` enforces.
        """
        if self._solution_controller.solution is None:
            self._add_error("Cache warm requires an initialized workspace. Run `strata sln init` first.")
            return None

        profile, _ = self._solution_controller.get_active_profile()
        if profile is None:
            self._add_error("Cache warm requires an active profile. Run `strata profile activate <name>`.")
            return None

        configfile_paths = profile.configfile_paths or []
        if not configfile_paths:
            self._add_error(
                "Cache warm requires at least one configfile path on the active profile. "
                "Add one with `strata ref configfile add`."
            )
            return None

        repo_map = self._solution_controller.get_repo_map()
        resolved_paths = []
        for entry in configfile_paths:
            try:
                resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
            except ValueError:
                continue
            if resolved.exists():
                resolved_paths.append(str(resolved))

        if not resolved_paths:
            self._add_error("No configfile_paths resolved to existing files. Check your profile refs.")
            return None

        try:
            ConfigurationService.reset()
            config_svc = ConfigurationService.get_instance()
            success, load_errors = config_svc.load_from_paths(resolved_paths)
            if not success:
                self._add_error(f"Failed to load configuration: {'; '.join(load_errors)}")
                return None
            return config_svc
        except Exception as exc:
            self._add_error(f"Unexpected error loading configuration: {exc}")
            return None

    def _load_deployment_full(
        self, file_path: str, sync_remotes: bool = True
    ) -> Tuple[bool, Optional[DeploymentService]]:
        """Load a deployment and its related services (workspace, environments).

        This is the same pipeline ``build run``/``build plan`` use before calling
        ``PlatformBuilder.build()``. When *sync_remotes* is True (default), also
        replicates ``BaseBuildCommand``'s remote-checkout step
        (``RepositoryController.ensure_remote_refs``) so a warm has the same fidelity
        as a real build. Set ``sync_remotes=False`` for callers that must not perform
        git operations implicitly (see module-level NOTE on scope).
        """
        ok, deployment_service = self._load_deployment(file_path)
        if not ok or deployment_service is None:
            return False, None
        assert deployment_service is not None

        config_service = self._load_configuration_service()
        if config_service is None:
            return False, None
        config_model = config_service.model

        repo_map = self._solution_controller.get_repo_map()

        ok, errors = deployment_service.validate(
            configuration_model=config_model, work_path=str(self._work_path), repo_map=repo_map
        )
        if not ok:
            self._errors.extend(errors)
            return False, None

        if not deployment_service.load_deploy_services(str(self._work_path), repo_map=repo_map):
            self._errors.extend(deployment_service.get_validation_errors())
            return False, None

        ok, errors = deployment_service.validate_related_services()
        if not ok:
            self._errors.extend(errors)
            return False, None

        ok, errors = deployment_service.apply_environment_overrides()
        if not ok:
            critical = [e for e in errors if "skipped" not in e.lower()]
            if critical:
                self._errors.extend(critical)
                return False, None

        if sync_remotes:
            repo_controller = RepositoryController()
            checkout_ok, _resolved_refs = repo_controller.ensure_remote_refs(
                config_service=config_service,
                work_path=self._work_path,
                repo_map=repo_map,
            )
            if not checkout_ok:
                self._errors.extend(repo_controller.get_errors())
                return False, None

        return True, deployment_service

    def _collect_input_paths(self, deployment_service: DeploymentService) -> List[str]:
        """Best-effort collection of files that contributed to the resolved model.

        See module docstring for current scope. Reads paths directly off the Phase-1
        model — does not require workspace/environment services to be loaded, so this
        also works from ``_load_deployment`` alone (cheap path used by ``status``).
        """
        paths: List[str] = []

        if deployment_service.path:
            paths.append(str(Path(deployment_service.path).resolve()))

        repo_map: Dict[str, str] = {}
        try:
            repo_map = deployment_service._merged_repo_map(None)
        except Exception:
            pass

        model = getattr(deployment_service, "model", None)
        spec = getattr(model, "spec", None) if model else None

        workspace_ref = getattr(spec, "workspace", None) if spec else None
        workspace_path: Optional[Path] = None
        if workspace_ref is not None and getattr(workspace_ref, "file", None):
            try:
                workspace_path = Path(
                    resolve_path(str(self._work_path), workspace_ref.file, repo_map=repo_map)
                ).resolve()
                paths.append(str(workspace_path))
            except Exception as exc:
                self.logger.debug(
                    "Could not resolve workspace file for cache key", file=workspace_ref.file, error=str(exc)
                )

        environments = getattr(spec, "environments", None) if spec else None
        for ref in environments or []:
            try:
                resolved = resolve_path(str(self._work_path), ref.file, repo_map=repo_map)
                paths.append(str(Path(resolved).resolve()))
            except Exception as exc:
                self.logger.debug("Could not resolve environment file for cache key", file=ref.file, error=str(exc))

        configurations = getattr(spec, "configurations", None) if spec else None
        for cfg in configurations or []:
            try:
                resolved = resolve_path(str(self._work_path), cfg.file, repo_map=repo_map)
                paths.append(str(Path(resolved).resolve()))
            except Exception as exc:
                self.logger.debug("Could not resolve configuration file for cache key", file=cfg.file, error=str(exc))

        tenant_code = getattr(spec, "tenant", None) if spec else None
        if tenant_code:
            tenant_path = self._work_path / "tenants" / f"{tenant_code}.yaml"
            if tenant_path.exists():
                paths.append(str(tenant_path.resolve()))

        if workspace_path is not None:
            paths.extend(self._collect_workspace_input_paths(workspace_path, repo_map))

        return paths

    def _collect_workspace_input_paths(self, workspace_path: Path, repo_map: Dict[str, str]) -> List[str]:
        """Resolve every file a workspace directly references (providers, resources,
        modules, namespaces, firewalls, DNS zones, networks).

        Best-effort: a Phase-1-only load of the workspace file (no ConfigurationService
        needed), so this works even from the cheap ``status`` path. Any single
        unresolvable reference is skipped (logged at debug) rather than failing the
        whole collection.
        """
        paths: List[str] = []
        try:
            workspace_service = WorkspaceService.load(str(workspace_path), validate=True)
        except Exception as exc:
            self.logger.debug("Could not load workspace file for cache key", file=str(workspace_path), error=str(exc))
            return paths

        if not workspace_service.is_validated():
            return paths

        spec = getattr(workspace_service.model, "spec", None)
        if spec is None:
            return paths

        def _add(file_ref: Optional[str]) -> Optional[Path]:
            if not file_ref:
                return None
            try:
                resolved = Path(resolve_path(str(self._work_path), file_ref, repo_map=repo_map)).resolve()
                paths.append(str(resolved))
                return resolved
            except Exception as exc:
                self.logger.debug("Could not resolve workspace-referenced file", file=file_ref, error=str(exc))
                return None

        for provider in spec.providers or []:
            _add(getattr(provider, "file", None))
        for resource in spec.resources or []:
            _add(getattr(resource, "file", None))
            for mod in getattr(resource, "modules", None) or []:
                _add(getattr(mod, "file", None))
        for namespace in spec.namespaces or []:
            namespace_path = _add(getattr(namespace, "file", None))
            if namespace_path is not None:
                paths.extend(self._collect_namespace_input_paths(namespace_path, repo_map))
        for firewall in spec.firewalls or []:
            _add(getattr(firewall, "file", None))
        for dns_zone in spec.dns_zones or []:
            _add(getattr(dns_zone, "file", None))
        for network in spec.networks or []:
            _add(getattr(network, "file", None))

        return paths

    def _collect_namespace_input_paths(self, namespace_path: Path, repo_map: Dict[str, str]) -> List[str]:
        """Resolve every file a namespace itself references (``spec.modules[].file``).

        This is the one genuine "file referenced by a referenced file" case in the
        schema today — provider/resource/module/firewall/DNS/network models have no
        further nested file references of their own (module ``source`` points at app
        code/templates, often a directory or glob, which is out of scope for cache-key
        hashing). Best-effort, same Phase-1-only pattern as ``_collect_workspace_input_paths``.
        """
        paths: List[str] = []
        try:
            namespace_service = NamespaceService.load(str(namespace_path), validate=True)
        except Exception as exc:
            self.logger.debug("Could not load namespace file for cache key", file=str(namespace_path), error=str(exc))
            return paths

        if not namespace_service.is_validated():
            return paths

        spec = getattr(namespace_service.model, "spec", None)
        if spec is None:
            return paths

        for mod in getattr(spec, "modules", None) or []:
            file_ref = getattr(mod, "file", None)
            if not file_ref:
                continue
            try:
                resolved = Path(resolve_path(str(self._work_path), file_ref, repo_map=repo_map)).resolve()
                paths.append(str(resolved))
            except Exception as exc:
                self.logger.debug("Could not resolve namespace-referenced module file", file=file_ref, error=str(exc))

        return paths

    # ------------------------------------------------------------------
    # Resolved-environment cache (ADR-0026 Path B — no workspace load)
    # ------------------------------------------------------------------

    def _collect_environment_input_paths(self, deployment_service: DeploymentService) -> List[str]:
        """Cache-key scope for the ``resolved_environment`` kind.

        Deliberately narrower than ``_collect_input_paths``: the deployment file,
        its environment file(s), and its tenant file (if any) — the only inputs
        that affect the merged environment (variables/secrets/features/overrides).
        The workspace file and everything it references are irrelevant here and
        are NOT hashed, which is what makes this cache kind cheap to warm.
        """
        paths: List[str] = []

        if deployment_service.path:
            paths.append(str(Path(deployment_service.path).resolve()))

        repo_map: Dict[str, str] = {}
        try:
            repo_map = deployment_service._merged_repo_map(None)
        except Exception:
            pass

        model = getattr(deployment_service, "model", None)
        spec = getattr(model, "spec", None) if model else None

        for ref in getattr(spec, "environments", None) or []:
            try:
                resolved = resolve_path(str(self._work_path), ref.file, repo_map=repo_map)
                paths.append(str(Path(resolved).resolve()))
            except Exception as exc:
                self.logger.debug("Could not resolve environment file for cache key", file=ref.file, error=str(exc))

        tenant_code = getattr(spec, "tenant", None) if spec else None
        if tenant_code:
            tenant_path = self._work_path / "tenants" / f"{tenant_code}.yaml"
            if tenant_path.exists():
                paths.append(str(tenant_path.resolve()))

        return paths

    def _resolve_environment_snapshot(
        self, deployment_service: DeploymentService, repo_map: Dict[str, str]
    ) -> Dict[str, Any]:
        """Build the ``resolved_environment`` cache payload for *deployment_service*.

        Contains only declarative data derived from YAML: the merged environment
        model (meta, properties, custom, declared variables/secrets/features,
        overrides.remotes) plus merge provenance and a couple of deployment-level
        fields ``deploy show`` displays. Deliberately excludes anything
        workspace-derived (providers, resources, modules) — that's the whole point
        of this lighter cache kind.

        Caches only *declarations* (store type + reference), never resolved secret
        values — those always come from a live store call via ``ValueController``.
        This sidesteps the store-value-cache correctness/at-rest-encryption problem
        entirely (see ADR-0026 OQ-4): nothing sensitive is ever written to disk here.
        """
        if not deployment_service.load_environment_only(str(self._work_path), repo_map=repo_map):
            raise RuntimeError("; ".join(deployment_service.get_validation_errors()) or "environment resolution failed")

        deployment_model = deployment_service.model
        env_service = deployment_service._environment_service
        provenance = deployment_service.get_merge_provenance()

        workspace_path: Optional[str] = None
        workspace_ref = getattr(deployment_model.spec, "workspace", None) if deployment_model else None
        if workspace_ref is not None and getattr(workspace_ref, "file", None):
            try:
                merged_repo_map = deployment_service._merged_repo_map(None)
                workspace_path = str(
                    Path(resolve_path(str(self._work_path), workspace_ref.file, repo_map=merged_repo_map)).resolve()
                )
            except Exception as exc:
                self.logger.debug("Could not resolve workspace file for display", error=str(exc))

        stages: List[Dict[str, Any]] = []
        if deployment_model and deployment_model.spec.stages:
            stages = [s.model_dump(mode="json") for s in deployment_model.spec.stages]

        return {
            "deployment_name": deployment_service.get_name(),
            "stages": stages,
            "workspace_path": workspace_path,
            "environment": env_service.model.model_dump(mode="json") if env_service and env_service.model else None,
            "merge_provenance": asdict(provenance) if provenance is not None else None,
        }

    def get_or_resolve_environment(
        self,
        deployment_service: DeploymentService,
        repo_map: Dict[str, str],
        no_cache: bool = False,
        refresh_cache: bool = False,
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Return the ``resolved_environment`` snapshot for *deployment_service*.

        *deployment_service* must already be Phase-1/Phase-2 validated (the caller's
        normal deployment-loading flow) — this method never touches workspace or
        ``ConfigurationService`` state; it reuses whatever is already active via
        ``ConfigurationService.get_instance()``, exactly like a live
        ``load_deploy_services()`` call would.

        On a cache miss, *deployment_service* is mutated in place by the underlying
        ``load_environment_only()`` call (its ``_environment_service`` and
        ``_merge_provenance`` are populated) — the same side effect a live load
        would have. On a cache hit, *deployment_service* is left untouched; callers
        must call :meth:`apply_environment_snapshot` to populate it from the
        returned snapshot.

        Returns ``(success, snapshot, indicator)`` where ``indicator`` is one of
        ``"cached"``, ``"refreshed"``, or ``"no-cache"``. On failure, ``snapshot``
        is ``None`` and errors are available via ``get_errors()``.
        """
        name = deployment_service.get_name() or str(deployment_service.path)
        input_paths = self._collect_environment_input_paths(deployment_service)

        try:
            snapshot, indicator = self._cache.get_or_resolve(
                name=name,
                kind=self.KIND_RESOLVED_ENVIRONMENT,
                input_paths=input_paths,
                resolve_fn=lambda: self._resolve_environment_snapshot(deployment_service, repo_map),
                no_cache=no_cache,
                refresh_cache=refresh_cache,
            )
        except Exception as exc:
            self._add_error(f"Failed to resolve environment for '{name}': {exc}")
            return False, None, "error"

        return True, snapshot, indicator

    @staticmethod
    def apply_environment_snapshot(deployment_service: DeploymentService, snapshot: Dict[str, Any]) -> None:
        """Rehydrate *deployment_service* from a cached ``resolved_environment`` snapshot.

        Sets ``_environment_service`` and ``_merge_provenance`` exactly as a live
        ``load_environment_only()`` call would — command code that only calls
        ``get_environment_service()`` / ``get_merge_provenance()`` (``deploy show``,
        ``values list/get/resolve``, ``ValueController.resolve_values``) works
        unmodified against a cache hit. Does not touch the workspace service or
        ``deployment_service.model`` (both already valid from the caller's own
        Phase-1/Phase-2 load).
        """
        env_data = snapshot.get("environment")
        if env_data is not None:
            env_service = EnvironmentService(data=env_data)
            env_service.validate()
            deployment_service._environment_service = env_service
        else:
            deployment_service._environment_service = None

        provenance_data = snapshot.get("merge_provenance")
        deployment_service._merge_provenance = MergeProvenance(**provenance_data) if provenance_data else None

    def _resolve_platform_model(self, deployment_service: DeploymentService) -> Dict[str, Any]:
        """Build the platform artifact model in memory (no files written).

        *deployment_service* must come from ``_load_deployment_full`` — related
        services (workspace, environment) must already be loaded.
        """
        builder = PlatformBuilder(verbose=False)
        build_path = self._work_path / DEFAULT_BUILD_PATH
        ok = builder.build(
            deployment_service,
            work_path=self._work_path,
            build_path=build_path,
            dry_run=True,
            solution_controller=self._solution_controller,
        )
        if not ok or builder.last_platform_model is None:
            raise RuntimeError("; ".join(builder.get_errors()) or "platform model build failed")
        return builder.last_platform_model.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def warm(
        self, file_path: str, refresh_cache: bool = True, sync_remotes: bool = True
    ) -> Tuple[bool, str, List[str]]:
        """Resolve *file_path* and store the result in the cache.

        *sync_remotes* controls whether gitops remotes are checked out to their
        configured reference before resolving (matches ``build run`` fidelity when
        True). Set to False for silent/background warms that must not perform git
        operations (see module-level NOTE on scope).

        Returns ``(success, indicator, errors)`` where indicator is
        ``"cached"`` / ``"refreshed"`` / ``"no-cache"``.
        """
        ok, deployment_service = self._load_deployment_full(file_path, sync_remotes=sync_remotes)
        if not ok or deployment_service is None:
            return False, "error", self.get_errors()

        name = deployment_service.model.meta.name if deployment_service.model else file_path
        input_paths = self._collect_input_paths(deployment_service)

        try:
            resolved, indicator = self._cache.get_or_resolve(
                name=name,
                kind=self.KIND,
                input_paths=input_paths,
                resolve_fn=lambda: self._resolve_platform_model(deployment_service),
                no_cache=False,
                refresh_cache=refresh_cache,
            )
        except Exception as exc:
            self._add_error(f"Failed to resolve '{name}': {exc}")
            return False, "error", self.get_errors()

        return True, indicator, []

    def warm_all(self, sync_remotes: bool = True) -> Tuple[bool, List[Dict[str, str]], List[str]]:
        """Warm every deployment registered in the solution.

        Returns ``(success, rows, errors)`` where each row is
        ``{"name": ..., "indicator": ...}``.
        """
        deployments, errors = self._solution_controller.get_deployments()
        if errors:
            self._errors.extend(errors)
            return False, [], self.get_errors()

        rows: List[Dict[str, str]] = []
        overall_ok = True
        for entry in deployments:
            ok, indicator, warm_errors = self.warm(entry.path, refresh_cache=True, sync_remotes=sync_remotes)
            rows.append({"name": entry.name, "indicator": indicator if ok else "error"})
            if not ok:
                overall_ok = False
                self._errors.extend(warm_errors)

        return overall_ok, rows, self.get_errors()

    def status(self, file_path: Optional[str] = None) -> Tuple[bool, List[Dict[str, Any]], List[str]]:
        """Return cache status rows.

        With *file_path*, checks just that deployment (recomputing its cache key
        live). Without it, lists every entry currently in the cache database.
        """
        if file_path:
            ok, deployment_service = self._load_deployment(file_path)
            if not ok or deployment_service is None:
                return False, [], self.get_errors()
            name = deployment_service.model.meta.name if deployment_service.model else file_path
            input_paths = self._collect_input_paths(deployment_service)
            cache_key = self._cache.compute_cache_key(input_paths)
            status = self._cache.status(name, self.KIND, cache_key) if cache_key else CacheStatus.COLD
            return True, [{"name": name, "status": status}], []

        entries = self._cache.list_entries()
        rows = [
            {
                "name": e["name"],
                "kind": e["kind"],
                "written_at": e["written_at"],
                "size_bytes": e["size_bytes"],
                "strata_version": e["strata_version"],
            }
            for e in entries
        ]
        return True, rows, []

    def clear(self) -> Tuple[bool, List[str]]:
        """Remove every entry from the cache."""
        self._cache.invalidate_all()
        return True, []

    def export(self, output_path: str) -> Tuple[bool, List[str]]:
        """Write the full cache (decompressed) to *output_path* as JSON."""
        import json

        data = self._cache.export_json()
        try:
            Path(output_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            self._add_error(f"Failed to write export file '{output_path}': {exc}")
            return False, self.get_errors()
        return True, []


def warm_platform_model_best_effort(
    work_path: Path,
    deployment_service: Optional[DeploymentService],
    platform_model: Optional[Any],
    logger: Optional[Any] = None,
) -> None:
    """Warm the resolved-model cache with a ``PlatformArtifactModel`` a caller already
    has in memory (or just read off disk) — no re-resolution, no YAML re-parsing.

    Shared by any command that happens to already hold both a loaded
    ``DeploymentService`` and a ``PlatformArtifactModel`` (``build run``, ``build plan``
    via ``BaseBuildCommand._warm_model_cache``, and ``policy check`` which reads an
    existing ``platform.json`` off disk). Never raises — failures are logged at debug
    and otherwise swallowed, matching the ADR's "cache is a performance optimisation,
    never a source of truth" contract.
    """
    if deployment_service is None or platform_model is None:
        return
    try:
        controller = CacheController(work_path)
        name = deployment_service.model.meta.name if deployment_service.model else str(deployment_service.path)
        input_paths = controller.collect_input_paths(deployment_service)
        cache_key = controller.cache.compute_cache_key(input_paths)
        if cache_key is None:
            return
        controller.cache.warm(name, "deployment", cache_key, platform_model.model_dump(mode="json"), input_paths)
    except Exception as exc:
        if logger is not None:
            logger.debug("Cache warm skipped (non-fatal)", error=str(exc))
