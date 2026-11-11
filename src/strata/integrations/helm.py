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
`HelmDeployer.plan()`/`.apply()`/`.destroy()` — proven, not redesigned. Helm 4 gets its own flags
(`--dry-run=client`, `--wait=watcher`, `--rollback-on-failure`); v1's (`--dry-run`, `--wait`, `--atomic`) are kept
for a Helm integration whose `spec.version` excludes Helm 4 (ADR-0055, docs/work/helm-4-support.md).

Chart reference resolution (`meta.yaml` parsing, OCI-vs-HTTP-vs-local,
repo aliasing) and value substitution (`${var:}/${secret:}/${feature:}`
tree-walking, ADR-0075) are deployer/build-layer concerns (ADR-0021 Phase 7)
— this class receives an already-resolved `chart` reference and an
already-resolved values file, same split Phase 5 drew for `TF_VAR_`
injection.

`prepare_namespace()` (ADR-0022 D6/D7) is the *build*-time half of that
split: it writes `strata-values.yaml`/`meta.yaml` with `${var:}`/`${secret:}`/
`${feature:}` tokens still in place, verbatim — resolving them is `deploy
run`'s job, not `build run`'s (ADR-0023's value-substitution table), and no
`deploy run` exists yet.
"""

import hashlib
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from strata.integrations.base import ReadinessProblem
from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError, RegistryAuthError
from strata.integrations.helm_cache import ChartPin, ChartPullError, pull_chart

#: A registry chart with no archive verified against the build's pin (docs/work/helm-registry-chart-checks.md).
CHART_NOT_PINNED = "helm_chart_not_pinned"
from strata.integrations.resolved_context import ResolvedModule, ValueResolution
from strata.models.auth_models import AuthenticationModel
from strata.models.common_models import SourceModel
from strata.models.integration_model import Capability
from strata.models.module_model import ModuleModel
from strata.models.namespace_model import NamespaceModel
from strata.models.solution_model import RemoteType, SolutionRemoteModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.transport import CommandResult
from strata.utils.value_tokens import (
    escape_path_segment,
    referenced_output_keys,
    referenced_secret_keys,
    resolve_value_tokens_tracking_secrets,
)
from strata.utils.version_constraints import parse_version_constraint

#: Helm 3 installed where strata passes Helm 4's flags (ADR-0055).
HELM_3_NOT_DECLARED = "helm_3_not_declared"
#: Helm 3 installed and declared: it works, and is deprecated (ADR-0055).
HELM_3_DEPRECATED = "helm_3_deprecated"

#: A `spec.version` that matches neither allows no Helm 4, so it chooses Helm 3's flags. A heuristic: a constraint
#: built to dodge both is misread, and the `spec.version` check then catches the Helm on `PATH`.
_HELM_4_PROBES = ("4.0.0", "4.999.999")

#: What Helm prints when asked about a release it does not have (`uninstall` and `get manifest` both end this
#: way), matched instead of `--ignore-not-found`, whose first Helm version was not verified.
_RELEASE_NOT_FOUND = "release: not found"

#: The module's values, written beside `meta.yaml` and given to Helm with `-f`. Not `values.yaml`: in a local
#: chart that is the chart's own defaults, which Helm reads as the base the `-f` files overlay.
VALUES_FILE = "strata-values.yaml"


class HelmIntegration(InfraIntegration):
    """Helm — installs/upgrades/uninstalls chart releases on Kubernetes."""

    TYPE = "helm"
    CAPABILITIES = frozenset({Capability.CONTAINER})
    TRANSPORTS = frozenset({"cli"})
    COMMAND = "helm"
    VERSION_ARGS = ("version",)
    #: The readiness check reads `helm version` on every run, so Helm 3 without a declaration stops before anything
    #: applies instead of failing at `helm upgrade` with "unknown flag" (ADR-0055).
    READ_VERSION = True

    TARGET_CONTEXT_FIELD = "kube_context"

    def target_context_env(self, context: str) -> dict[str, str]:
        """Helm reads `HELM_KUBECONTEXT` as the default for `--kube-context`, for every subcommand."""
        return {KUBE_CONTEXT_ENV: context}

    def target_context_problem(self, context: str, environ: Mapping[str, str]) -> tuple[str, str] | None:
        """See `kube_context_problem()`; the kubeconfig is read, the cluster is not contacted."""
        return kube_context_problem(context, environ)

    def parse_version(self, raw: str) -> str:
        """Extract `X.Y.Z` from `'version.BuildInfo{Version:"v3.14.0",...}'`."""
        match = re.search(r"v?(\d+\.\d+\.\d+)", raw)
        return match.group(1) if match else raw.strip()

    def helm_major_declared(self) -> int:
        """3 when this integration's `spec.version` allows no Helm 4, else 4: which flags `plan()`/`deploy()` pass.

        Taken from the configuration, never from the Helm on `PATH`, so one configuration gives the same command on
        every machine (ADR-0055).
        """
        declared = self.config.spec.version if self.config is not None else None
        if declared is None:
            return 4
        allowed = parse_version_constraint(declared)
        return 4 if any(allowed.contains(probe, prereleases=True) for probe in _HELM_4_PROBES) else 3

    def version_problems(self, installed: str) -> list[ReadinessProblem]:
        """Helm 3 where Helm 4's flags would be passed. Only the flags are wrong, so `deploy destroy` (`helm
        uninstall`, no such flag) treats it as a warning."""
        if _major(installed) != 3 or self.helm_major_declared() == 3:
            return []
        declared = self.config.spec.version if self.config is not None else None
        if declared is None:
            reason = "strata uses Helm 4 flags unless a helm integration declares Helm 3"
            fix = 'declare spec.version: "<4" on a helm integration'
        else:
            reason = f"spec.version '{declared}' allows Helm 4, so strata uses Helm 4 flags"
            fix = 'narrow it to "<4"'
        return [
            ReadinessProblem(
                HELM_3_NOT_DECLARED,
                f"Helm {installed} found, but {reason}. Install Helm 4, or {fix} (Helm 3 is deprecated).",
                flags_only=True,
            )
        ]

    def version_notices(self, installed: str) -> list[ReadinessProblem]:
        """Declared Helm 3: it works, and is deprecated. No date: the release that removes it decides (ADR-0055)."""
        if _major(installed) != 3 or self.helm_major_declared() != 3:
            return []
        return [
            ReadinessProblem(
                HELM_3_DEPRECATED,
                f"Helm {installed} is deprecated in strata and will stop being supported in a later release. "
                "Install Helm 4 and remove the Helm 3 spec.version.",
            )
        ]

    def prepare_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        resolved: ValueResolution,
    ) -> None:
        """Write one `VALUES_FILE` + one `meta.yaml` per module (ADR-0022
        D6/D7) — Helm never merges, unlike Compose.

        `VALUES_FILE` is always written, `{}` when the module sets nothing,
        so a deploy can always pass it. A local chart's own `values.yaml`
        is left alone: it holds the chart's defaults, and replacing it
        dropped every default the module did not repeat.

        `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` tokens inside a
        service's `environment[].value` are written verbatim, unresolved.
        Helm's own value substitution happens at *deploy* time — secrets
        via a values document on stdin (never on disk or in argv),
        variables/features via a rewritten values file —
        never during `build run` (`resolved` is accepted for signature
        symmetry with every other `InfraIntegration` rendering method, but
        genuinely unused here for that reason).

        Each module's own `VALUES_FILE`/`meta.yaml` land directly in its
        `ResolvedModule.source_path` — already computed and, for a
        git-based `source`, already populated with the module's chart
        directory by the orchestrator (`workload_controller.py`) before
        this is called.
        """
        del resolved
        for item in modules:
            values = _render_values(item.module)
            (item.source_path / VALUES_FILE).write_text(
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
        env: dict[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        chart_archives: Mapping[Path, ChartPin] | None = None,
        on_step: Callable[[str], None] | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> Diagnostics:
        """Deploy every Helm module in `modules` (docs/work/gap_fit_v1.md gap #13).

        One `helm upgrade` per module — `prepare_namespace()`'s own
        already-written `VALUES_FILE`/`meta.yaml` are this method's real
        input, never re-rendered from scratch: `meta.yaml` supplies
        release/namespace/chart coordinates exactly as v1's real
        `HelmDeployer` reads them back (zero new schema needed);
        `VALUES_FILE`'s tokens are resolved via `resolve_module_values()`
        (gap #9 Phase 4) and rewritten in place for non-secret leaves,
        while secret-shaped leaves go to Helm as a second values document
        on stdin (`-f -`), never written to disk or put in argv.

        `dry_run`: still writes the resolved `VALUES_FILE` (a preview
        needs the same real input a `helm upgrade` would use) then calls
        `self.plan_or_warn(...)` (`helm upgrade --dry-run --install`)
        instead of `self.deploy(...)` — never both. A tool-unsupported
        preview is a warning, not a failure (`plan_or_warn()`'s own
        contract); a real plan failure is still an error. The preview's
        output is never streamed: it is the rendered manifest, whose Secret
        `data` is base64 and so beyond the output redaction. `on_step` gets
        one `Kind/name` line per resource instead, as the destroy preview
        does (docs/design/deploy-destroy.md, D7).

        `chart_archives`, keyed by module build directory: the registry charts `deploy_run()` verified
        against the pin `build run` recorded (docs/work/helm-registry-chart-checks.md). A registry-chart
        module is installed from that archive, so no repository or registry is contacted here; one with
        no verified archive is an error (`helm_chart_not_pinned`) and the others still run.

        `timeout` (seconds, the stage's `plan` or `apply` timeout; docs/design/cli-timeout.md) limits each
        module's `helm upgrade` (see `_upgrade_timeouts()`). `None` keeps the defaults.
        """
        del kwargs
        return self._deploy_modules(
            namespace,
            modules,
            tokens=tokens,
            dry_run=dry_run,
            env=env,
            line_callback=line_callback,
            chart_archives=chart_archives or {},
            say=on_step or (lambda message: None),
            timeouts=_upgrade_timeouts(timeout, dry_run=dry_run),
        )

    def _deploy_modules(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        tokens: dict[str, str],
        dry_run: bool,
        env: dict[str, str] | None,
        line_callback: Callable[[str, str], None] | None,
        chart_archives: Mapping[Path, ChartPin],
        say: Callable[[str], None],
        timeouts: dict[str, Any],
    ) -> Diagnostics:
        diagnostics = Diagnostics()
        for item in modules:
            meta_file = item.source_path / "meta.yaml"
            values_file = item.source_path / VALUES_FILE
            missing_files = [path.name for path in (meta_file, values_file) if not path.exists()]
            if missing_files:
                # A build from before `VALUES_FILE` has `meta.yaml` only, and deploying it would drop every value.
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}', module '{item.reference.name}': "
                    f"{' and '.join(missing_files)} not found — run 'strata build run' first.",
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
                chart = str(item.source_path)
            else:
                pin = chart_archives.get(item.source_path)
                if pin is None:
                    diagnostics.error(
                        f"Namespace '{namespace.meta.name}', module '{item.reference.name}': chart "
                        f"'{meta['chartName']}' has no archive verified against the build's pin, so it is not deployed.",
                        location=item.reference.name,
                        code=CHART_NOT_PINNED,
                    )
                    continue
                chart = str(pin.archive)

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
            if resolved_values:
                values_file.write_text(yaml.safe_dump(resolved_values, sort_keys=False, default_flow_style=False))

            if dry_run:
                # Real preview, not a skip — `plan()` needs the same
                # rendered file on disk a real `deploy()` would use, just
                # not followed by one. No `line_callback`: the output is
                # the rendered manifest, Secrets included.
                plan_result = self.plan_or_warn(
                    values_file,
                    diagnostics=diagnostics,
                    location=item.reference.name,
                    release=release,
                    namespace=release_namespace,
                    chart=chart,
                    secret_values=secrets,
                    env=env,
                    **timeouts,
                )
                if plan_result is not None and not plan_result.is_successful:
                    diagnostics.error(
                        f"Namespace '{namespace.meta.name}', module '{item.reference.name}': "
                        f"helm plan (dry-run) failed — {plan_result.stderr}",
                        location=item.reference.name,
                    )
                elif plan_result is not None:
                    resources = summarise_dry_run(plan_result.stdout)
                    say(
                        f"would upgrade release '{release}' (namespace '{release_namespace}'): "
                        f"{len(resources)} resource(s)"
                    )
                    for resource in resources:
                        say(f"  {resource}")
                continue

            result = self.deploy(
                values_file,
                release=release,
                namespace=release_namespace,
                chart=chart,
                secret_values=secrets,
                env=env,
                line_callback=line_callback,
                **timeouts,
            )
            if not result.is_successful:
                diagnostics.error(
                    f"Namespace '{namespace.meta.name}', module '{item.reference.name}': "
                    f"helm upgrade failed — {result.stderr}",
                    location=item.reference.name,
                )
        return diagnostics

    def referenced_secret_keys(self, namespace: NamespaceModel, modules: list[ResolvedModule]) -> set[str] | None:
        """Every `${secret:KEY}` in each module's rendered values, the input `deploy_namespace()` resolves."""
        del namespace
        keys: set[str] = set()
        for item in modules:
            keys |= referenced_secret_keys(_render_values(item.module))
        return keys

    def check_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        tokens: dict[str, str],
        env: dict[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        timeout: int = 60,
        chart_archives: Mapping[Path, ChartPin] | None = None,
        **kwargs: Any,
    ) -> Diagnostics:
        """`helm lint` every module in `modules` (ADR-0043).

        Lints the built module directory with a temporary `-f` overlay of the resolved non-secret values,
        so it sees what `deploy_namespace()` will. Secret-shaped leaves stay as literal tokens: lint needs no
        secret, so none reaches disk or argv. The overlay lives outside the build directory, which a check
        must not alter. A `${output:}` whose upstream step has not applied yet gets a placeholder, because a
        check must not depend on infrastructure that does not exist yet. A registry chart is linted from its
        verified archive in `chart_archives` (docs/work/helm-registry-chart-checks.md) and skipped without one.
        Only `[ERROR]` findings fail (lint exits non-zero on nothing else).
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
            chart_dir = item.source_path
            if (yaml.safe_load(meta_file.read_text()) or {}).get("chartName") is not None:
                pin = (chart_archives or {}).get(item.source_path)
                if pin is None:
                    continue
                chart_dir = pin.chart_dir

            missing = referenced_output_keys(_render_values(item.module)) - tokens.keys()
            try:
                resolved_values, _ = resolve_module_values(item.module, {**dict.fromkeys(missing, "lint"), **tokens})
            except ValueError as exc:
                diagnostics.error(f"{label}: {exc}", location=item.reference.name)
                continue

            with tempfile.TemporaryDirectory(prefix="strata-lint-") as tmp:
                overlay = Path(tmp) / "values.yaml"
                overlay.write_text(yaml.safe_dump(resolved_values, sort_keys=False, default_flow_style=False))
                result = self.lint(overlay, chart=str(chart_dir), timeout=timeout, env=env, line_callback=line_callback)
            if not result.is_successful:
                lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                # Without `[ERROR]` lines (a missing Chart.yaml, say) lint's own message is on stdout, not stderr.
                findings = _error_findings(lines) or [
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
        secret_values: Mapping[str, Any] | None = None,
        timeout: int = 600,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`helm upgrade --dry-run=client --install --namespace ns -f path [-f -] release chart [--version v]`;
        a bare `--dry-run` for declared Helm 3 (`helm_major_declared()`), which has no `=client` before 3.13.

        `secret_values`: a values document read from stdin (`-f -`), after `path` so it wins.

        Raises:
            IntegrationError: `release`/`namespace`/`chart` were not all given.
                Keyword-only with defaults (not required) to stay
                override-compatible with `InfraIntegration.plan`'s
                `**kwargs: Any` signature.
        """
        release, namespace, chart = self._require(release=release, namespace=namespace, chart=chart)
        dry_run = "--dry-run=client" if self.helm_major_declared() >= 4 else "--dry-run"
        args = ["upgrade", dry_run, "--install", "--namespace", namespace, "-f", str(path)]
        stdin = _stdin_values(secret_values)
        if stdin is not None:
            args.extend(["-f", "-"])
        args.extend([release, chart])
        if version:
            args.extend(["--version", version])
        return self.run(*args, cwd=path.parent, env=env, timeout=timeout, input=stdin, line_callback=line_callback)

    def deploy(
        self,
        path: Path,
        *,
        release: str | None = None,
        namespace: str | None = None,
        chart: str | None = None,
        version: str | None = None,
        secret_values: Mapping[str, Any] | None = None,
        create_namespace: bool = True,
        wait: bool = True,
        atomic: bool = True,
        deploy_timeout: str = "5m",
        timeout: int = 600,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`helm upgrade --install [--create-namespace] [--wait=watcher] [--rollback-on-failure] --timeout T
        --namespace ns -f path [-f -] release chart [--version v]`; `--wait` and `--atomic` instead for declared
        Helm 3 (`helm_major_declared()`). `--wait=watcher` is named because `--rollback-on-failure` would otherwise
        pick a wait mode itself.

        `secret_values`: a values document read from stdin (`-f -`), after `path` so it wins.

        Raises:
            IntegrationError: `release`/`namespace`/`chart` were not all given.
        """
        release, namespace, chart = self._require(release=release, namespace=namespace, chart=chart)
        helm_4 = self.helm_major_declared() >= 4
        args = ["upgrade", "--install"]
        if create_namespace:
            args.append("--create-namespace")
        if wait:
            args.append("--wait=watcher" if helm_4 else "--wait")
        if atomic:
            args.append("--rollback-on-failure" if helm_4 else "--atomic")
        args.extend(["--timeout", deploy_timeout, "--namespace", namespace, "-f", str(path)])
        stdin = _stdin_values(secret_values)
        if stdin is not None:
            args.extend(["-f", "-"])
        args.extend([release, chart])
        if version:
            args.extend(["--version", version])
        return self.run(*args, cwd=path.parent, env=env, timeout=timeout, input=stdin, line_callback=line_callback)

    def destroy(
        self,
        path: Path,
        *,
        release: str | None = None,
        namespace: str | None = None,
        timeout: int = 300,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`helm uninstall --namespace ns release`.

        Raises:
            IntegrationError: `release`/`namespace` were not both given.
        """
        if release is None or namespace is None:
            raise IntegrationError(f"{self.name}: 'release' and 'namespace' are required to uninstall a release.")
        return self.run(
            "uninstall", "--namespace", namespace, release, env=env, timeout=timeout, line_callback=line_callback
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
        """Uninstall every Helm module in `modules`, last module first (docs/design/deploy-destroy.md).

        The release and namespace names come from each module's `meta.yaml`, exactly as `deploy_namespace()`
        reads them; nothing else is read, so no value is resolved and no registry login is made.

        `dry_run` previews with `helm get manifest` and sends one `Kind/name` line per resource to `on_step`.
        The manifest itself is never streamed or printed: it embeds Secret `data`, which redaction cannot mask.
        A release Helm does not have is reported as nothing to remove, not as a failure, on both paths, so a
        retry after a partial failure can finish.
        """
        del kwargs
        diagnostics = Diagnostics()
        say = on_step or (lambda message: None)
        for item in reversed(modules):
            label = f"Namespace '{namespace.meta.name}', module '{item.reference.name}'"
            meta_file = item.source_path / "meta.yaml"
            if not meta_file.exists():
                diagnostics.error(
                    f"{label}: meta.yaml not found - run 'strata build run' first.", location=item.reference.name
                )
                continue
            meta = yaml.safe_load(meta_file.read_text()) or {}
            release = str(meta.get("releaseName") or item.reference.name)
            release_namespace = str(meta.get("namespace") or namespace.meta.name)
            where = f"release '{release}' (namespace '{release_namespace}')"

            if dry_run:
                preview = self.get_manifest(release=release, namespace=release_namespace, env=env)
                if preview.is_successful:
                    resources = summarise_manifest(preview.stdout)
                    say(f"would uninstall {where}: {len(resources)} resource(s)")
                    for resource in resources:
                        say(f"  {resource}")
                elif _RELEASE_NOT_FOUND in preview.stderr:
                    say(f"{where} is not installed, nothing to remove")
                else:
                    diagnostics.warning(
                        f"{label}: could not read {where} to preview its removal - {preview.stderr.strip()}",
                        location=item.reference.name,
                    )
                continue

            result = self.destroy(
                item.source_path,
                release=release,
                namespace=release_namespace,
                env=env,
                line_callback=line_callback,
                **({} if timeout is None else {"timeout": timeout}),
            )
            if result.is_successful:
                say(f"uninstalled {where}")
            elif _RELEASE_NOT_FOUND in result.stderr:
                say(f"{where} is already uninstalled")
            else:
                diagnostics.error(
                    f"{label}: helm uninstall failed for {where} - {result.stderr.strip()}",
                    location=item.reference.name,
                    code="helm_uninstall_failed",
                )
        return diagnostics

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

    def pull_registry_chart(
        self,
        meta: Mapping[str, Any],
        remotes: Mapping[str, SolutionRemoteModel],
        *,
        version: str | None,
        cache_root: Path,
        credentials: "RegistryCredentials | None" = None,
        expected_digest: str | None = None,
        diagnostics: Diagnostics | None = None,
        location: str | None = None,
    ) -> ChartPin:
        """Pull the registry chart `meta` names into the `.strata/helm/` cache (docs/work/helm-registry-chart-checks.md).

        Always in an isolated Helm configuration, credentialed or not, so neither `build run` nor the deploy's
        verification touches the user's own repositories or registry logins. `version` may be a range at build;
        the deploy passes the pinned version and `expected_digest`.

        Raises:
            ChartPullError: the chart reference does not resolve, or the pull fails or differs from `expected_digest`.
            RegistryAuthError: logging in with `credentials` failed.
        """
        with ExitStack() as isolated:
            env = _isolated_helm_env(isolated)
            ref, error = _resolve_chart(
                self,
                dict(meta),
                dict(remotes),
                env=env,
                diagnostics=diagnostics,
                location=location,
                credentials=credentials,
            )
            if ref is None:
                raise ChartPullError(error or "the chart reference did not resolve")
            return pull_chart(
                self,
                ref,
                chart=str(meta["chartName"]),
                version=version,
                cache_root=cache_root,
                source=chart_cache_source(meta),
                env=env,
                expected_digest=expected_digest,
            )

    def dependency_list(self, chart_dir: Path, *, timeout: int = 60) -> CommandResult:
        """`helm dependency list chart_dir`: each dependency's status in `charts/`, read offline."""
        return self.run("dependency", "list", str(chart_dir), timeout=timeout)

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


def summarise_manifest(text: str) -> list[str]:
    """`Kind/name` for every resource in a rendered release manifest, and never the manifest itself.

    A manifest embeds Secret `data`, which the output redaction cannot mask, so a destroy preview shows only
    what would be removed (docs/design/deploy-destroy.md, D7). Text that is not YAML is not echoed either.
    """
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return ["(manifest could not be summarised)"]
    lines: list[str] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        metadata = document.get("metadata")
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if document.get("kind") and name:
            lines.append(f"{document['kind']}/{name}")
    return lines


def summarise_dry_run(text: str) -> list[str]:
    """`Kind/name` for every resource `helm upgrade --dry-run` would apply, hooks marked, and nothing else.

    The output holds the rendered `HOOKS:` and `MANIFEST:` sections and then `NOTES:`; only the first two are
    read, through `summarise_manifest()`. The notes are a rendered template too, so they are never shown.
    """
    sections: dict[str, list[str]] = {"HOOKS:": [], "MANIFEST:": []}
    current: list[str] | None = None
    for line in text.splitlines():
        if line in sections:
            current = sections[line]
        elif line == "NOTES:":
            current = None
        elif current is not None:
            current.append(line)
    hooks = [f"{resource} (hook)" for resource in summarise_manifest("\n".join(sections["HOOKS:"]))]
    return summarise_manifest("\n".join(sections["MANIFEST:"])) + hooks


def read_chart_values(chart_dir: Path) -> dict[str, Any]:
    """A local chart's own `values.yaml` as a dict; `{}` when absent, unparseable or not a mapping."""
    try:
        data = yaml.safe_load((chart_dir / "values.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def check_configuration_keys(
    configuration: Mapping[str, Any], chart_values: Mapping[str, Any], subchart_keys: Iterable[str] = ()
) -> list[str]:
    """Findings for `configuration` keys a chart's default `values.yaml` does not declare.

    Ported from v1's `check_helm_values()` (warnings only): top-level keys plus one nested level, with a
    close-match suggestion. An empty default mapping (`env: {}`) is a free-form bag, so its children are not
    checked. No chart defaults means nothing to check against. `subchart_keys` (each dependency's name or alias,
    and `global`) pass Helm on to subcharts, so they and their children are not checked
    (docs/work/helm-chart-dependencies.md).
    """
    if not chart_values:
        return []
    passed_on = set(subchart_keys)
    configuration = {key: value for key, value in configuration.items() if key not in passed_on}

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


def _error_findings(lines: "Sequence[str]") -> list[str]:
    """The `[ERROR]` findings from `helm lint` stdout, each with the lines that continue it.

    Newer helm puts the cause of a template error (`nil pointer evaluating ...`) on lines after the
    `[ERROR]` one, without a marker of their own; keeping only the marked line leaves a location and no reason.
    A continuation ends at the next marker or at lint's closing `Error ...` summary.
    """
    findings: list[str] = []
    continuing = False
    for line in lines:
        if "[ERROR]" in line:
            findings.append(line)
            continuing = True
        elif line.startswith(("==>", "[INFO]", "[WARNING]", "Error")):
            continuing = False
        elif continuing:
            findings[-1] = f"{findings[-1]} {line}"
    return findings


def _render_values(module: ModuleModel) -> dict[str, Any]:
    """Build the `VALUES_FILE` payload for one Helm module.

    Adapted from v1's real `HelmBuilder._render_module_artifacts()`, ported
    to v2's schema: v1 spread a Value binding across four optional
    `value`/`var`/`secret`/`feature` fields on each environment entry; v2
    collapsed that into `ModuleServiceEnvironmentModel.value`, one string
    that may itself contain a `${var:}`/`${secret:}`/`${feature:}` token
    (ADR-0002) — so this only ever copies `.value` verbatim, never branches
    on which kind of reference it is.

    Returns `{}` when the module has neither `spec.services` nor
    `spec.configuration`; `prepare_namespace()` still writes it.
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
    """Resolve Value tokens in one module's rendered `VALUES_FILE` payload
    for deploy-time delivery (docs/work/gap_fit_v1.md gap #9, docs/design/
    value-token-resolution.md's Full Solution Phase 4).

    Reuses `_render_values()`'s own build-time payload (still containing
    unresolved `${var:}`/`${secret:}`/`${feature:}` tokens verbatim, exactly
    what `prepare_namespace()` already wrote to disk) and
    `resolve_value_tokens_tracking_secrets()` (`value_tokens.py`, Phase 3)
    to split it: non-secret leaves resolve in place, safe to rewrite into
    `VALUES_FILE`; secret-shaped leaves are returned as a second values
    document instead, for a caller to give `helm upgrade` on stdin
    (`-f -`) — never written to disk, never in argv.

    Deliberately pure and side-effect-free (matches `_render_values()`'s own
    "testable without touching disk" convention) — writing the resolved
    `VALUES_FILE` back and assembling `helm upgrade`'s actual argv are a
    caller's job (`deploy_namespace()`), not this function's.

    Returns:
        `(resolved_values, secret_values)` — `resolved_values` is what
        `VALUES_FILE` should be rewritten to; `secret_values` holds only
        the branches that lead to a secret-shaped leaf, resolved (`{}`
        when there is none). Helm merges maps but replaces lists, so a
        list that holds a secret is carried whole, every item resolved.
    """
    payload = _render_values(module)
    resolved, secrets = resolve_value_tokens_tracking_secrets(payload, values)
    return resolved, _secret_overlay(resolved, secrets)


def _secret_overlay(node: Any, secrets: Mapping[str, str], path: str = "", *, prune: bool = True) -> Any:
    """The parts of `node` that lead to a path in `secrets` (dotted as `resolve_value_tokens_tracking_secrets()`
    writes them), with each such path's resolved value in place. Below a list nothing is pruned."""

    def _join(segment: str) -> str:
        return f"{path}.{segment}" if path else segment

    if isinstance(node, dict):
        overlay = {}
        for key, value in node.items():
            child = _join(escape_path_segment(key))
            if not prune or any(secret == child or secret.startswith(f"{child}.") for secret in secrets):
                overlay[key] = _secret_overlay(value, secrets, child, prune=prune)
        return overlay
    if isinstance(node, list):
        return [_secret_overlay(value, secrets, _join(str(index)), prune=False) for index, value in enumerate(node)]
    return secrets.get(path, node)


#: Helm's own release name rule (`chartutil.ValidateReleaseName`); checked with `helm template` 3.13.2.
_RELEASE_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")
RELEASE_NAME_MAX_LENGTH = 53
#: A Kubernetes namespace is a DNS-1123 label; the API server rejects anything else.
_KUBERNETES_NAMESPACE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
KUBERNETES_NAMESPACE_MAX_LENGTH = 63


@dataclass(frozen=True)
class ReleaseIdentity:
    """Where one module reference is installed: its release name and Kubernetes namespace, as `meta.yaml` has them.

    `release_from_field`/`namespace_from_field` say whether each came from the module's own field or from a default
    (the reference name, the strata namespace name), because the fix for a bad name differs.
    """

    release: str
    kubernetes_namespace: str
    release_from_field: bool
    namespace_from_field: bool


def release_identity(namespace_name: str, reference_name: str, module: ModuleModel) -> ReleaseIdentity:
    """The release name and Kubernetes namespace a Helm module reference deploys as.

    The release defaults to the reference name, not `module.meta.name` (v1's default): one Module document can
    be attached to a namespace more than once under different reference names (`ModuleReferenceModel`), and two
    live releases can never share a name. The namespace defaults to the strata namespace name. Both values are
    used verbatim; a `${...}` token in either field is not resolved.
    """
    return ReleaseIdentity(
        release=module.spec.release_name or reference_name,
        kubernetes_namespace=module.spec.kubernetes_namespace or namespace_name,
        release_from_field=module.spec.release_name is not None,
        namespace_from_field=module.spec.kubernetes_namespace is not None,
    )


def release_name_problem(name: str) -> str | None:
    """Why Helm rejects `name` as a release name, or `None` when it accepts it."""
    if len(name) > RELEASE_NAME_MAX_LENGTH:
        return f"it is {len(name)} characters long, and Helm allows at most {RELEASE_NAME_MAX_LENGTH}"
    if not _RELEASE_NAME.match(name):
        return (
            "a Helm release name has only lowercase letters, digits, '-' and '.', and starts and ends with a "
            "letter or digit"
        )
    return None


def kubernetes_namespace_problem(name: str) -> str | None:
    """Why Kubernetes rejects `name` as a namespace, or `None` when it accepts it."""
    if len(name) > KUBERNETES_NAMESPACE_MAX_LENGTH:
        return f"it is {len(name)} characters long, and Kubernetes allows at most {KUBERNETES_NAMESPACE_MAX_LENGTH}"
    if not _KUBERNETES_NAMESPACE.match(name):
        return (
            "a Kubernetes namespace has only lowercase letters, digits and '-', and starts and ends with a "
            "letter or digit"
        )
    return None


#: Helm's default for `--kube-context`; set for every helm call of a deployment that names `kube_context`.
KUBE_CONTEXT_ENV = "HELM_KUBECONTEXT"
#: Overrides the server of whichever context Helm uses, so a deployment pinned to a context cannot allow it.
KUBE_API_SERVER_ENV = "HELM_KUBEAPISERVER"


@dataclass(frozen=True)
class KubeconfigContexts:
    """The context names the caller's kubeconfig defines, and the files they were read from."""

    files: list[Path]
    names: list[str]
    unreadable: list[Path]


def kubeconfig_context_names(environ: Mapping[str, str]) -> KubeconfigContexts:
    """The context names in the kubeconfig Helm reads: the `KUBECONFIG` files, else `~/.kube/config`.

    Only `contexts[].name` is read. A file that does not exist is skipped, as Helm skips it; a file that is not
    valid YAML is listed in `unreadable`.
    """
    listed = [part for part in environ.get("KUBECONFIG", "").split(os.pathsep) if part]
    files = [Path(part) for part in listed] if listed else [Path.home() / ".kube" / "config"]
    names: list[str] = []
    unreadable: list[Path] = []
    for path in files:
        if not path.is_file():
            continue
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            unreadable.append(path)
            continue
        contexts = document.get("contexts") if isinstance(document, dict) else None
        for context in contexts if isinstance(contexts, list) else []:
            name = context.get("name") if isinstance(context, dict) else None
            if isinstance(name, str) and name not in names:
                names.append(name)
    return KubeconfigContexts(files=files, names=names, unreadable=unreadable)


def kube_context_problem(kube_context: str, environ: Mapping[str, str]) -> tuple[str, str] | None:
    """Why Helm cannot be pinned to `kube_context` in this environment, as `(code, reason)`, or `None`.

    The caller's own `HELM_KUBECONTEXT` may only repeat the same context, and `HELM_KUBEAPISERVER` would point the
    context at another server. Otherwise the context must be defined in the kubeconfig.
    """
    caller_context = environ.get(KUBE_CONTEXT_ENV)
    if caller_context and caller_context != kube_context:
        return (
            "helm_kube_context_conflict",
            f"{KUBE_CONTEXT_ENV} is set to '{caller_context}' in the environment. Unset it, or deploy with a "
            "deployment that names that context.",
        )
    if environ.get(KUBE_API_SERVER_ENV):
        return (
            "helm_kube_context_conflict",
            f"{KUBE_API_SERVER_ENV} is set in the environment, which overrides the server of the context. Unset it.",
        )
    kubeconfig = kubeconfig_context_names(environ)
    if kube_context in kubeconfig.names:
        return None
    files = ", ".join(str(path) for path in kubeconfig.files)
    source = f"KUBECONFIG={files}" if environ.get("KUBECONFIG") else files
    known = ", ".join(f"'{name}'" for name in kubeconfig.names) or "none"
    unreadable = (
        f" Not valid YAML: {', '.join(str(path) for path in kubeconfig.unreadable)}." if kubeconfig.unreadable else ""
    )
    return (
        "helm_kube_context_missing",
        f"it is not in the kubeconfig ({source}; contexts: {known}).{unreadable}",
    )


def _render_meta(namespace: NamespaceModel, item: ResolvedModule) -> dict[str, Any]:
    """Build the `meta.yaml` payload for one Helm module.

    `releaseName`/`namespace` are `release_identity()`, the same names `strata validate` checks.

    Chart coordinates (`chartName`/`chartVersion`/`chartRemote`/
    `chartRepository`) are included only for a chart-based `source`
    (`chart_name` set) — a registry chart has no local copy for a
    deploy-time `helm upgrade` to reference by path, so `meta.yaml` must
    carry enough for the deployer to pull it directly instead (mirrors
    v1's real "self-contained build artifact" reasoning). Omitted for a
    git-based (local chart) `source`, where `item.source_path` itself is
    the chart to deploy. `chartRemote`/`chartRepository` are mutually
    exclusive on `SourceModel` (docs/work/gap_fit_v1.md gap #2's Option 1) — at
    most one is ever written.
    """
    module = item.module
    identity = release_identity(str(namespace.meta.name), item.reference.name, module)
    meta: dict[str, Any] = {"releaseName": identity.release, "namespace": identity.kubernetes_namespace}

    source = module.spec.source
    if source.chart_name is not None:
        meta.update(chart_coordinates(source))
        pin = item.chart_pin
        if pin is not None:
            meta["chartVersion"] = pin.version
            if source.chart_version != pin.version:
                meta["chartVersionConstraint"] = source.chart_version
            meta["chartDigest"] = pin.digest
            if pin.app_version is not None:
                meta["chartAppVersion"] = pin.app_version
            if pin.subcharts:
                meta["chartSubcharts"] = [{"name": sub.name, "version": sub.version} for sub in pin.subcharts]
        elif source.chart_version is not None:
            meta["chartVersion"] = source.chart_version
    else:
        if item.chart_dependencies:
            meta["chartDependencies"] = [record.to_meta() for record in item.chart_dependencies]
        if item.unpinned_dependencies:
            meta["chartDependenciesUnpinned"] = list(item.unpinned_dependencies)

    return meta


def chart_coordinates(source: SourceModel) -> dict[str, Any]:
    """`chartName` and where it comes from (`chartRemote` or `chartRepository`), as `meta.yaml` records them."""
    coordinates: dict[str, Any] = {"chartName": source.chart_name}
    if source.remote is not None:
        coordinates["chartRemote"] = source.remote
    elif source.chart_repository is not None:
        coordinates["chartRepository"] = source.chart_repository
    return coordinates


def chart_cache_source(meta: Mapping[str, Any]) -> str:
    """The `.strata/helm/` directory a chart's source maps to: the remote name, else the repository's alias."""
    if meta.get("chartRemote") is not None:
        return str(meta["chartRemote"])
    return _repo_alias(str(meta.get("chartRepository") or ""))


# ----------------------------------------------------------------------
# deploy_namespace() helpers — chart reference resolution, registry
# credentials, and the stdin values document for secrets
# (docs/work/gap_fit_v1.md gap #13).
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


def _major(version: str) -> int | None:
    """The major number of `X.Y.Z`, or `None` when `version` does not start with one."""
    match = re.match(r"(\d+)\.", version)
    return int(match.group(1)) if match else None


def _upgrade_timeouts(timeout: int | None, *, dry_run: bool) -> dict[str, Any]:
    """`plan()`/`deploy()` keyword arguments for a stage timeout of `timeout` seconds; `{}` keeps their defaults.

    The stage timeout is the most one `helm upgrade` may take, so it is the process timeout. Helm's own
    `--timeout` gets half of it: the rollback (`--rollback-on-failure`, or `--atomic` on Helm 3) waits that long
    again while it rolls a failed release back, and
    strata must not kill Helm in the middle of a rollback. The defaults (600 s and `5m`) are the same rule for
    600 s. A dry-run waits for nothing, so it gets the process timeout only.
    """
    if timeout is None:
        return {}
    if dry_run:
        return {"timeout": timeout}
    return {"timeout": timeout, "deploy_timeout": f"{max(timeout // 2, 1)}s"}


def _stdin_values(secret_values: Mapping[str, Any] | None) -> str | None:
    """The YAML document `-f -` reads from stdin, or `None` when there is nothing secret to send.

    Replaces v1's `--set-string path=value`, which put every secret in argv (visible in `ps` and Task Manager,
    the leak ADR-0042 rules out) and read a list index in a path as a map key. YAML quotes a string that looks
    like a number or a boolean, so every value still arrives as a string, as `--set-string` guaranteed.
    """
    if not secret_values:
        return None
    return yaml.safe_dump(dict(secret_values), sort_keys=False, default_flow_style=False)


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
    `chartRepository` (an inline URL, docs/work/gap_fit_v1.md gap #2's Option 1)
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
    docstring, docs/work/gap_fit_v1.md gap #2):

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
