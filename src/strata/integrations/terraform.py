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
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.integrations.terraform_projection import build_strata_locals, find_secret_paths, planned_strata_file
from strata.models.integration_model import Capability
from strata.models.provisioning_model import ProvisionerModel
from strata.utils.transport import CommandResult


class TerraformIntegration(InfraIntegration):
    """Terraform CLI — plans, applies, and destroys infrastructure."""

    TYPE = "terraform"
    CAPABILITIES = frozenset({Capability.INFRASTRUCTURE})
    TRANSPORTS = frozenset({"cli"})
    COMMAND = "terraform"
    #: Terraform's own real CLI contract (not a strata convention) — see
    #: `Integration.ENV_VAR_PREFIX`'s own docstring for why this can't be
    #: generalised to Helm/Compose.
    ENV_VAR_PREFIX = "TF_VAR_"
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
        line_callback: Callable[[str, str], None] | None = None,
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
        return self.run(*args, cwd=path, env=env, timeout=timeout, line_callback=line_callback)

    def validate(
        self,
        path: Path,
        *,
        json_output: bool = False,
        timeout: int = 60,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
    ) -> CommandResult:
        """`terraform validate`. v1's `check` step."""
        args = ["validate"]
        if json_output:
            args.append("-json")
        return self.run(*args, cwd=path, env=env, timeout=timeout, line_callback=line_callback)

    def output(
        self,
        path: Path,
        *,
        output_name: str | None = None,
        json_format: bool = False,
        raw: bool = False,
        timeout: int = 60,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`terraform output`. v1's `output` step.

        `**kwargs: Any` added (docs/design/gitops-integration.md
        Implementation Plan Phase 5) purely as defense-in-depth, matching
        this class's own `plan()`/`deploy()` siblings, which already have
        it — found on review that this method was the one real,
        currently-reachable `output()` implementation `deploy_controller.
        collect_step_outputs()` calls in production with no catch-all,
        which an unconditionally-passed extra kwarg would have silently
        broken (caught by that function's own `except TypeError: return
        {}`, degrading every Terraform deployment's `${output:...}`
        resolution to empty). `collect_step_outputs()` itself is also
        fixed to never pass such a kwarg unconditionally — this is
        independent belt-and-suspenders, not the only fix.
        """
        args = ["output"]
        if json_format:
            args.append("-json")
        if raw:
            args.append("-raw")
        if output_name:
            args.append(output_name)
        return self.run(*args, cwd=path, env=env, timeout=timeout, line_callback=line_callback)

    def show(
        self,
        path: Path,
        *,
        plan_file: str | None = None,
        json_format: bool = True,
        timeout: int = 60,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
    ) -> CommandResult:
        """`terraform show` — current state, or decode a saved plan file. v1's `show_plan` step."""
        args = ["show"]
        if json_format:
            args.append("-json")
        if plan_file:
            args.append(plan_file)
        return self.run(*args, cwd=path, env=env, timeout=timeout, line_callback=line_callback)

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
        line_callback: Callable[[str, str], None] | None = None,
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
        return self.run(*args, cwd=path, env=env, timeout=timeout, line_callback=line_callback)

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
        line_callback: Callable[[str, str], None] | None = None,
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
        return self.run(*args, cwd=path, env=env, timeout=timeout, line_callback=line_callback)

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
        line_callback: Callable[[str, str], None] | None = None,
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
        return self.run(*args, cwd=path, env=env, timeout=timeout, line_callback=line_callback)

    # ------------------------------------------------------------------
    # InfraIntegration.default_output (ADR-0023 D1/D5, Phase 1; ADR-0035
    # Phase 4/5)
    # ------------------------------------------------------------------

    #: `src/strata/templates/terraform/strata_variables.tf` — this file
    #: lives at `src/strata/integrations/terraform.py`, so `.parent.parent`
    #: reaches `src/strata/` (same pattern `scaffold_controller.py`'s own
    #: `_TEMPLATE_ROOT` uses for `templates/solution/`).
    _STRATA_VARIABLES_TEMPLATE = (
        Path(__file__).resolve().parent.parent / "templates" / "terraform" / "strata_variables.tf"
    )

    #: Matches a root-module `variable "strata" { ... }` declaration in a
    #: copied `.tf` source — the opt-out check (ADR-0035 Phase 5): a
    #: consumer that declares its own wins, and strata stops generating one.
    _STRATA_VARIABLE_DECLARATION = re.compile(r'variable\s+"strata"\s*\{')

    @classmethod
    def _sources_declare_own_strata_variable(cls, path: Path) -> bool:
        """Scan every top-level `*.tf` file already synced into `path`
        (`sync_source()` already ran before `prepare()` is ever called) for
        an existing `variable "strata" {}` declaration — not recursive,
        matching Terraform's own "a module is exactly one directory" rule.
        No HCL parsing — a plain regex search, consistent with this
        package's existing "no AST manipulation" convention (`terraform_
        projection.py`'s `rewrite_strata_payload()`/Phase 6's planned
        `build_strata_locals()`).
        """
        return any(
            cls._STRATA_VARIABLE_DECLARATION.search(tf_file.read_text(encoding="utf-8"))
            for tf_file in path.glob("*.tf")
        )

    def default_output(
        self,
        path: Path,
        resolved: ValueResolution,
        provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph,
    ) -> dict[str, str]:
        """The default tfvars projection (ADR-0035, docs/design/
        unified-terraform-projection.md Phase 4/5/6) — one `strata.auto.
        tfvars.json` file, always, replacing the old one-file-per-category
        projection entirely, plus two generated files declaring `var.strata`'s
        real type (Phase 5) and reassembling resolved secrets into
        `local.strata.*` (Phase 6) — both skipped whenever `path` already
        has its own `variable "strata"` declaration, the documented
        opt-out (a consumer who opts out of the generated type takes on
        its own secret-merging too, not just its own type). `resolved`/
        `provisioner` are unused — `flags`/`variables`/`properties`/
        `custom` are fed by `graph`'s own fields, not by `ValueResolution`;
        `provisioner` is confirmed unused by every category `build_strata_
        payload()` builds — accepted here so `InfraIntegration.prepare()`'s
        uniform dispatch (D5) does not need a different call shape.
        """
        del resolved
        del provisioner
        filename, data = planned_strata_file(graph)
        output = {filename: json.dumps(data)}
        if not self._sources_declare_own_strata_variable(path):
            output["strata_variables.tf"] = self._STRATA_VARIABLES_TEMPLATE.read_text(encoding="utf-8")
            output["strata_locals.tf"] = build_strata_locals(find_secret_paths(data["strata"]))
        return output
