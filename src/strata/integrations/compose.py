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
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
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
