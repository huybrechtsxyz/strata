#!/usr/bin/env python3
"""Terraform integration (ADR-0021 Phase 5) — the first real `InfraIntegration`.

Argv assembly per method matches v1's real, production `TerraformIntegration`
exactly (`-var-file`, repeated `-var K=V`, `-out`, `-target`, `-auto-approve`)
— proven, no reason to redesign it. Two things v1 evidence settled without a
design fork:

- **Secrets never travel as `-var` CLI flags.** v1's real deploy path
  (`resolved_values.as_tf_vars()`) injects everything as `TF_VAR_<KEY>`
  **environment variables**, not argv — avoids leaking secrets via `ps`/
  process listings. `Integration.run()` already accepts `env`, so `plan`/
  `deploy`/`destroy` just forward it through; no new machinery needed.
- **No `ensure_available()`-raises guard, unlike v1.** `CommandResult` never
  raises (D5) — an unavailable `terraform` just comes back as
  `CommandResult(returncode=127, ...)`.

Beyond the three `InfraIntegration` methods, this class also has `init`,
`validate`, `output`, `show` — real, evidenced v1 methods (`TerraformIntegration`
itself has all seven; only `init`/`plan`/`apply` were in its own declared
`IInfrastructureTool` Protocol, the rest are called directly by the deployer
holding a concrete reference). Two v1 *deployer steps* are not new methods at
all: `plan_destroy` is `plan(destroy=True)`, and `drift` is
`plan(detailed_exitcode=True)` run without applying — exit code 2 means
"changes present", not failure. Callers of `detailed_exitcode=True` must
inspect `result.returncode` directly, not `.is_successful` (which stays
`returncode == 0` — unchanged, no special-casing added to `CommandResult`).
"""

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.integrations.terraform_projection import build_platform_projection, planned_files
from strata.models.provisioning_model import ProvisionerModel
from strata.utils.transport import CommandResult


class TerraformIntegration(InfraIntegration):
    """Terraform CLI — plans, applies, and destroys infrastructure."""

    TYPE = "terraform"
    CAPABILITIES = frozenset({"infrastructure"})
    TRANSPORTS = frozenset({"cli"})
    COMMAND = "terraform"
    #: v1 precedent: the version subcommand, not a flag.
    VERSION_ARGS = ("version",)

    def parse_version(self, raw: str) -> str:
        """Extract `X.Y.Z` from `"Terraform vX.Y.Z\\non ..."`."""
        match = re.search(r"v(\d+\.\d+\.\d+)", raw)
        return match.group(1) if match else raw.strip()

    # ------------------------------------------------------------------
    # extras beyond InfraIntegration — real v1 methods, not speculative
    # ------------------------------------------------------------------

    def init(
        self,
        path: Path,
        *,
        backend_config: Mapping[str, str] | None = None,
        upgrade: bool = False,
        reconfigure: bool = False,
        timeout: int = 300,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """`terraform init`. Not part of `InfraIntegration` (no `init` in the ABC) —
        the future deployer (ADR-0021 Phase 7) calls this as its own step, same as
        v1's separate `setup` step; never auto-chained inside `plan`/`deploy`/`destroy`.
        """
        args = ["init"]
        for key, value in (backend_config or {}).items():
            args.extend(["-backend-config", f"{key}={value}"])
        if upgrade:
            args.append("-upgrade")
        if reconfigure:
            args.append("-reconfigure")
        return self.run(*args, cwd=path, env=env, timeout=timeout)

    def validate(self, path: Path, *, json_output: bool = False, timeout: int = 60,
                 env: Mapping[str, str] | None = None) -> CommandResult:
        """`terraform validate`. v1's `check` step."""
        args = ["validate"]
        if json_output:
            args.append("-json")
        return self.run(*args, cwd=path, env=env, timeout=timeout)

    def output(
        self,
        path: Path,
        *,
        output_name: str | None = None,
        json_format: bool = False,
        raw: bool = False,
        timeout: int = 60,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """`terraform output`. v1's `output` step."""
        args = ["output"]
        if json_format:
            args.append("-json")
        if raw:
            args.append("-raw")
        if output_name:
            args.append(output_name)
        return self.run(*args, cwd=path, env=env, timeout=timeout)

    def show(
        self,
        path: Path,
        *,
        plan_file: str | None = None,
        json_format: bool = True,
        timeout: int = 60,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """`terraform show` — current state, or decode a saved plan file. v1's `show_plan` step."""
        args = ["show"]
        if json_format:
            args.append("-json")
        if plan_file:
            args.append(plan_file)
        return self.run(*args, cwd=path, env=env, timeout=timeout)

    # ------------------------------------------------------------------
    # InfraIntegration
    # ------------------------------------------------------------------

    def plan(
        self,
        path: Path,
        *,
        var_file: str | None = None,
        variables: Mapping[str, str] | None = None,
        out_file: str | None = None,
        destroy: bool = False,
        detailed_exitcode: bool = False,
        target: Sequence[str] | None = None,
        timeout: int = 600,
        env: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`terraform plan`. Also backs v1's `plan_destroy` step (`destroy=True`) and
        `drift` step (`detailed_exitcode=True`, run without applying) — neither is a
        separate method. With `detailed_exitcode=True`, inspect `result.returncode`
        directly: 0 = no changes, 2 = changes present (not a failure), 1 = error.
        `CommandResult.is_successful` is unchanged (`returncode == 0` only).
        """
        args = ["plan"]
        if var_file:
            args.extend(["-var-file", var_file])
        for key, value in (variables or {}).items():
            args.extend(["-var", f"{key}={value}"])
        if out_file:
            args.extend(["-out", out_file])
        if destroy:
            args.append("-destroy")
        if detailed_exitcode:
            args.append("-detailed-exitcode")
        for resource in target or []:
            args.extend(["-target", resource])
        return self.run(*args, cwd=path, env=env, timeout=timeout)

    def deploy(
        self,
        path: Path,
        *,
        plan_file: str | None = None,
        var_file: str | None = None,
        variables: Mapping[str, str] | None = None,
        auto_approve: bool = False,
        target: Sequence[str] | None = None,
        timeout: int = 1800,
        env: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`terraform apply`. Applies a saved `plan_file` when given; otherwise
        applies `var_file`/`variables`/`target` directly (matching v1: a plan file
        already has variables baked in, so the two modes are mutually exclusive)."""
        args = ["apply"]
        if auto_approve:
            args.append("-auto-approve")
        if plan_file:
            args.append(plan_file)
        else:
            if var_file:
                args.extend(["-var-file", var_file])
            for key, value in (variables or {}).items():
                args.extend(["-var", f"{key}={value}"])
            for resource in target or []:
                args.extend(["-target", resource])
        return self.run(*args, cwd=path, env=env, timeout=timeout)

    def destroy(
        self,
        path: Path,
        *,
        var_file: str | None = None,
        variables: Mapping[str, str] | None = None,
        auto_approve: bool = False,
        target: Sequence[str] | None = None,
        timeout: int = 1800,
        env: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`terraform destroy`."""
        args = ["destroy"]
        if auto_approve:
            args.append("-auto-approve")
        if var_file:
            args.extend(["-var-file", var_file])
        for key, value in (variables or {}).items():
            args.extend(["-var", f"{key}={value}"])
        for resource in target or []:
            args.extend(["-target", resource])
        return self.run(*args, cwd=path, env=env, timeout=timeout)

    # ------------------------------------------------------------------
    # InfraIntegration.default_output (ADR-0023 D1/D5, Phase 1)
    # ------------------------------------------------------------------

    def default_output(
        self,
        resolved: ValueResolution,
        provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph,
    ) -> dict[str, str]:
        """The default tfvars projection (ADR-0023 D1) - one
        `*.auto.tfvars.json` file per non-empty category, Terraform's own
        auto-load convention (no `-var-file` flag needed). `resolved` is
        unused - `flags`/`variables`/`properties`/`custom` (docs/design/
        build-time-value-categories.md) are fed entirely by `graph`'s own
        `variable_refs`/`feature_refs`/`properties`/`custom` fields, not by
        `resolved`/`ValueResolution` - accepted here so
        `InfraIntegration.prepare()`'s uniform dispatch (D5) does not need
        a different call shape once a later phase (Phase 3 token
        substitution) does use it.
        """
        del resolved
        payload = build_platform_projection(graph, provisioner)
        return {filename: json.dumps(data) for filename, data in planned_files(payload)}
