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
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.integration_model import Capability
from strata.models.module_model import ModuleCheckModel, ModuleMountModel
from strata.models.namespace_model import NamespaceModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.transport import CommandResult
from strata.utils.value_tokens import (
    referenced_output_keys,
    referenced_secret_keys,
    resolve_value_tokens_renaming_secrets,
)

#: Docker's context for every command; set for the Compose steps of a deployment that names `docker_context`.
#: What `build run` writes per namespace: every module merged, tokens kept. Never rewritten by a deploy.
COMPOSE_FILE = "docker-compose.yml"

#: What each `deploy run` resolves `COMPOSE_FILE` into and hands to `docker stack`: non-secret tokens as their
#: values, `${secret:KEY}` as `${KEY}` (the value goes in the process environment, never on disk). Next to
#: `COMPOSE_FILE`, because Docker resolves a relative path in it (a bind mount, `env_file`) from its folder.
DEPLOY_FILE = "docker-compose.deploy.yml"

DOCKER_CONTEXT_ENV = "DOCKER_CONTEXT"
#: Overrides `DOCKER_CONTEXT` without a word, so a deployment pinned to a context cannot allow it.
DOCKER_HOST_ENV = "DOCKER_HOST"


class ComposeIntegration(InfraIntegration):
    """Docker Swarm — deploys a compose file as a stack."""

    TYPE = "compose"
    CAPABILITIES = frozenset({Capability.CONTAINER})
    TRANSPORTS = frozenset({"cli"})
    COMMAND = "docker"
    VERSION_ARGS = ("--version",)
    TARGET_CONTEXT_FIELD = "docker_context"

    def target_context_env(self, context: str) -> dict[str, str]:
        """Docker reads `DOCKER_CONTEXT` for every command, unless `DOCKER_HOST` is set (rejected before any step)."""
        return {DOCKER_CONTEXT_ENV: context}

    def target_context_problem(self, context: str, environ: Mapping[str, str]) -> tuple[str, str] | None:
        """Why the stacks cannot be pinned to Docker context `context`, as `(code, reason)`, or `None`.

        `DOCKER_HOST` silently wins over `DOCKER_CONTEXT`, so it may not be set at all; the caller's own
        `DOCKER_CONTEXT` may only repeat `context`. The context must exist: `docker context inspect` asks Docker's
        local context store (which honours `DOCKER_CONFIG`) without contacting an engine.
        """
        if environ.get(DOCKER_HOST_ENV):
            return (
                "compose_docker_context_conflict",
                f"{DOCKER_HOST_ENV} is set in the environment, and it overrides the context. Unset it.",
            )
        caller_context = environ.get(DOCKER_CONTEXT_ENV)
        if caller_context and caller_context != context:
            return (
                "compose_docker_context_conflict",
                f"{DOCKER_CONTEXT_ENV} is set to '{caller_context}' in the environment. Unset it, or deploy with a "
                "deployment that names that context.",
            )
        if self.run("context", "inspect", context, env=environ, timeout=30).is_successful:
            return None
        listing = self.run("context", "ls", "--format", "{{.Name}}", env=environ, timeout=30)
        names = [line.strip() for line in listing.stdout.splitlines() if line.strip()] if listing.is_successful else []
        known = ", ".join(f"'{name}'" for name in names) or "none found"
        return ("compose_docker_context_missing", f"Docker has no such context (contexts: {known}).")

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
        value-substitution table). Rewriting a typed token down to
        Compose's own bare `${KEY}` shape and delivering the value are
        `deploy_namespace()`'s job (docs/design/gap_fit_v1.md gap #13, docs/design/
        value-token-resolution.md Full Solution Phase 5), never `build
        run`'s (`resolved` is accepted for signature symmetry with every
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
        (namespace_dir / COMPOSE_FILE).write_text(yaml.safe_dump(document, sort_keys=False, default_flow_style=False))

    def referenced_secret_keys(self, namespace: NamespaceModel, modules: list[ResolvedModule]) -> set[str] | None:
        """Every `${secret:KEY}` in the namespace's merged `docker-compose.yml`, the document `deploy_namespace()`
        resolves (into `DEPLOY_FILE`; this one keeps its tokens). `None` when the file is missing
        (`deploy_namespace()` then fails first anyway)."""
        del namespace
        if not modules:
            return set()
        compose_file = modules[0].source_path.parent / COMPOSE_FILE
        if not compose_file.exists():
            return None
        return referenced_secret_keys(yaml.safe_load(compose_file.read_text()) or {})

    def deploy_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        tokens: dict[str, str],
        dry_run: bool,
        env: dict[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> Diagnostics:
        """Deploy `namespace`'s merged `docker-compose.yml` as one Swarm stack
        (docs/design/gap_fit_v1.md gap #13).

        Unlike Helm (one release per module, `deploy_namespace()` loops over
        `modules`), Compose already merged every module into **one** file at
        build time (`prepare_namespace()`) — there is exactly one
        `docker stack deploy` per namespace, not per module; `modules` is
        only consulted to locate that shared file
        (`modules[0].source_path.parent`, the same namespace build
        directory `prepare_namespace()` wrote to) and to short-circuit
        when a namespace has no compose modules at all.

        Non-secret tokens (`${var:}`/`${feature:}`/`${output:}`) are
        resolved to their literal value and rewritten into the compose
        file, same as Helm's `values.yaml`. A `${secret:KEY}` token is
        rewritten to Compose's own native, bare `${KEY}` interpolation
        syntax instead (`resolve_compose_values()`) — never written to disk
        as a literal — and its real value is delivered only as the
        `docker stack deploy`/`stack config` subprocess's own environment
        (`env=`, merged by `Integration.run()`), which Compose's own
        interpolation engine reads at parse time.

        `dry_run`: still writes the resolved compose file (a preview
        needs the same real input a `docker stack deploy` would use) then
        calls `self.plan_or_warn(...)` (`docker stack config`) instead of
        `self.deploy(...)` — never both. A tool-unsupported preview is a
        warning, not a failure (`plan_or_warn()`'s own contract); a real
        plan failure is still an error.

        `timeout` (seconds, the stage's `plan` or `apply` timeout; docs/design/cli-timeout.md) replaces the
        `stack config`/`stack deploy` default. `None` keeps it.
        """
        del kwargs
        diagnostics = Diagnostics()
        if not modules:
            return diagnostics
        timeouts: dict[str, Any] = {} if timeout is None else {"timeout": timeout}

        compose_file = modules[0].source_path.parent / COMPOSE_FILE
        if not compose_file.exists():
            diagnostics.error(
                f"Namespace '{namespace.meta.name}': {COMPOSE_FILE} not found — run 'strata build run' first.",
                location=str(namespace.meta.name),
            )
            return diagnostics

        document = yaml.safe_load(compose_file.read_text()) or {}

        # Output pre-flight (docs/design/output-declarations.md Phase 3) —
        # every '${output:STEP.KEY}' the merged document references must
        # already be in `tokens` before resolution is attempted. Unlike
        # Helm's per-module loop, Compose already merged every module into
        # this one document — a missing key aborts the whole namespace,
        # same granularity the existing error paths below already use.
        referenced = referenced_output_keys(document)
        missing = sorted(key for key in referenced if key not in tokens)
        if missing:
            for key in missing:
                upstream_step, _, output_key = key.partition(".")
                available = sorted(k.split(".", 1)[1] for k in tokens if k.startswith(f"{upstream_step}."))
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}': '${{output:{key}}}' references step "
                    f"'{upstream_step}', but '{output_key}' was not produced by it (or any durable "
                    f"record). Available from '{upstream_step}': {available}",
                    location=str(namespace.meta.name),
                )
            return diagnostics

        try:
            resolved_document, secrets = resolve_compose_values(document, tokens)
        except ValueError as exc:
            diagnostics.error(f"Namespace '{namespace.meta.name}': {exc}", location=str(namespace.meta.name))
            return diagnostics

        # Never back into `compose_file`: a later deploy of the same build (after a dry run, a retry) would find
        # no token left in it, so deliver no secret and reuse this run's values.
        deploy_file = compose_file.with_name(DEPLOY_FILE)
        deploy_file.write_text(yaml.safe_dump(resolved_document, sort_keys=False, default_flow_style=False))

        if dry_run:
            # Real preview, not a skip — `docker stack config` needs the
            # same rendered file on disk a real `deploy()` would use, just
            # not followed by one.
            plan_result = self.plan_or_warn(
                deploy_file,
                diagnostics=diagnostics,
                location=str(namespace.meta.name),
                env={**(env or {}), **secrets},
                line_callback=line_callback,
                **timeouts,
            )
            if plan_result is not None and not plan_result.is_successful:
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}': docker stack config (dry-run) failed — {plan_result.stderr}",
                    location=str(namespace.meta.name),
                )
            return diagnostics

        result = self.deploy(
            deploy_file,
            namespace=str(namespace.meta.name),
            env={**(env or {}), **secrets},
            line_callback=line_callback,
            **timeouts,
        )
        if not result.is_successful:
            diagnostics.error(
                f"Namespace '{namespace.meta.name}': docker stack deploy failed — {result.stderr}",
                location=str(namespace.meta.name),
            )
        return diagnostics

    def plan(
        self,
        path: Path,
        *,
        timeout: int = 60,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`docker stack config -c path` — renders the merged compose file.
        No true dry-run exists for Swarm (v1's own admitted limitation);
        this is the closest real analog. Takes no stack name — it renders
        the file, it does not target a live stack."""
        return self.run(
            "stack", "config", "-c", str(path), cwd=path.parent, env=env, timeout=timeout, line_callback=line_callback
        )

    def deploy(
        self,
        path: Path,
        *,
        namespace: str | None = None,
        with_registry_auth: bool = True,
        timeout: int = 300,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
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
        return self.run(*args, cwd=path.parent, env=env, timeout=timeout, line_callback=line_callback)

    def destroy(
        self,
        path: Path,
        *,
        namespace: str | None = None,
        timeout: int = 300,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`docker stack rm --detach=false namespace`.

        `--detach=false` makes a successful destroy mean the stack's tasks have stopped, not only that removal
        was requested (docs/design/deploy-destroy.md, D7). A stack that does not exist is not an error for Docker.

        Raises:
            IntegrationError: `namespace` was not given.
        """
        if namespace is None:
            raise IntegrationError(f"{self.name}: 'namespace' is required to remove a stack.")
        return self.run(
            "stack",
            "rm",
            "--detach=false",
            namespace,
            cwd=path.parent,
            env=env,
            timeout=timeout,
            line_callback=line_callback,
        )

    def destroy_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        dry_run: bool,
        env: dict[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        on_step: Callable[[str], None] | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> Diagnostics:
        """Remove the namespace's stack (docs/design/deploy-destroy.md).

        Like `deploy_namespace()`, one Swarm stack per namespace, so `modules` only says whether there is
        anything to do. No compose file is read: a stack is removed by name.

        `dry_run` lists the stack's running services (`docker stack services`) to `on_step`, since Docker has no
        removal preview of its own. A stack Docker does not have lists nothing and is reported as nothing to
        remove; a listing that fails (for example, not a swarm manager) is a warning, because the preview is
        best effort. A real removal that fails is an error.
        """
        del kwargs
        diagnostics = Diagnostics()
        if not modules:
            return diagnostics
        say = on_step or (lambda message: None)
        stack = str(namespace.meta.name)
        compose_file = modules[0].source_path.parent / COMPOSE_FILE

        if dry_run:
            listing = self.output(compose_file, namespace=stack, env=env)
            if not listing.is_successful:
                diagnostics.warning(
                    f"Namespace '{stack}': could not list the services of stack '{stack}' to preview its removal"
                    f" - {listing.stderr.strip()}",
                    location=stack,
                )
            elif not listing.stdout.strip():
                say(f"stack '{stack}' not found, nothing to remove")
            else:
                say(f"would remove stack '{stack}':")
                for line in listing.stdout.splitlines():
                    if line.strip():
                        say(f"  {line.rstrip()}")
            return diagnostics

        result = self.destroy(
            compose_file,
            namespace=stack,
            env=env,
            line_callback=line_callback,
            **({} if timeout is None else {"timeout": timeout}),
        )
        if result.is_successful:
            say(f"removed stack '{stack}'")
        else:
            diagnostics.error(f"Namespace '{stack}': docker stack rm failed - {result.stderr.strip()}", location=stack)
        return diagnostics

    def output(
        self, path: Path, *, namespace: str, timeout: int = 60, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        """`docker stack services namespace`. Not part of `InfraIntegration` —
        an extra method, matching v1's `output` step."""
        return self.run("stack", "services", namespace, cwd=path.parent, env=env, timeout=timeout)


def resolve_compose_values(document: dict[str, Any], values: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve Value tokens in a namespace's already-merged `docker-compose.yml`
    payload for deploy-time delivery (docs/design/gap_fit_v1.md gap #9, docs/design/
    value-token-resolution.md's Full Solution Phase 5).

    Thin wrapper around `resolve_value_tokens_renaming_secrets()`
    (`value_tokens.py`) — unlike Helm's `resolve_module_values()`, there is
    no per-module payload to build first: `prepare_namespace()` already
    merged every module into the one document this function receives
    directly, verbatim off disk.

    Deliberately pure and side-effect-free, matching `resolve_module_values()`'s
    own "testable without touching disk" convention — writing the resolved
    document back and running `docker stack deploy` are `deploy_namespace()`'s
    job, not this function's.

    Returns:
        `(resolved_document, secrets)` — `resolved_document` is what
        `docker-compose.yml` should be rewritten to (non-secret tokens
        resolved to their literal value, secret tokens renamed to bare
        `${KEY}`); `secrets` is every `{KEY: resolved_value}` pair to
        deliver as the deploy subprocess's own environment.
    """
    return resolve_value_tokens_renaming_secrets(document, values)


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
                entry["depends_on"] = [_resolve_depends_on(module_name, dep, registry) for dep in service.depends_on]
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
