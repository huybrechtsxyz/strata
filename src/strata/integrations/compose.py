#!/usr/bin/env python3
"""Docker Compose integration (ADR-0021 Phase 6) — an `InfraIntegration` over
Docker Swarm's `stack` subcommands.

**The real tool is `docker stack`, not `docker-compose`/`docker compose`.**
v1 evidence (`ComposeDeployer`) confirms production usage deploys via Swarm
mode (`docker stack deploy -c file.yml namespace`), never the standalone
Compose CLI — matched here rather than invented.

**"Thick", matching Phase 5's `TerraformIntegration`, not v1's own
`DockerIntegration`.** v1's `DockerIntegration` is nearly empty (`COMMAND`,
version parsing, `ensure_available()`) — all real argv assembly
(`docker stack deploy --with-registry-auth -c file.yml namespace`) lives in
`ComposeDeployer` instead, calling `_run_integration()` directly. That's an
inconsistency in v1 itself (Terraform's own integration class *does* own its
argv), not a pattern worth repeating — this class owns its argv the same
way `TerraformIntegration` does.

**`plan()` has no true dry-run, honestly.** v1's own comment on the `plan`
step admits it: "list namespaces and service counts that would be deployed
(no true dry-run)". `docker stack config` is the closest real analog —
merges and renders the compose file without deploying anything — but it is
weaker than Terraform's plan (no diff, no per-resource preview). It also
takes no stack/namespace argument (it renders the file, not a live stack),
so `plan()` does not accept one, unlike `deploy`/`destroy`.

Chart^H^Hstack reference resolution (which compose file, which namespace)
and value substitution are deployer/build-layer concerns (ADR-0021 Phase 7),
not this class's job — same split Phase 5 drew for `TF_VAR_` injection.

`prepare_namespace()` (ADR-0022 D6/D7) is the *build*-time half: unlike
Helm (one `values.yaml`/`meta.yaml` per module, never merged), Compose
merges every module attached to a namespace into **one**
`docker-compose.yml` — services prefixed `{module}-{service}` (module
document name, not the reference name — matches Helm's own choice, ADR-0022
D6), collision-free by construction. Ported from v1's real `ComposeBuilder`
(`_render_module_services()`), adapted to v2's collapsed environment
schema. `module.spec.compose_file` (pass-through: copy an external compose
file verbatim instead of generating one) is intentionally not implemented
yet — no real example uses it; a module setting it raises `IntegrationError`
rather than silently ignoring it.
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.module_model import ModuleCheckModel, ModuleMountModel
from strata.models.namespace_model import NamespaceModel
from strata.utils.transport import CommandResult


class ComposeIntegration(InfraIntegration):
    """Docker Swarm — deploys a compose file as a stack."""

    TYPE = "compose"
    CAPABILITIES = frozenset({"container"})
    TRANSPORTS = frozenset({"cli"})
    COMMAND = "docker"
    VERSION_ARGS = ("--version",)

    def parse_version(self, raw: str) -> str:
        """Extract `X.Y.Z` from `"Docker version X.Y.Z, build ..."`."""
        match = re.search(r"(\d+\.\d+\.\d+)", raw)
        return match.group(1) if match else raw.strip()

    def prepare_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        resolved: ValueResolution,
    ) -> None:
        """Merge every compose module attached to `namespace` into **one**
        `docker-compose.yml` (ADR-0022 D6/D7) — unlike Helm, which never
        merges.

        `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` tokens inside a
        service's `environment[].value` are written verbatim, unresolved —
        matches Helm's own stance (deploy-time substitution, ADR-0023's
        value-substitution table), though Compose's real convention is a
        bare `${KEY}` in the rendered file (`.env`-file substitution, no
        type prefix); rewriting a typed token down to that bare shape is
        deferred (`resolved` is accepted for signature symmetry with every
        other `InfraIntegration` rendering method, but unused here for the
        same reason).

        Args:
            namespace: The namespace `modules` are attached to — only used
                for the volume-key prefix (`{namespace}_{module}_{volume}`).
            modules: Every module in this namespace whose `spec.type ==
                "compose"` (already grouped by `workload_controller
                .build_workload_modules()`). All share one namespace, so
                `modules[0].source_path.parent` is the namespace's own
                build directory — where the single merged file lands.
            resolved: Unused (see above); kept for signature symmetry.

        Raises:
            IntegrationError: a module sets `compose_file` (pass-through —
                not implemented yet; no real example uses it).
        """
        del resolved
        if not modules:
            return

        services, volumes = _render_namespace_services(str(namespace.meta.name), modules)
        if not services:
            return

        document: dict[str, Any] = {"services": services}
        if volumes:
            document["volumes"] = volumes

        namespace_dir = modules[0].source_path.parent
        namespace_dir.mkdir(parents=True, exist_ok=True)
        (namespace_dir / "docker-compose.yml").write_text(
            yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
        )

    def plan(self, path: Path, *, timeout: int = 60, env: Mapping[str, str] | None = None,
             **kwargs: Any) -> CommandResult:
        """`docker stack config -c path` — renders the merged compose file.
        No true dry-run exists for Swarm (v1's own admitted limitation);
        this is the closest real analog. Takes no stack name — it renders
        the file, it does not target a live stack."""
        return self.run("stack", "config", "-c", str(path), cwd=path.parent, env=env, timeout=timeout)

    def deploy(
        self,
        path: Path,
        *,
        namespace: str | None = None,
        with_registry_auth: bool = True,
        timeout: int = 300,
        env: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`docker stack deploy [--with-registry-auth] -c path namespace`.

        Raises:
            IntegrationError: `namespace` was not given. Keyword-only with a
                default (not required) to stay override-compatible with
                `InfraIntegration.deploy`'s `**kwargs: Any` signature.
        """
        if namespace is None:
            raise IntegrationError(f"{self.name}: 'namespace' is required to deploy a stack.")
        args = ["stack", "deploy"]
        if with_registry_auth:
            args.append("--with-registry-auth")
        args.extend(["-c", str(path), namespace])
        return self.run(*args, cwd=path.parent, env=env, timeout=timeout)

    def destroy(
        self,
        path: Path,
        *,
        namespace: str | None = None,
        timeout: int = 300,
        env: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`docker stack rm namespace`.

        Raises:
            IntegrationError: `namespace` was not given.
        """
        if namespace is None:
            raise IntegrationError(f"{self.name}: 'namespace' is required to remove a stack.")
        return self.run("stack", "rm", namespace, cwd=path.parent, env=env, timeout=timeout)

    def output(self, path: Path, *, namespace: str, timeout: int = 60,
               env: Mapping[str, str] | None = None) -> CommandResult:
        """`docker stack services namespace`. Not part of `InfraIntegration` —
        an extra method, matching v1's `output` step."""
        return self.run("stack", "services", namespace, cwd=path.parent, env=env, timeout=timeout)


# ----------------------------------------------------------------------
# prepare_namespace() rendering — pure, so it's testable without touching
# disk (same split `default_output()`/`prepare()` already draw). Ported
# from v1's real `ComposeBuilder._render_module_services()`.
# ----------------------------------------------------------------------


def _render_namespace_services(
    namespace_name: str, modules: list[ResolvedModule]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Render every compose module in `modules` into one shared
    `{service: entry}`/`{volume: entry}` pair.

    A registry of `module.meta.name -> {service names}` is built first
    (across the *whole* group, not module-by-module) so a service's
    `depends_on` can reference a sibling module's service by
    `@module/service` — v1's real cross-module dependency convention.

    Raises:
        IntegrationError: a module sets `compose_file` (pass-through — not
            implemented yet).
    """
    registry: dict[str, set[str]] = {
        str(item.module.meta.name): {str(service.name) for service in (item.module.spec.services or [])}
        for item in modules
    }

    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {}

    for item in modules:
        module = item.module
        if module.spec.compose_file is not None:
            raise IntegrationError(
                f"module '{module.meta.name}': compose_file pass-through is not implemented yet — "
                "use spec.services instead."
            )
        if not module.spec.services:
            continue

        module_name = str(module.meta.name)
        for service in module.spec.services:
            entry: dict[str, Any] = {}
            if service.image:
                entry["image"] = service.image
            if service.command:
                entry["command"] = service.command
            if service.restart:
                entry["restart"] = service.restart
            if service.environment:
                entry["environment"] = {env.key: env.value for env in service.environment}
            if service.ports:
                entry["ports"] = list(service.ports)
            if service.mounts:
                vol_list = _render_mounts(namespace_name, module_name, service.mounts, volumes)
                if vol_list:
                    entry["volumes"] = vol_list
            if service.depends_on:
                entry["depends_on"] = [
                    _resolve_depends_on(module_name, dep, registry) for dep in service.depends_on
                ]
            if service.healthcheck:
                healthcheck = _render_healthcheck(service.healthcheck)
                if healthcheck:
                    entry["healthcheck"] = healthcheck
            if service.configuration:
                entry.update(service.configuration)

            entry_name = str(service.name) if str(service.name) == module_name else f"{module_name}-{service.name}"
            services[entry_name] = entry

    return services, volumes


def _render_mounts(
    namespace_name: str, module_name: str, mounts: list[ModuleMountModel], volumes: dict[str, Any]
) -> list[str]:
    """Render `mounts` into Compose `volumes:` entries, registering a named
    top-level volume (keyed `{namespace}_{module}_{volume_ref}`, unique per
    module) for each `volume_ref` mount into `volumes` (mutated in place)."""
    entries: list[str] = []
    for mount in mounts:
        target = mount.target_path or "/data"
        if mount.volume_ref is not None:
            volume_key = f"{namespace_name}_{module_name}_{mount.volume_ref}"
            volumes[volume_key] = {}
            entries.append(f"{volume_key}:{target}")
        elif mount.source_path is not None:
            entries.append(f"{mount.source_path}:{target}")
    return entries


def _resolve_depends_on(module_name: str, dep: str, registry: dict[str, set[str]]) -> str:
    """Resolve one `depends_on` entry to its prefixed Compose service name.

    Intra-module (`dep` names a service in this same module): rewritten to
    the prefixed form. Cross-module (`@module/service` or bare `@module`,
    meaning "the service with the same name as the module"): resolved
    against `registry`.

    Raises:
        IntegrationError: `dep` names a module or service that does not
            exist in `registry` (this same group).
    """
    if not dep.startswith("@"):
        if dep not in registry[module_name]:
            return dep  # not a recognised intra-module service — left as-is, matching v1
        return dep if dep == module_name else f"{module_name}-{dep}"

    ref = dep[1:]
    parts = ref.split("/", 1)
    target_module, target_service = parts[0], parts[1] if len(parts) > 1 else parts[0]

    if target_module not in registry:
        raise IntegrationError(
            f"module '{module_name}': depends_on '{dep}' references module '{target_module}', "
            f"which is not a compose module in this namespace. Available: {sorted(registry)}."
        )
    if target_service not in registry[target_module]:
        raise IntegrationError(
            f"module '{module_name}': depends_on '{dep}' references service '{target_service}', "
            f"which does not exist in module '{target_module}'. Available: {sorted(registry[target_module])}."
        )
    return target_service if target_service == target_module else f"{target_module}-{target_service}"


def _render_healthcheck(check: ModuleCheckModel) -> dict[str, Any]:
    """Render a `ModuleCheckModel` into a Docker Compose `healthcheck:` block."""
    healthcheck: dict[str, Any] = {}

    if check.command:
        healthcheck["test"] = ["CMD", *check.command]
    elif check.type == "http" and check.target:
        healthcheck["test"] = ["CMD-SHELL", f"curl -sf {check.target} || exit 1"]
    elif check.type == "tcp" and check.target:
        healthcheck["test"] = ["CMD-SHELL", f"nc -z {check.target} || exit 1"]

    if check.interval:
        healthcheck["interval"] = check.interval
    if check.timeout:
        healthcheck["timeout"] = check.timeout
    if check.retries is not None:
        healthcheck["retries"] = check.retries

    return healthcheck
