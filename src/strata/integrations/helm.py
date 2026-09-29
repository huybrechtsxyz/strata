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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.integration_model import Capability
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.transport import CommandResult
from strata.utils.value_tokens import resolve_value_tokens_tracking_secrets


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
        **kwargs: Any,
    ) -> Diagnostics:
        """Deploy every Helm module in `modules` (docs/_gap_v1.md gap #13).

        One `helm upgrade` per module — `prepare_namespace()`'s own
        already-written `values.yaml`/`meta.yaml` are this method's real
        input, never re-rendered from scratch: `meta.yaml` supplies
        release/namespace/chart coordinates exactly as v1's real
        `HelmDeployer` reads them back (zero new schema needed);
        `values.yaml`'s tokens are resolved via `resolve_module_values()`
        (gap #9 Phase 4) and rewritten in place for non-secret leaves,
        while secret-shaped leaves are delivered via `--set-string`,
        never written to disk.
        """
        del kwargs
        diagnostics = Diagnostics()
        remotes = remotes or {}
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

            if meta.get("chartName") is None:
                # A git-based/local chart `source` (`_render_meta()` omits
                # chart coordinates entirely for this case) — the module's
                # own already-materialised directory *is* the chart.
                chart: str | None = str(item.source_path)
                chart_error: str | None = None
            else:
                chart, chart_error = _resolve_chart(self, meta, remotes, env=env)
            if chart_error:
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}', module '{item.reference.name}': {chart_error}",
                    location=item.reference.name,
                )
                continue

            resolved_values, secrets = resolve_module_values(item.module, tokens)
            set_string = list(secrets.items())

            if dry_run:
                continue

            if resolved_values:
                values_file.write_text(yaml.safe_dump(resolved_values, sort_keys=False, default_flow_style=False))

            result = self.deploy(
                values_file,
                release=release,
                namespace=release_namespace,
                chart=chart,
                set_string=set_string,
                env=env,
            )
            if not result.is_successful:
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}', module '{item.reference.name}': "
                    f"helm upgrade failed — {result.stderr}",
                    location=item.reference.name,
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
        return self.run(*args, cwd=path.parent, env=env, timeout=timeout)

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

    def lint(self, path: Path, *, chart: str, timeout: int = 60, env: Mapping[str, str] | None = None) -> CommandResult:
        """`helm lint -f path chart`. v1's `check` step."""
        return self.run("lint", "-f", str(path), chart, cwd=path.parent, env=env, timeout=timeout)

    def repo_update(self, *, timeout: int = 60, env: Mapping[str, str] | None = None) -> CommandResult:
        """`helm repo update`. v1's `setup` step (repo registration/aliasing is
        deployer-layer — this only refreshes already-added repos)."""
        return self.run("repo", "update", env=env, timeout=timeout)

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
    for deploy-time delivery (docs/_gap_v1.md gap #9, docs/design/
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
    for a Helm module at all (docs/_gap_v1.md gap #13) — this function only
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
    exclusive on `SourceModel` (docs/_gap_v1.md gap #2's Option 1) — at
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
# argv assembly (docs/_gap_v1.md gap #13). Ported from v1's real
# `helm_deployer.py` (`_sanitize_repo_name()`/`_escape_set_value()`), not
# redesigned — same evidence-over-assumption reasoning as the argv shapes
# `plan()`/`deploy()`/`destroy()` already copied verbatim.
# ----------------------------------------------------------------------


def _sanitize_repo_name(url: str) -> str:
    """Derive a Helm-compatible repo alias from a chart registry URL.

    Ported verbatim from v1's real `helm_deployer.py`: strips the scheme,
    replaces non-alphanumeric characters with `-`, truncates to 20 chars —
    `helm repo add` needs a short, stable, filesystem/CLI-safe alias, not
    the raw URL.
    """
    name = re.sub(r"^https?://", "", url)
    name = re.sub(r"[^a-zA-Z0-9]", "-", name)
    name = name.strip("-")
    return name[:20]


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


def _chart_ref_from_url(
    integration: "HelmIntegration", url: str, chart_name: str, *, is_oci: bool, env: dict[str, str] | None
) -> str:
    """Build the real `helm upgrade` chart argument from a resolved registry
    URL — shared by both `chartRemote` (a `SolutionRemoteModel` lookup) and
    `chartRepository` (an inline URL, docs/_gap_v1.md gap #2's Option 1)
    resolution paths, which differ only in *where* the URL and OCI-ness
    come from, never in what happens once both are known.

    No `helm repo add` needed for an OCI registry — Helm resolves `oci://`
    refs natively. Otherwise, register a repo alias first (`helm repo add
    <alias> <url>`, ignoring failure — may already be registered, matches
    v1's own "best effort" comment).
    """
    if is_oci:
        return f"{url.rstrip('/')}/{chart_name}"
    alias = _sanitize_repo_name(url)
    integration.run("repo", "add", alias, url, env=env, timeout=60)
    return f"{alias}/{chart_name}"


def _resolve_chart(
    integration: "HelmIntegration",
    meta: dict[str, Any],
    remotes: dict[str, SolutionRemoteModel],
    *,
    env: dict[str, str] | None,
) -> tuple[str | None, str | None]:
    """Resolve `meta.yaml`'s chart coordinates into a real `helm upgrade`
    chart reference, or return an error message.

    No `chartName` (a git-based/local chart `source`): the module's own
    `item.source_path` **is** the chart, already handled by the caller
    before this function is even reached in that case — this function is
    only called when `chartName` is present, matching v1's real branch.

    Two mutually exclusive ways a chart-based source names its registry
    (`SourceModel`'s own validator enforces exactly one — see its
    docstring, docs/_gap_v1.md gap #2):

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

    Returns:
        `(chart_ref, None)` on success, `(None, error_message)` on failure.
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
        if remote.type is RemoteType.OCI:
            return _chart_ref_from_url(integration, remote.url, chart_name, is_oci=True, env=env), None
        if remote.type is RemoteType.HELM:
            return _chart_ref_from_url(integration, remote.url, chart_name, is_oci=False, env=env), None
        return None, f"chart remote '{remote_name}' has type '{remote.type.value}', not a valid Helm chart source."

    if inline_repository is not None:
        url = str(inline_repository)
        return _chart_ref_from_url(integration, url, chart_name, is_oci=url.startswith("oci://"), env=env), None

    return None, "meta.yaml has 'chartName' but no 'chartRemote'/'chartRepository' — cannot resolve a chart registry."
