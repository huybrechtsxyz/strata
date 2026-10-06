#!/usr/bin/env python3
"""GitOps (ArgoCD/Flux) integration (docs/design/gitops-integration.md,
Implementation Plan Phase 3) — the first real `InfraIntegration` whose
mechanism is a git commit + push, never a Kubernetes/ArgoCD/Flux API call
(confirmed directly against v1's real `deployers/sync_deployer.py`).

One shared `BaseGitOpsIntegration`, mirroring v1's own `BaseSyncDeployer`
split — `ArgoCDIntegration`/`FluxIntegration` are thin subclasses differing
only in `TYPE` (no behavioural difference exists yet; a real one would only
appear in a `health` check, deliberately not built here — see the design
doc's own "not implemented in this design" note).

`COMMAND = "git"` — not a strata convention, a correctness fix: the real
external dependency this integration needs on `PATH` is `git` itself, not an
`argocd`/`flux` CLI (neither is ever invoked), so `is_available()`'s PATH
check and `get_version()` both need to probe for `git`, not `None` (which
`Integration.is_available()` treats as "unavailable", always failing
`deploy_controller.py`'s preflight for any `COMMAND`-less class). The actual
git plumbing itself runs through `git_push.py`, not through `self.run()` —
`self.run()`/`self.command` exist here solely so the generic
availability/version machinery stays meaningful.

**The real design problem this module had to solve, found only by reading
`deploy_controller.py` directly (not assumed from the design doc alone):**
its generic (non-container) dispatch loop calls `integration.plan(path,
out_file=..., env=env)` and `integration.deploy(path, plan_file=..., env=env)`
with **only** `path` and `env` — never `provisioner`, never a resolved
remote. `build_run()`'s own loop *does* already receive everything needed
(`provisioner`, and now `remotes`/`root`, threaded into its one `prepare()`
call site) but that happens in a wholly separate CLI invocation/process from
`deploy run` — nothing in memory survives between them. The fix: `prepare()`
(called during `build run`) resolves `provisioner.gitops.remote` against the
now-passed `remotes` mapping and writes a small `.gitops-push.json` sidecar
into `path` (the step's on-disk build directory, which — unlike process
memory — *does* survive to `deploy run`, the same reasoning `build_dir()`'s
own docstring already gives for being real, reviewable, on-disk output).
`plan`/`deploy`/`destroy`/`output` then read that sidecar back from `path`
alone, needing no other change to `deploy_controller.py` at all.

This makes the design doc's original "no build_controller.py/
deploy_controller.py changes expected" Implementation Plan claim only
*mostly* true, not fully: one existing `build_controller.py` call site
(`integration.prepare(...)`) gained two additional keyword arguments
(`remotes`, `root`), both accepted for free by `InfraIntegration.prepare()`'s
pre-existing `**kwargs: Any`, so every other integration's `prepare()` call
is unaffected. `deploy_controller.py` itself needed zero changes, since the
sidecar travels via `path` instead.

**Phase 5 (real credentials at actual deploy time)** adds `auth`/
`resolved_values` to `plan`/`deploy`/`destroy`/`output` above — forwarded
straight through to `git_push.py`'s already-Phase-4-ready
`ensure_checkout()`/`push_file()`/`remove_file()`, zero new credential
logic here. `deploy_controller.py` resolves `remote.integration` and
threads `resolved.values` down to these calls **only for a GitOps step**
(`None` for every other tool) — not unconditionally: `collect_step_outputs()`'s
`output()` call is the one real, currently-reachable consumer of a tool's
`output()` method in production, and `TerraformIntegration.output()` has no
`**kwargs: Any` to absorb an unconditionally-passed extra kwarg (found on
review, not assumed).
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.git_push import ensure_checkout, push_file, remove_file
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.models.auth_models import AuthenticationModel
from strata.models.integration_model import Capability
from strata.models.provisioning_model import ProvisionerModel
from strata.models.solution_model import SolutionRemoteModel
from strata.utils import layout
from strata.utils.transport import CommandResult, run_command

#: Sidecar recording what `deploy run`'s later `plan`/`deploy`/`destroy`/
#: `output` calls need, written by `prepare()` (`build run`) into the
#: step's own build directory — see module docstring. Dot-prefixed so it
#: reads as internal bookkeeping, not a rendered artifact a user reviews.
_SIDECAR_FILENAME = ".gitops-push.json"

_GIT_TIMEOUT = 60


def _strip_template_suffix(name: str) -> str:
    """Matches `InfraIntegration.render_output_template()`'s own stripping
    rule exactly (`variables.json.j2` -> `variables.json`) — duplicated,
    not imported, because that logic lives inline in a method body there,
    not its own function."""
    for suffix in (".j2", ".jinja2", ".jinja"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _write_sidecar(
    path: Path, *, root: Path, remote: SolutionRemoteModel, output_file: str, rendered_filename: str
) -> None:
    data = {
        "root": str(root),
        "remote": remote.model_dump(mode="json"),
        "output_file": output_file,
        "rendered_filename": rendered_filename,
    }
    (path / _SIDECAR_FILENAME).write_text(json.dumps(data))


def _read_sidecar(path: Path) -> dict[str, Any] | None:
    sidecar_path = path / _SIDECAR_FILENAME
    if not sidecar_path.is_file():
        return None
    try:
        parsed: Any = json.loads(sidecar_path.read_text())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class BaseGitOpsIntegration(InfraIntegration):
    """Shared `plan`/`deploy`/`destroy`/`output` for ArgoCD/Flux — a git
    commit+push, nothing more (see module docstring)."""

    CAPABILITIES = frozenset({Capability.INFRASTRUCTURE})
    TRANSPORTS = frozenset({"cli"})
    COMMAND = "git"

    def parse_version(self, raw: str) -> str:
        """Extract `X.Y.Z` from `"git version X.Y.Z"`."""
        match = re.search(r"(\d+\.\d+\.\d+)", raw)
        return match.group(1) if match else raw.strip()

    # ------------------------------------------------------------------
    # InfraIntegration.prepare()/default_output() — build time
    # ------------------------------------------------------------------

    def default_output(
        self, resolved: ValueResolution, provisioner: ProvisionerModel, graph: ResolvedWorkspaceGraph
    ) -> dict[str, str]:
        """No default projection exists for a GitOps push (docs/design/
        gitops-integration.md's "Rendering" section's own conclusion) —
        `output.template` is required; this is the honest failure when it
        is not set, same reasoning as the base class's own docstring for
        when a tool genuinely has nothing sensible to generate."""
        del resolved, graph
        raise IntegrationError(
            f"{self.name}: provisioner '{provisioner.name}' (tool '{provisioner.tool}') requires "
            "'output.template' to be set — there is no default values/config projection for a "
            "GitOps push (docs/design/gitops-integration.md)."
        )

    def prepare(
        self,
        path: Path,
        *,
        resolved: ValueResolution,
        provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph,
        template_path: Path | None = None,
        remotes: Mapping[str, SolutionRemoteModel] | None = None,
        root: Path | None = None,
        **kwargs: Any,
    ) -> Path:
        """Validates `output.template` (base behaviour — raises via
        `default_output()` above when unset) then, for a real (non-dry-run)
        build, resolves `provisioner.gitops.remote` against `remotes` and
        persists everything `deploy run`'s later calls need into a sidecar
        under `path` (see module docstring).

        `remotes`/`root` are `None` for `build_controller.py`'s `--dry-run`
        validate-only call (which does not pass them) — in that case only
        template validation runs, matching every other tool's dry-run
        behaviour; nothing is persisted.

        Raises:
            IntegrationError: `output.template` is unset or invalid (base
                behaviour), `provisioner.gitops` is unset, or
                `provisioner.gitops.remote` does not name a declared
                `spec.remotes` entry.
        """
        path = super().prepare(
            path, resolved=resolved, provisioner=provisioner, graph=graph, template_path=template_path, **kwargs
        )
        if remotes is None or root is None:
            return path

        if provisioner.gitops is None:
            raise IntegrationError(
                f"{self.name}: provisioner '{provisioner.name}' (tool '{provisioner.tool}') has no "
                "'gitops' configuration."
            )
        remote = remotes.get(provisioner.gitops.remote)
        if remote is None:
            raise IntegrationError(
                f"{self.name}: provisioner '{provisioner.name}': remote '{provisioner.gitops.remote}' is "
                "not declared in this solution's spec.remotes."
            )
        assert template_path is not None  # guaranteed by the successful super().prepare() call above

        _write_sidecar(
            path,
            root=root,
            remote=remote,
            output_file=provisioner.gitops.output_file,
            rendered_filename=_strip_template_suffix(template_path.name),
        )
        return path

    # ------------------------------------------------------------------
    # InfraIntegration.plan/deploy/destroy/output — deploy time
    # ------------------------------------------------------------------

    def plan(self, path: Path, **kwargs: Any) -> CommandResult:
        """`git diff --no-index` between what's already pushed and the
        freshly-rendered file — a preview, never a mutation. Folds v1's
        separate `check` step in (see the design doc's own "Integration
        class(es)" section) — an unreachable remote surfaces as a failure
        here, there is no separate reachability probe.

        `line_callback`: streams this method's one real subprocess call
        (`git diff`). `deploy()`/
        `destroy()` do **not** support it yet — both delegate to
        `git_push.py`'s `push_file()`/`remove_file()`, which each make
        several internal `run_command()` calls (clone/fetch/verify/reset/
        config/add/commit/push) that would all need their own
        `line_callback` threaded through first; deferred as a separate,
        larger follow-up rather than bundled into this phase (GitOps is
        not a proven Tier-1 critical-path integration per docs/repo
        memory's v1-consumer-usage notes, unlike Terraform/Helm/Compose).
        """
        env = kwargs.get("env")
        auth = kwargs.get("auth")
        resolved_values = kwargs.get("resolved_values")
        line_callback = kwargs.get("line_callback")
        sidecar = _read_sidecar(path)
        if sidecar is None:
            return CommandResult(
                returncode=1, stdout="", stderr=f"{self.name}: no GitOps sidecar at {path} — was prepare() run?"
            )
        remote = SolutionRemoteModel.model_validate(sidecar["remote"])
        root = Path(sidecar["root"])
        rendered_file = path / sidecar["rendered_filename"]
        if not rendered_file.is_file():
            return CommandResult(
                returncode=1,
                stdout="",
                stderr=f"{self.name}: rendered file '{rendered_file}' does not exist — was "
                "output.template rendered (render_output_template()) before plan()?",
            )

        try:
            ensured = ensure_checkout(root, remote, env=env, auth=auth, resolved_values=resolved_values)
        except IntegrationError as exc:
            return CommandResult(returncode=1, stdout="", stderr=str(exc))
        if not ensured.success:
            return CommandResult(returncode=1, stdout="", stderr=ensured.detail)

        checkout_path = layout.gitops_push_checkout_path(root, remote.name, remote.reference or "")
        existing_file = checkout_path / sidecar["output_file"]
        diff_result = run_command(
            ["git", "diff", "--no-index", "--", str(existing_file), str(rendered_file)],
            env=env,
            timeout=_GIT_TIMEOUT,
            line_callback=line_callback,
        )
        # `--no-index` exits 0 (identical) or 1 (differences, including one
        # side missing entirely — "new file") on a successful comparison;
        # only 2+ is a real failure. Normalised to returncode=0 either way
        # (the diff text is the useful payload) — deploy_controller.py's
        # generic loop only ever checks `.is_successful`, never a
        # detailed-exitcode style distinction (unlike TerraformIntegration's
        # own `detailed_exitcode` callers, which inspect `.returncode`
        # directly by convention — not how this generic path is invoked).
        if diff_result.returncode not in (0, 1):
            return CommandResult(returncode=1, stdout="", stderr=f"'git diff --no-index' failed: {diff_result.stderr}")
        return CommandResult(returncode=0, stdout=diff_result.stdout, stderr="")

    def deploy(self, path: Path, **kwargs: Any) -> CommandResult:
        """Push the rendered file (`git_push.push_file()`) — v1's `apply` step."""
        env = kwargs.get("env")
        auth = kwargs.get("auth")
        resolved_values = kwargs.get("resolved_values")
        sidecar = _read_sidecar(path)
        if sidecar is None:
            return CommandResult(
                returncode=1, stdout="", stderr=f"{self.name}: no GitOps sidecar at {path} — was prepare() run?"
            )
        remote = SolutionRemoteModel.model_validate(sidecar["remote"])
        root = Path(sidecar["root"])
        rendered_file = path / sidecar["rendered_filename"]
        if not rendered_file.is_file():
            return CommandResult(
                returncode=1,
                stdout="",
                stderr=f"{self.name}: rendered file '{rendered_file}' does not exist — was "
                "output.template rendered (render_output_template()) before deploy()?",
            )
        message = f"strata: update {sidecar['output_file']} (provisioner step '{path.name}')"
        try:
            result = push_file(
                root,
                remote,
                rendered_file,
                sidecar["output_file"],
                message,
                env=env,
                auth=auth,
                resolved_values=resolved_values,
            )
        except IntegrationError as exc:
            return CommandResult(returncode=1, stdout="", stderr=str(exc))
        if not result.success:
            return CommandResult(returncode=1, stdout="", stderr=result.detail)
        return CommandResult(returncode=0, stdout=result.detail, stderr="")

    def destroy(self, path: Path, **kwargs: Any) -> CommandResult:
        """Remove the rendered file from the config repo and push the
        removal (`git_push.remove_file()`) — v1's `destroy` step."""
        env = kwargs.get("env")
        auth = kwargs.get("auth")
        resolved_values = kwargs.get("resolved_values")
        sidecar = _read_sidecar(path)
        if sidecar is None:
            return CommandResult(
                returncode=1, stdout="", stderr=f"{self.name}: no GitOps sidecar at {path} — was prepare() run?"
            )
        remote = SolutionRemoteModel.model_validate(sidecar["remote"])
        root = Path(sidecar["root"])
        message = f"strata: remove {sidecar['output_file']} (provisioner step '{path.name}')"
        try:
            result = remove_file(
                root, remote, sidecar["output_file"], message, env=env, auth=auth, resolved_values=resolved_values
            )
        except IntegrationError as exc:
            return CommandResult(returncode=1, stdout="", stderr=str(exc))
        if not result.success:
            return CommandResult(returncode=1, stdout="", stderr=result.detail)
        return CommandResult(returncode=0, stdout=result.detail, stderr="")

    def output(
        self,
        path: Path,
        *,
        json_format: bool = True,
        env: Mapping[str, str] | None = None,
        auth: AuthenticationModel | None = None,
        resolved_values: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """`{"commit_sha": {"value": ...}, "remote_path": {"value": ...}}` —
        the same `CommandResult.stdout`-is-JSON shape `collect_step_outputs()`
        duck-types on (confirmed directly: it is not Terraform-specific
        despite its own docstring/comments, docs/design/gitops-integration.md's
        own "resolved" note). Called independently of `deploy()`, so this
        re-derives everything from the sidecar + a fresh, read-only
        `ensure_checkout()` rather than relying on any state `deploy()` left
        behind in memory. `auth`/`resolved_values` (docs/design/
        gitops-integration.md Implementation Plan Phase 5) are explicit
        kwonly params, not pulled from `**kwargs` like `plan`/`deploy`/
        `destroy` above — this method previously discarded `**kwargs`
        wholesale (`del json_format, kwargs`), so there was nothing to
        'read out of' until now."""
        del json_format, kwargs
        sidecar = _read_sidecar(path)
        if sidecar is None:
            return CommandResult(
                returncode=1, stdout="", stderr=f"{self.name}: no GitOps sidecar at {path} — was prepare() run?"
            )
        remote = SolutionRemoteModel.model_validate(sidecar["remote"])
        root = Path(sidecar["root"])

        try:
            ensured = ensure_checkout(root, remote, env=env, auth=auth, resolved_values=resolved_values)
        except IntegrationError as exc:
            return CommandResult(returncode=1, stdout="", stderr=str(exc))
        if not ensured.success:
            return CommandResult(returncode=1, stdout="", stderr=ensured.detail)

        checkout_path = layout.gitops_push_checkout_path(root, remote.name, remote.reference or "")
        sha_result = run_command(["git", "rev-parse", "HEAD"], cwd=checkout_path, env=env, timeout=_GIT_TIMEOUT)
        if not sha_result.is_successful:
            return CommandResult(returncode=1, stdout="", stderr=f"'git rev-parse HEAD' failed: {sha_result.stderr}")

        payload = {
            "commit_sha": {"value": sha_result.stdout.strip()},
            "remote_path": {"value": sidecar["output_file"]},
        }
        return CommandResult(returncode=0, stdout=json.dumps(payload), stderr="")


class ArgoCDIntegration(BaseGitOpsIntegration):
    """ArgoCD — identical mechanism to Flux (git commit+push); differs only
    in `TYPE`. A real behavioural difference would only appear in a `health`
    check (ArgoCD's own reconciliation-status API), deliberately not built
    here (see module docstring)."""

    TYPE = "argocd"


class FluxIntegration(BaseGitOpsIntegration):
    """Flux — identical mechanism to ArgoCD; differs only in `TYPE`."""

    TYPE = "flux"
