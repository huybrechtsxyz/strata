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
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
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
