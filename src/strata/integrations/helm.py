#!/usr/bin/env python3
"""Helm integration (ADR-0021 Phase 6) — an `InfraIntegration` over the `helm` CLI.

**"Thick", matching Phase 5's `TerraformIntegration`, not v1's own
`HelmIntegration`.** v1's `HelmIntegration` is nearly empty (`COMMAND`,
version parsing, `ensure_available()`) — all real argv assembly (`helm
upgrade --install --create-namespace --wait --atomic --timeout 5m ...`)
lives in `HelmDeployer` instead, calling `_run_integration()` directly.
Corrected here for the same reason Compose was: this class owns its argv,
matching Terraform's own class rather than v1's inconsistency.

Argv shapes (flag ordering) copied exactly from v1's real
`HelmDeployer.plan()`/`.apply()`/`.destroy()` — proven, not redesigned.

Chart reference resolution (`meta.yaml` parsing, OCI-vs-HTTP-vs-local,
repo aliasing) and value substitution (`${var:}/${secret:}/${feature:}`
tree-walking, ADR-0075) are deployer/build-layer concerns (ADR-0021 Phase 7)
— this class receives an already-resolved `chart` reference and an
already-resolved values file, same split Phase 5 drew for `TF_VAR_`
injection.

`prepare_namespace()` (ADR-0022 D6/D7) is the *build*-time half of that
split: it writes `values.yaml`/`meta.yaml` with `${var:}`/`${secret:}`/
`${feature:}` tokens still in place, verbatim — resolving them is `deploy
run`'s job, not `build run`'s (ADR-0023's value-substitution table), and no
`deploy run` exists yet.
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.utils.transport import CommandResult


class HelmIntegration(InfraIntegration):
    """Helm — installs/upgrades/uninstalls chart releases on Kubernetes."""

    TYPE = "helm"
    CAPABILITIES = frozenset({"container"})
    TRANSPORTS = frozenset({"cli"})
    COMMAND = "helm"
    VERSION_ARGS = ("version",)

    def parse_version(self, raw: str) -> str:
        """Extract `X.Y.Z` from `'version.BuildInfo{Version:"v3.14.0",...}'`."""
        match = re.search(r"v?(\d+\.\d+\.\d+)", raw)
        return match.group(1) if match else raw.strip()

    def prepare_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        resolved: ValueResolution,
    ) -> None:
        """Write one `values.yaml` + one `meta.yaml` per module (ADR-0022
        D6/D7) — Helm never merges, unlike Compose.

        `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` tokens inside a
        service's `environment[].value` are written verbatim, unresolved.
        Helm's own value substitution happens at *deploy* time — secrets
        via `--set-string` (never written to disk), variables/features via
        a rewritten values file (ADR-0023's value-substitution table) —
        never during `build run` (`resolved` is accepted for signature
        symmetry with every other `InfraIntegration` rendering method, but
        genuinely unused here for that reason).

        Each module's own `values.yaml`/`meta.yaml` land directly in its
        `ResolvedModule.source_path` — already computed and, for a
        git-based `source`, already populated with the module's chart
        directory by the orchestrator (`workload_controller.py`) before
        this is called.
        """
        del resolved
        for item in modules:
            values = _render_values(item.module)
            if values:
                (item.source_path / "values.yaml").write_text(
                    yaml.safe_dump(values, sort_keys=False, default_flow_style=False)
                )

            meta = _render_meta(namespace, item)
            (item.source_path / "meta.yaml").write_text(
                yaml.safe_dump(meta, sort_keys=False, default_flow_style=False)
            )

    def plan(
        self,
        path: Path,
        *,
        release: str | None = None,
        namespace: str | None = None,
        chart: str | None = None,
        version: str | None = None,
        timeout: int = 600,
        env: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`helm upgrade --dry-run --install --namespace ns -f path release chart [--version v]`.

        Raises:
            IntegrationError: `release`/`namespace`/`chart` were not all given.
                Keyword-only with defaults (not required) to stay
                override-compatible with `InfraIntegration.plan`'s
                `**kwargs: Any` signature.
        """
        release, namespace, chart = self._require(release=release, namespace=namespace, chart=chart)
        args = ["upgrade", "--dry-run", "--install", "--namespace", namespace, "-f", str(path), release, chart]
        if version:
            args.extend(["--version", version])
        return self.run(*args, cwd=path.parent, env=env, timeout=timeout)

    def deploy(
        self,
        path: Path,
        *,
        release: str | None = None,
        namespace: str | None = None,
        chart: str | None = None,
        version: str | None = None,
        create_namespace: bool = True,
        wait: bool = True,
        atomic: bool = True,
        deploy_timeout: str = "5m",
        timeout: int = 600,
        env: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`helm upgrade --install [--create-namespace] [--wait] [--atomic] --timeout T
        --namespace ns -f path release chart [--version v]`.

        Raises:
            IntegrationError: `release`/`namespace`/`chart` were not all given.
        """
        release, namespace, chart = self._require(release=release, namespace=namespace, chart=chart)
        args = ["upgrade", "--install"]
        if create_namespace:
            args.append("--create-namespace")
        if wait:
            args.append("--wait")
        if atomic:
            args.append("--atomic")
        args.extend(["--timeout", deploy_timeout, "--namespace", namespace, "-f", str(path), release, chart])
        if version:
            args.extend(["--version", version])
        return self.run(*args, cwd=path.parent, env=env, timeout=timeout)

    def destroy(
        self,
        path: Path,
        *,
        release: str | None = None,
        namespace: str | None = None,
        timeout: int = 300,
        env: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`helm uninstall --namespace ns release`.

        Raises:
            IntegrationError: `release`/`namespace` were not both given.
        """
        if release is None or namespace is None:
            raise IntegrationError(f"{self.name}: 'release' and 'namespace' are required to uninstall a release.")
        return self.run("uninstall", "--namespace", namespace, release, env=env, timeout=timeout)

    def _require(
        self, *, release: str | None, namespace: str | None, chart: str | None
    ) -> tuple[str, str, str]:
        """Narrow the three optional-but-really-required params, or raise naming
        exactly which ones are missing (not just "one of these three")."""
        pairs = [("release", release), ("namespace", namespace), ("chart", chart)]
        missing = [name for name, value in pairs if value is None]
        if missing:
            raise IntegrationError(f"{self.name}: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required.")
        assert release is not None and namespace is not None and chart is not None
        return release, namespace, chart

    # ------------------------------------------------------------------
    # extras beyond InfraIntegration — real v1 deployer steps
    # ------------------------------------------------------------------

    def lint(self, path: Path, *, chart: str, timeout: int = 60,
              env: Mapping[str, str] | None = None) -> CommandResult:
        """`helm lint -f path chart`. v1's `check` step."""
        return self.run("lint", "-f", str(path), chart, cwd=path.parent, env=env, timeout=timeout)

    def repo_update(self, *, timeout: int = 60, env: Mapping[str, str] | None = None) -> CommandResult:
        """`helm repo update`. v1's `setup` step (repo registration/aliasing is
        deployer-layer — this only refreshes already-added repos)."""
        return self.run("repo", "update", env=env, timeout=timeout)

    def get_manifest(self, *, release: str, namespace: str, timeout: int = 60,
                      env: Mapping[str, str] | None = None) -> CommandResult:
        """`helm get manifest --namespace ns release`. v1's `plan_destroy`/`show_plan` step."""
        return self.run("get", "manifest", "--namespace", namespace, release, env=env, timeout=timeout)

    def get_values(self, *, release: str, namespace: str, timeout: int = 60,
                   env: Mapping[str, str] | None = None) -> CommandResult:
        """`helm get values --namespace ns release`. v1's `output` step."""
        return self.run("get", "values", "--namespace", namespace, release, env=env, timeout=timeout)


# ----------------------------------------------------------------------
# prepare_namespace() rendering — pure, so it's testable without touching
# disk (same split `default_output()`/`prepare()` already draw).
# ----------------------------------------------------------------------


def _render_values(module: ModuleModel) -> dict[str, Any]:
    """Build the `values.yaml` payload for one Helm module.

    Adapted from v1's real `HelmBuilder._render_module_artifacts()`, ported
    to v2's schema: v1 spread a Value binding across four optional
    `value`/`var`/`secret`/`feature` fields on each environment entry; v2
    collapsed that into `ModuleServiceEnvironmentModel.value`, one string
    that may itself contain a `${var:}`/`${secret:}`/`${feature:}` token
    (ADR-0002) — so this only ever copies `.value` verbatim, never branches
    on which kind of reference it is.

    Returns `{}` when the module has neither `spec.services` nor
    `spec.configuration` — `prepare_namespace()` then skips writing the
    file entirely, so a local chart's own shipped `values.yaml` (copied in
    by `workload_controller.sync_module_source()`) is left untouched
    rather than overwritten with an empty document (matches v1's own
    "omit when there is nothing to render" rule).
    """
    module_name = str(module.meta.name)
    values: dict[str, Any] = {}

    for service in module.spec.services or []:
        service_name = str(service.name)
        entry_name = service_name if service_name == module_name else f"{module_name}-{service_name}"
        entry: dict[str, Any] = {}

        if service.environment:
            entry["env"] = {env.key: env.value for env in service.environment}

        if service.mounts:
            persistence: dict[str, Any] = {}
            for mount in service.mounts:
                if mount.storage_class is None:
                    continue
                pvc_key = str(mount.name) if mount.name is not None else "data"
                pvc_entry: dict[str, Any] = {
                    "storageClass": mount.storage_class,
                    "accessMode": mount.access_mode or "ReadWriteOnce",
                }
                if mount.storage_size:
                    pvc_entry["size"] = mount.storage_size
                persistence[pvc_key] = pvc_entry
            if persistence:
                entry["persistence"] = persistence

        if service.configuration:
            entry.update(service.configuration)

        values[entry_name] = entry

    if module.spec.configuration:
        values.update(module.spec.configuration)

    return values


def _render_meta(namespace: NamespaceModel, item: ResolvedModule) -> dict[str, Any]:
    """Build the `meta.yaml` payload for one Helm module.

    `releaseName` defaults to `item.reference.name`, not `module.meta.name`
    (v1's real default) — the same Module document can be attached to a
    namespace more than once under different reference names
    (`ModuleReferenceModel`'s own docstring), and two live Helm releases
    can never share a name, so the one identifier guaranteed unique per
    attachment (the reference name) is the only safe default; `module.meta.name`
    is not, and would silently collide if that ever happens.

    Chart coordinates (`chartName`/`chartVersion`/`chartRemote`) are
    included only for a chart-based `source` (`chart_name` set) — a
    registry chart has no local copy for a deploy-time `helm upgrade` to
    reference by path, so `meta.yaml` must carry enough for the deployer to
    pull it directly instead (mirrors v1's real "self-contained build
    artifact" reasoning). Omitted for a git-based (local chart) `source`,
    where `item.source_path` itself is the chart to deploy.
    """
    module = item.module
    meta: dict[str, Any] = {
        "releaseName": module.spec.release_name or item.reference.name,
        "namespace": module.spec.kubernetes_namespace or str(namespace.meta.name),
    }

    source = module.spec.source
    if source.chart_name is not None:
        meta["chartName"] = source.chart_name
        if source.chart_version is not None:
            meta["chartVersion"] = source.chart_version
        if source.remote is not None:
            meta["chartRemote"] = source.remote

    return meta
