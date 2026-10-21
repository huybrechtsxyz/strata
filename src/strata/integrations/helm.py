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

import hashlib
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError, RegistryAuthError
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.auth_models import AuthenticationModel
from strata.models.integration_model import Capability
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.transport import CommandResult
from strata.utils.value_tokens import referenced_output_keys, resolve_value_tokens_tracking_secrets


class HelmIntegration(InfraIntegration):
    """Helm — installs/upgrades/uninstalls chart releases on Kubernetes."""

    TYPE = "helm"
    CAPABILITIES = frozenset({Capability.CONTAINER})
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
            (item.source_path / "meta.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, default_flow_style=False))

    def deploy_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        tokens: dict[str, str],
        dry_run: bool,
        remotes: dict[str, SolutionRemoteModel] | None = None,
        env: dict[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        registry_credentials: Mapping[str, "RegistryCredentials"] | None = None,
        **kwargs: Any,
    ) -> Diagnostics:
        """Deploy every Helm module in `modules` (docs/design/gap_fit_v1.md gap #13).

        One `helm upgrade` per module — `prepare_namespace()`'s own
        already-written `values.yaml`/`meta.yaml` are this method's real
        input, never re-rendered from scratch: `meta.yaml` supplies
        release/namespace/chart coordinates exactly as v1's real
        `HelmDeployer` reads them back (zero new schema needed);
        `values.yaml`'s tokens are resolved via `resolve_module_values()`
        (gap #9 Phase 4) and rewritten in place for non-secret leaves,
        while secret-shaped leaves are delivered via `--set-string`,
        never written to disk.

        `dry_run`: still writes the resolved `values.yaml` (a preview
        needs the same real input a `helm upgrade` would use) then calls
        `self.plan_or_warn(...)` (`helm upgrade --dry-run --install`)
        instead of `self.deploy(...)` — never both. A tool-unsupported
        preview is a warning, not a failure (`plan_or_warn()`'s own
        contract); a real plan failure is still an error.

        `registry_credentials` (ADR-0042), keyed by remote name: a module whose
        `chartRemote` has an entry authenticates to that registry (password on stdin only) inside a
        temporary Helm config that every helm call for the module shares and that is removed when this
        returns, so the password never reaches the user's own Helm config. A failed login is an error
        for that module (`helm_registry_auth_failed`); the others still run.
        """
        del kwargs
        with ExitStack() as isolated:
            return self._deploy_modules(
                namespace,
                modules,
                tokens=tokens,
                dry_run=dry_run,
                remotes=remotes or {},
                env=env,
                line_callback=line_callback,
                registry_credentials=registry_credentials or {},
                isolated=isolated,
            )

    def _deploy_modules(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        tokens: dict[str, str],
        dry_run: bool,
        remotes: dict[str, SolutionRemoteModel],
        env: dict[str, str] | None,
        line_callback: Callable[[str, str], None] | None,
        registry_credentials: Mapping[str, "RegistryCredentials"],
        isolated: ExitStack,
    ) -> Diagnostics:
        diagnostics = Diagnostics()
        for item in modules:
            meta_file = item.source_path / "meta.yaml"
            values_file = item.source_path / "values.yaml"
            if not meta_file.exists():
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}', module '{item.reference.name}': "
                    "meta.yaml not found — run 'strata build run' first.",
                    location=item.reference.name,
                )
                continue

            meta = yaml.safe_load(meta_file.read_text()) or {}
            release = str(meta.get("releaseName") or item.reference.name)
            release_namespace = str(meta.get("namespace") or namespace.meta.name)

            credentials = None
            if meta.get("chartRemote") is not None:
                credentials = registry_credentials.get(str(meta["chartRemote"]))
            module_env = {**(env or {}), **_isolated_helm_env(isolated)} if credentials is not None else env

            if meta.get("chartName") is None:
                # A git-based/local chart `source` (`_render_meta()` omits
                # chart coordinates entirely for this case) — the module's
                # own already-materialised directory *is* the chart.
                chart: str | None = str(item.source_path)
                chart_error: str | None = None
                chart_error_code: str | None = None
            else:
                chart_error_code = None
                try:
                    chart, chart_error = _resolve_chart(
                        self,
                        meta,
                        remotes,
                        env=module_env,
                        diagnostics=diagnostics,
                        location=item.reference.name,
                        credentials=credentials,
                    )
                except RegistryAuthError as exc:
                    chart, chart_error, chart_error_code = None, str(exc), "helm_registry_auth_failed"
            if chart_error:
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}', module '{item.reference.name}': {chart_error}",
                    location=item.reference.name,
                    code=chart_error_code,
                )
                continue

            # Output pre-flight (docs/design/output-declarations.md Phase
            # 3) — every '${output:STEP.KEY}' this module's own rendered
            # values reference must already be in `tokens` before
            # resolution is attempted. Unlike deploy_controller.py's
            # Terraform steps, one bad module here only skips itself —
            # the rest of the namespace still deploys (matches the
            # severity the 2026-10-12 ValueError fix already established
            # for this path).
            referenced = referenced_output_keys(_render_values(item.module))
            missing = sorted(key for key in referenced if key not in tokens)
            if missing:
                for key in missing:
                    upstream_step, _, output_key = key.partition(".")
                    available = sorted(k.split(".", 1)[1] for k in tokens if k.startswith(f"{upstream_step}."))
                    diagnostics.error(
                        f"Namespace '{namespace.meta.name}', module '{item.reference.name}': "
                        f"'${{output:{key}}}' references step '{upstream_step}', but "
                        f"'{output_key}' was not produced by it (or any durable record). "
                        f"Available from '{upstream_step}': {available}",
                        location=item.reference.name,
                    )
                continue

            try:
                resolved_values, secrets = resolve_module_values(item.module, tokens)
            except ValueError as exc:
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}', module '{item.reference.name}': {exc}",
                    location=item.reference.name,
                )
                continue
            set_string = list(secrets.items())
            chart_version = str(meta["chartVersion"]) if meta.get("chartVersion") is not None else None

            if resolved_values:
                values_file.write_text(yaml.safe_dump(resolved_values, sort_keys=False, default_flow_style=False))

            if dry_run:
                # Real preview, not a skip — `plan()` needs the same
                # rendered file on disk a real `deploy()` would use, just
                # not followed by one.
                plan_result = self.plan_or_warn(
                    values_file,
                    diagnostics=diagnostics,
                    location=item.reference.name,
                    release=release,
                    namespace=release_namespace,
                    chart=chart,
                    version=chart_version,
                    set_string=set_string,
                    env=module_env,
                    line_callback=line_callback,
                )
                if plan_result is not None and not plan_result.is_successful:
                    diagnostics.error(
                        f"Namespace '{namespace.meta.name}', module '{item.reference.name}': "
                        f"helm plan (dry-run) failed — {plan_result.stderr}",
                        location=item.reference.name,
                    )
                continue

            result = self.deploy(
                values_file,
                release=release,
                namespace=release_namespace,
                chart=chart,
                version=chart_version,
                set_string=set_string,
                env=module_env,
                line_callback=line_callback,
            )
            if not result.is_successful:
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}', module '{item.reference.name}': "
                    f"helm upgrade failed — {result.stderr}",
                    location=item.reference.name,
                )
        return diagnostics

    def check_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        tokens: dict[str, str],
        env: dict[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        timeout: int = 60,
        **kwargs: Any,
    ) -> Diagnostics:
        """`helm lint` every local-chart module in `modules` (ADR-0043).

        Lints the built module directory with a temporary `-f` overlay of the resolved non-secret values,
        so it sees what `deploy_namespace()` will. Secret-shaped leaves stay as literal tokens: lint needs no
        secret, so none reaches disk or argv. The overlay lives outside the build directory, which a check
        must not alter. A `${output:}` whose upstream step has not applied yet gets a placeholder, because a
        check must not depend on infrastructure that does not exist yet. Registry charts are skipped: `helm
        lint` cannot lint a remote chart. Only `[ERROR]` findings fail (lint exits non-zero on nothing else).
        """
        del kwargs
        diagnostics = Diagnostics()
        for item in modules:
            label = f"Namespace '{namespace.meta.name}', module '{item.reference.name}'"
            meta_file = item.source_path / "meta.yaml"
            if not meta_file.exists():
                diagnostics.error(
                    f"{label}: meta.yaml not found — run 'strata build run' first.", location=item.reference.name
                )
                continue
            if (yaml.safe_load(meta_file.read_text()) or {}).get("chartName") is not None:
                continue

            missing = referenced_output_keys(_render_values(item.module)) - tokens.keys()
            try:
                resolved_values, _ = resolve_module_values(item.module, {**dict.fromkeys(missing, "lint"), **tokens})
            except ValueError as exc:
                diagnostics.error(f"{label}: {exc}", location=item.reference.name)
                continue

            with tempfile.TemporaryDirectory(prefix="strata-lint-") as tmp:
                overlay = Path(tmp) / "values.yaml"
                overlay.write_text(yaml.safe_dump(resolved_values, sort_keys=False, default_flow_style=False))
                result = self.lint(
                    overlay, chart=str(item.source_path), timeout=timeout, env=env, line_callback=line_callback
                )
            if not result.is_successful:
                lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                # Without `[ERROR]` lines (a missing Chart.yaml, say) lint's own message is on stdout, not stderr.
                findings = [line for line in lines if "[ERROR]" in line] or [
                    line for line in lines if not line.startswith(("==>", "[INFO]", "[WARNING]"))
                ]
                detail = " | ".join(findings) or result.stderr.strip() or result.stdout.strip()
                diagnostics.error(
                    f"{label}: helm lint failed — {detail}", location=item.reference.name, code="helm_lint_failed"
                )
        return diagnostics

    def plan(
        self,
        path: Path,
        *,
        release: str | None = None,
        namespace: str | None = None,
        chart: str | None = None,
        version: str | None = None,
        set_string: Sequence[tuple[str, str]] | None = None,
        timeout: int = 600,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`helm upgrade --dry-run --install --namespace ns -f path [--set-string ...] release chart [--version v]`.

        Raises:
            IntegrationError: `release`/`namespace`/`chart` were not all given.
                Keyword-only with defaults (not required) to stay
                override-compatible with `InfraIntegration.plan`'s
                `**kwargs: Any` signature.
        """
        release, namespace, chart = self._require(release=release, namespace=namespace, chart=chart)
        args = ["upgrade", "--dry-run", "--install", "--namespace", namespace, "-f", str(path)]
        args.extend(_set_string_args(set_string))
        args.extend([release, chart])
        if version:
            args.extend(["--version", version])
        return self.run(*args, cwd=path.parent, env=env, timeout=timeout, line_callback=line_callback)

    def deploy(
        self,
        path: Path,
        *,
        release: str | None = None,
        namespace: str | None = None,
        chart: str | None = None,
        version: str | None = None,
        set_string: Sequence[tuple[str, str]] | None = None,
        create_namespace: bool = True,
        wait: bool = True,
        atomic: bool = True,
        deploy_timeout: str = "5m",
        timeout: int = 600,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`helm upgrade --install [--create-namespace] [--wait] [--atomic] --timeout T
        --namespace ns -f path [--set-string ...] release chart [--version v]`.

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
        args.extend(["--timeout", deploy_timeout, "--namespace", namespace, "-f", str(path)])
        args.extend(_set_string_args(set_string))
        args.extend([release, chart])
        if version:
            args.extend(["--version", version])
        return self.run(*args, cwd=path.parent, env=env, timeout=timeout, line_callback=line_callback)

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

    def _require(self, *, release: str | None, namespace: str | None, chart: str | None) -> tuple[str, str, str]:
        """Narrow the three optional-but-really-required params, or raise naming
        exactly which ones are missing (not just "one of these three")."""
        pairs = [("release", release), ("namespace", namespace), ("chart", chart)]
        missing = [name for name, value in pairs if value is None]
        if missing:
            raise IntegrationError(
                f"{self.name}: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} required."
            )
        assert release is not None and namespace is not None and chart is not None
        return release, namespace, chart

    # ------------------------------------------------------------------
    # extras beyond InfraIntegration — real v1 deployer steps
    # ------------------------------------------------------------------

    def lint(
        self,
        path: Path,
        *,
        chart: str,
        timeout: int = 60,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
    ) -> CommandResult:
        """`helm lint -f path chart`. v1's `check` step."""
        return self.run(
            "lint", "-f", str(path), chart, cwd=path.parent, env=env, timeout=timeout, line_callback=line_callback
        )

    def get_manifest(
        self, *, release: str, namespace: str, timeout: int = 60, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        """`helm get manifest --namespace ns release`. v1's `plan_destroy`/`show_plan` step."""
        return self.run("get", "manifest", "--namespace", namespace, release, env=env, timeout=timeout)

    def get_values(
        self, *, release: str, namespace: str, timeout: int = 60, env: Mapping[str, str] | None = None
    ) -> CommandResult:
        """`helm get values --namespace ns release`. v1's `output` step."""
        return self.run("get", "values", "--namespace", namespace, release, env=env, timeout=timeout)


# ----------------------------------------------------------------------
# prepare_namespace() rendering — pure, so it's testable without touching
# disk (same split `default_output()`/`prepare()` already draw).
# ----------------------------------------------------------------------


def read_chart_values(chart_dir: Path) -> dict[str, Any]:
    """A local chart's own `values.yaml` as a dict; `{}` when absent, unparseable or not a mapping."""
    try:
        data = yaml.safe_load((chart_dir / "values.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def check_configuration_keys(configuration: Mapping[str, Any], chart_values: Mapping[str, Any]) -> list[str]:
    """Findings for `configuration` keys a chart's default `values.yaml` does not declare.

    Ported from v1's `check_helm_values()` (warnings only): top-level keys plus one nested level, with a
    close-match suggestion. An empty default mapping (`env: {}`) is a free-form bag, so its children are not
    checked. No chart defaults means nothing to check against.
    """
    if not chart_values:
        return []

    def _unknown(key: str, known: Mapping[str, Any], path: str) -> str:
        message = f"configuration key '{path}' is not in the chart's values.yaml"
        close = get_close_matches(key, sorted(known), n=1, cutoff=0.6)
        if close:
            message += f" (did you mean '{path.removesuffix(key)}{close[0]}'?)"
        return message

    findings: list[str] = []
    for key, value in configuration.items():
        if key not in chart_values:
            findings.append(_unknown(key, chart_values, key))
            continue
        default = chart_values[key]
        if isinstance(value, dict) and isinstance(default, dict) and default:
            findings.extend(_unknown(child, default, f"{key}.{child}") for child in value if child not in default)
    return findings


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


def resolve_module_values(module: ModuleModel, values: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve Value tokens in one module's rendered `values.yaml` payload
    for deploy-time delivery (docs/design/gap_fit_v1.md gap #9, docs/design/
    value-token-resolution.md's Full Solution Phase 4).

    Reuses `_render_values()`'s own build-time payload (still containing
    unresolved `${var:}`/`${secret:}`/`${feature:}` tokens verbatim, exactly
    what `prepare_namespace()` already wrote to disk) and
    `resolve_value_tokens_tracking_secrets()` (`value_tokens.py`, Phase 3)
    to split it: non-secret leaves resolve in place, safe to rewrite into
    `values.yaml`; secret-shaped leaves are reported as `{dotted_path:
    resolved_value}` instead, for a caller to deliver via `helm upgrade
    --set-string <path>=<value>` — never written to disk.

    Deliberately pure and side-effect-free (matches `_render_values()`'s own
    "testable without touching disk" convention) — writing the resolved
    `values.yaml` back and assembling `helm upgrade`'s actual argv are a
    caller's job, not this function's. That caller doesn't exist yet:
    `deploy_run()`'s step loop has no release/namespace/chart derivation
    for a Helm module at all (docs/design/gap_fit_v1.md gap #13) — this function only
    supplies the resolution primitive gap #9 needs, same reasoning gap #9's
    own design already gave for `helm.py`'s docstring naming `--set-string`
    as the intended mechanism before the path-tracking to make it buildable
    existed.

    Returns:
        `(resolved_values, secrets)` — `resolved_values` is what
        `values.yaml` should be rewritten to; `secrets` is every
        secret-shaped leaf's dotted path and resolved value.
    """
    payload = _render_values(module)
    return resolve_value_tokens_tracking_secrets(payload, values)


def _render_meta(namespace: NamespaceModel, item: ResolvedModule) -> dict[str, Any]:
    """Build the `meta.yaml` payload for one Helm module.

    `releaseName` defaults to `item.reference.name`, not `module.meta.name`
    (v1's real default) — the same Module document can be attached to a
    namespace more than once under different reference names
    (`ModuleReferenceModel`'s own docstring), and two live Helm releases
    can never share a name, so the one identifier guaranteed unique per
    attachment (the reference name) is the only safe default; `module.meta.name`
    is not, and would silently collide if that ever happens.

    Chart coordinates (`chartName`/`chartVersion`/`chartRemote`/
    `chartRepository`) are included only for a chart-based `source`
    (`chart_name` set) — a registry chart has no local copy for a
    deploy-time `helm upgrade` to reference by path, so `meta.yaml` must
    carry enough for the deployer to pull it directly instead (mirrors
    v1's real "self-contained build artifact" reasoning). Omitted for a
    git-based (local chart) `source`, where `item.source_path` itself is
    the chart to deploy. `chartRemote`/`chartRepository` are mutually
    exclusive on `SourceModel` (docs/design/gap_fit_v1.md gap #2's Option 1) — at
    most one is ever written.
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
        elif source.chart_repository is not None:
            meta["chartRepository"] = source.chart_repository

    return meta


# ----------------------------------------------------------------------
# deploy_namespace() helpers — chart reference resolution + --set-string
# argv assembly (docs/design/gap_fit_v1.md gap #13). Ported from v1's real
# `helm_deployer.py` (`_escape_set_value()`), not redesigned — same
# evidence-over-assumption reasoning as the argv shapes
# `plan()`/`deploy()`/`destroy()` already copied verbatim.
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryCredentials:
    """Username and password for a private chart registry (ADR-0042)."""

    username: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.username or not self.password:
            raise ValueError("registry credentials need a non-empty username and password")


#: Authentication methods a chart registry accepts; `cli` means the operator already logged in.
REGISTRY_AUTH_METHODS = ("token", "cli")


def credentials_from_authentication(
    auth: AuthenticationModel | None, values: Mapping[str, str], *, owner: str
) -> RegistryCredentials | None:
    """The credentials `auth` describes, read from the deploy's resolved `values`.

    `None` for no authentication or `method: cli`: Helm then uses whatever login it already has. Unlike a git
    fetch, a chart registry needs the literal `username` beside the token.

    Raises:
        IntegrationError: an unsupported method, no `username`, or no value for the token's key. `owner` names the
            remote and integration in the message; the password never appears in it.
    """
    if auth is None or auth.method == "cli":
        return None
    if auth.method != "token":
        raise IntegrationError(
            f"{owner}: authentication method '{auth.method}' cannot authenticate a chart registry "
            f"(supported: {', '.join(REGISTRY_AUTH_METHODS)})"
        )
    assert auth.token is not None  # guaranteed by AuthenticationModel's own validator
    if not auth.token.username:
        raise IntegrationError(f"{owner}: a chart registry needs 'username' beside the token (a literal, not a key)")
    password = values.get(auth.token.token)
    if not password:
        raise IntegrationError(f"{owner}: no value for '{auth.token.token}', the key its token authentication names")
    return RegistryCredentials(username=auth.token.username, password=password)


def _repo_alias(url: str) -> str:
    """Derive a collision-free Helm repo alias from a chart registry URL.

    A readable prefix plus 8 hex characters of the URL's hash. v1 truncated the
    sanitised URL to 20 characters, so two registries sharing that prefix got
    one alias — and with `--force-update` the second would overwrite the first.
    """
    host_and_path = re.sub(r"^https?://", "", url)
    prefix = re.sub(r"[^a-zA-Z0-9]", "-", host_and_path).strip("-")[:12].rstrip("-")
    digest = hashlib.sha1(url.rstrip("/").encode(), usedforsecurity=False).hexdigest()[:8]
    return f"{prefix}-{digest}"


def _escape_set_value(value: str) -> str:
    """Backslash-escape characters with special meaning in Helm's `--set`
    mini-language, so a value survives as a literal string instead of
    being parsed as additional `--set` assignments or nested paths.

    Ported verbatim from v1's real `helm_deployer.py`. Order matters: `\\`
    must be escaped first, or characters escaped afterwards would have
    their own backslash re-escaped.
    """
    for ch in ("\\", ",", ".", "=", "{", "}", "[", "]"):
        value = value.replace(ch, f"\\{ch}")
    return value


def _set_string_args(set_string: "Sequence[tuple[str, str]] | None") -> list[str]:
    """`["--set-string", "path=value", ...]` for every `(path, value)` pair —
    the value escaped for Helm's `--set` mini-language (`_escape_set_value()`),
    the path left as-is (a dotted `values.yaml` path, already Helm's own
    `--set` path syntax, e.g. `authentik-server.env.DB_PASSWORD`)."""
    args: list[str] = []
    for path, value in set_string or []:
        args.extend(["--set-string", f"{path}={_escape_set_value(value)}"])
    return args


def _isolated_helm_env(stack: ExitStack) -> dict[str, str]:
    """Helm state variables pointing at a temporary directory that `stack` removes.

    `repo add --password-stdin` persists the password in plain text in `repositories.yaml`, and credentials only
    work in the config they were added to, so every helm call for one credentialed module shares this directory.
    """
    root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="strata-helm-")))
    return {
        "HELM_REPOSITORY_CONFIG": str(root / "repositories.yaml"),
        "HELM_REPOSITORY_CACHE": str(root / "repository-cache"),
        "HELM_REGISTRY_CONFIG": str(root / "registry.json"),
        "HELM_CACHE_HOME": str(root / "cache"),
    }


def _scrub(text: str, credentials: RegistryCredentials) -> str:
    return text.replace(credentials.password, "***")


def _chart_ref_from_url(
    integration: "HelmIntegration",
    url: str,
    chart_name: str,
    *,
    is_oci: bool,
    env: dict[str, str] | None,
    diagnostics: Diagnostics | None = None,
    location: str | None = None,
    credentials: RegistryCredentials | None = None,
) -> str:
    """Build the real `helm upgrade` chart argument from a resolved registry
    URL — shared by both `chartRemote` (a `SolutionRemoteModel` lookup) and
    `chartRepository` (an inline URL, docs/design/gap_fit_v1.md gap #2's Option 1)
    resolution paths, which differ only in *where* the URL and OCI-ness
    come from, never in what happens once both are known.

    No `helm repo add` needed for an OCI registry — Helm resolves `oci://`
    refs natively. Otherwise register the repo alias and refresh its index in
    one call (`helm repo add --force-update`): a plain `repo add` skips an
    alias that already exists, leaving a stale index that cannot resolve a
    newer pinned `--version` (docs/design/helm-chart-deployment.md). A failure is a
    warning, not an error — a cached index may still satisfy the pin.

    With `credentials` (gap 5) the registration also authenticates — `repo add --username --password-stdin`
    for HTTP, `registry login` for OCI, the password on stdin only — and a failure raises, because without
    the credentials the chart cannot be fetched at all.

    Raises:
        RegistryAuthError: authenticating with `credentials` failed (the password is scrubbed from the message).
    """
    if is_oci:
        if credentials is not None:
            host = urlsplit(url).netloc
            result = integration.run(
                "registry",
                "login",
                host,
                "--username",
                credentials.username,
                "--password-stdin",
                input=credentials.password,
                env=env,
                timeout=60,
            )
            if not result.is_successful:
                raise RegistryAuthError(
                    _scrub(f"could not log in to chart registry '{host}' — {result.stderr.strip()}", credentials)
                )
        return f"{url.rstrip('/')}/{chart_name}"
    alias = _repo_alias(url)
    if credentials is not None:
        result = integration.run(
            "repo",
            "add",
            alias,
            url,
            "--force-update",
            "--username",
            credentials.username,
            "--password-stdin",
            input=credentials.password,
            env=env,
            timeout=60,
        )
        if not result.is_successful:
            raise RegistryAuthError(
                _scrub(f"could not authenticate to chart repository '{url}' — {result.stderr.strip()}", credentials)
            )
        return f"{alias}/{chart_name}"
    result = integration.run("repo", "add", alias, url, "--force-update", env=env, timeout=60)
    if not result.is_successful and diagnostics is not None:
        diagnostics.warning(
            f"could not refresh chart repository '{url}', continuing with any cached index — {result.stderr.strip()}",
            location=location,
            code="helm_repo_refresh_failed",
        )
    return f"{alias}/{chart_name}"


def _resolve_chart(
    integration: "HelmIntegration",
    meta: dict[str, Any],
    remotes: dict[str, SolutionRemoteModel],
    *,
    env: dict[str, str] | None,
    diagnostics: Diagnostics | None = None,
    location: str | None = None,
    credentials: RegistryCredentials | None = None,
) -> tuple[str | None, str | None]:
    """Resolve `meta.yaml`'s chart coordinates into a real `helm upgrade`
    chart reference, or return an error message.

    No `chartName` (a git-based/local chart `source`): the module's own
    `item.source_path` **is** the chart, already handled by the caller
    before this function is even reached in that case — this function is
    only called when `chartName` is present, matching v1's real branch.

    Two mutually exclusive ways a chart-based source names its registry
    (`SourceModel`'s own validator enforces exactly one — see its
    docstring, docs/design/gap_fit_v1.md gap #2):

    - `chartRemote`: a name that must resolve to a real, declared
      `SolutionRemoteModel` (looked up by name, never guessed). Its `type`
      decides the mechanism, matching v1's real OCI-vs-HTTP split —
      `RemoteType.OCI` or `RemoteType.HELM` only; `git`/`local` are not
      valid chart remote types.
    - `chartRepository`: an inline URL/OCI reference, for the common
      single-consumer, unauthenticated case (gap #2's real haven evidence:
      every one of its 6 chart modules has a distinct, unshared registry).
      No `SolutionRemoteModel` to read a `type` from, so OCI-ness is
      sniffed from the URL's own `oci://` prefix instead — the same
      sniffing v1 always did (v2's typed remotes only exist for the
      `chartRemote` path).

    `credentials` apply to the `chartRemote` path only: an inline `chartRepository` has no integration to
    name them.

    Returns:
        `(chart_ref, None)` on success, `(None, error_message)` on failure.

    Raises:
        RegistryAuthError: `credentials` were given and the registry rejected them.
    """
    chart_name = meta.get("chartName")
    remote_name = meta.get("chartRemote")
    inline_repository = meta.get("chartRepository")
    if chart_name is None:
        # Defensive only — callers only reach this function once they've
        # already confirmed `meta["chartName"]` is present; satisfies the
        # type checker for `_chart_ref_from_url()`'s `str` parameter too.
        return None, "meta.yaml has no 'chartName' — cannot resolve a chart registry."
    chart_name = str(chart_name)

    if remote_name is not None:
        remote = remotes.get(str(remote_name))
        if remote is None:
            return None, f"chart remote '{remote_name}' is not declared in this solution's remotes."
        if remote.type not in (RemoteType.OCI, RemoteType.HELM):
            return None, f"chart remote '{remote_name}' has type '{remote.type.value}', not a valid Helm chart source."
        chart_ref = _chart_ref_from_url(
            integration,
            remote.url,
            chart_name,
            is_oci=remote.type is RemoteType.OCI,
            env=env,
            diagnostics=diagnostics,
            location=location,
            credentials=credentials,
        )
        return chart_ref, None

    if inline_repository is not None:
        url = str(inline_repository)
        chart_ref = _chart_ref_from_url(
            integration,
            url,
            chart_name,
            is_oci=url.startswith("oci://"),
            env=env,
            diagnostics=diagnostics,
            location=location,
        )
        return chart_ref, None

    return None, "meta.yaml has 'chartName' but no 'chartRemote'/'chartRepository' — cannot resolve a chart registry."
