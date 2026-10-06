#!/usr/bin/env python3
"""Pydantic models for provisioner and provisioning-step configuration validation.

See ADR-0011 for the conceptual design: `Topology` (grouping) and
`ProvisionerModel` (tool) are deliberately decoupled — `ProvisioningStepModel`
is the only place a tool binding and a set of targets (resources/namespaces)
come together, ordered via `depends_on` (not `priority` — see ADR-0011).

Neither model here is a standalone kind (no `PlatformKind`/`apiVersion`
wrapper, no dedicated service) — both are sub-models used as fields within
the future `Workspace` kind, the same way `SourceModel`/`AuthenticationModel`
are reusable sub-models, not documents of their own.

`ProvisionerModel.version` (this ADR's original field) is **removed** by
ADR-0021 D4 and replaced by `.integration`, naming an `Integration` document
that owns the expected version instead. Three places modelled the same fact
in v1 with nothing arbitrating between them; see that ADR for the evidence
and the reasoning for which one won.
"""

from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator

from strata.models.common_models import (
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    SourceModel,
)
from strata.models.reference_fields import References, RemoteReference
from strata.utils.builtin_types import SYNC_PROVISIONER_TYPES, TERRAFORM_COMPATIBLE_TYPES, ProvisionerType
from strata.utils.dependency_order import topological_order
from strata.utils.names import check_unique_names
from strata.utils.path_safety import validate_relative_path


class ProvisionerBackendModel(PlatformBaseModel):
    """IaC state backend configuration. Only valid when a Provisioner's `tool` is 'terraform'."""

    type: str = Field(
        ..., min_length=1, description="Backend type (e.g. 'terraform_cloud', 's3', 'azurerm', 'gcs', 'local')"
    )
    configuration: dict[str, Any] = Field(
        description="Backend-specific configuration (supports '${var:}'/'${secret:}'/'${feature:}' tokens)"
    )


class ProvisionerAnsiblePropertiesModel(PlatformBaseModel):
    """Typed Ansible properties. Only valid when a Provisioner's `tool` is 'ansible'."""

    playbook: str | None = Field(
        None, description="Playbook file to run, relative to the playbook directory (default: site.yml)"
    )
    inventory: str | None = Field(None, description="Static inventory file path, relative to the playbook directory")
    ssh_private_key_secret: str | None = Field(
        None, description="Name of the secret holding the SSH private key (default: ssh_private_key)"
    )
    extra_vars: dict[str, str] | None = Field(
        None, description="Extra variables passed to ansible-playbook via --extra-vars"
    )


class ProvisionerGitOpsModel(PlatformBaseModel):
    """Only valid when a Provisioner's `tool` is a `SYNC_PROVISIONER_TYPES` member
    (argocd, flux) — docs/design/gitops-integration.md.

    GitOps provisioners render a values/config file (via `output.template`,
    already tool-agnostic — ADR-0023 D3) and push it to an already-declared
    git remote, where the in-cluster ArgoCD Application/Flux Kustomization
    (bootstrapped separately, entirely out of strata's scope) watches for
    changes. This model carries only the push *destination* — not v1's
    loose `stage.backend.integration`/`.remote` (that field is earmarked for
    terraform state config in this schema; reusing it here would be the
    exact "type lie" `RemoteFetch`'s own docstring already warns against
    elsewhere).
    """

    remote: Annotated[PlatformName, RemoteReference()] = Field(
        description="The already-declared git remote (strata.yaml spec.remotes) holding the GitOps config repo."
    )
    output_file: str = Field(
        description="Path, relative to the remote's root, of the file this provisioner renders and pushes."
    )

    @field_validator("output_file")
    @classmethod
    def validate_output_file(cls, v: str) -> str:
        """Reject an absolute path or a '..' segment (found on code review,
        2026-10-02) — `git_push.py` joins this verbatim onto the GitOps
        checkout directory (`checkout_path / output_file`) and writes there
        directly, before `git add` ever runs. `pathlib`'s `/` operator does
        not sanitise '..', and silently *discards* the left side entirely
        when the right side is absolute — an unvalidated `output_file` could
        write anywhere on disk the process can reach. Same guard
        `SourceModel.source_path`/`.target_path` already use
        (`strata.utils.path_safety.validate_relative_path`) — this field is
        the same risk class and had no reason to be the one exception.
        """
        return validate_relative_path(v)


class OutputModel(PlatformBaseModel):
    """The Jinja2 full-file-template escape hatch (ADR-0023 D3;
    docs/design/value-token-resolution.md's "Value Supply Mechanisms", option
    C) - generating an entire custom file `default_output()`'s built-in
    projection doesn't produce. Valid for any `tool` - unlike `backend`/
    `properties`, nothing about this field is tool-specific (same mechanism
    on the workload pipeline's `ModuleModel.spec.output` too, once that's
    wired up).

    `build run` can only **validate** `template` (its referenced names exist
    somewhere in the declared schema - no rendering, since build never has
    every value resolved). The actual render is deploy-time only, blocked on
    `deploy run` not existing yet - see docs/design/value-token-resolution.md.
    """

    template: str | None = Field(
        None,
        description="Workspace-relative path to a Jinja2 template rendered in place of the default output "
        "projection. '@repo/' cross-repo templates not supported yet - no evidenced need for it.",
    )


class ProvisionerModel(PlatformBaseModel):
    """A tool, its source location, and tool-specific config.

    Deliberately has no topology/targets binding (ADR-0011) — see
    `ProvisioningStepModel` for that. `tool` is an open string, not a closed
    `ProvisionerType` enum: v1's real `DeployerFactory` supports
    user-registered provisioner plugins beyond the known built-ins, so a
    custom tool name must remain schema-valid (same reasoning as
    `Module.spec.type`, ADR-0009 Decision 9). Fields below that are only
    meaningful for a specific known tool (`backend` for terraform,
    `properties` for ansible) are only rejected when `tool` is a
    *recognized* built-in that's known to be wrong — an unrecognized `tool`
    (a possible custom plugin) is never rejected by these checks.
    """

    name: PlatformName = Field(description="Unique provisioner name")
    description: str | None = Field(None, description="Optional description for documentation purposes")
    tool: PlatformName = Field(
        description="Tool this provisioner runs (e.g. terraform, ansible, helm, compose, argocd, flux, bicep, "
        "or a custom provisioner plugin name)"
    )
    source: SourceModel | None = Field(
        None,
        description="Source location for this provisioner's IaC/chart code. Required unless `tool` is a known "
        "sync/GitOps type (argocd, flux), which render from the platform artifact instead.",
    )
    backend: ProvisionerBackendModel | None = Field(
        None, description="State backend configuration. Only valid when tool is 'terraform'."
    )
    properties: ProvisionerAnsiblePropertiesModel | None = Field(
        None, description="Typed Ansible properties. Only valid when tool is 'ansible'."
    )
    gitops: ProvisionerGitOpsModel | None = Field(
        None, description="GitOps push destination. Only valid when tool is a sync type ('argocd', 'flux')."
    )
    configuration: dict[str, Any] | None = Field(
        None, description="Tool-specific passthrough configuration, not validated by strata."
    )
    integration: Annotated[PlatformName, References(PlatformKind.INTEGRATION)] | None = Field(
        None,
        description="Name of an Integration document this provisioner binds to, for the tool's expected "
        "version, transport and authentication (ADR-0021 D4). If unset, resolution auto-binds to the sole "
        "compatible registered Integration and errors — rather than guessing — when more than one candidate "
        "exists. Valid for any 'tool' (unlike v1, which only allowed this for terraform/ansible/bicep): every "
        "tool eventually needs a binding, not just the ones v1 happened to build CLI checks for.",
    )
    output: OutputModel | None = Field(
        None,
        description="Jinja2 full-file-template escape hatch, replacing the default output projection. "
        "Valid for any 'tool' (ADR-0023 D3) — build run only validates it, see OutputModel.",
    )
    depends_on: list[PlatformName] | None = Field(
        None,
        description="Names of other Provisioners in this same workspace whose 'source' must be materialised "
        "before this one's, independent of 'execution'/'targets' (ADR-0029). A dependency provisioner named "
        "here does not need its own execution step — it is synced purely to make its files available on disk "
        "for this provisioner's own source code to compose (e.g. a relative Terraform module path) at build "
        "time. Never implies an execution order or deploy-time relationship by itself — see "
        "ProvisioningStepModel.depends_on for that, which names steps, not provisioners.",
    )

    @model_validator(mode="after")
    def validate_source_required_unless_sync(self) -> "ProvisionerModel":
        """`source` is required when `tool` is a *recognized* non-sync built-in.

        v1's `WorkspaceIacModel.validate_provisioner_fields()` does an
        unconditional string check with no unrecognized-tool exception — but
        that's an artifact of simple code, not a deliberate "custom plugins
        can't be sync-like" design decision (v1 never reasoned about this
        case). A custom provisioner plugin could legitimately be its own
        sync/GitOps-style tool needing no `source`, just as argocd/flux are
        built-in — we have no basis to assume otherwise for an unrecognized
        `tool`, so it's not enforced either way. Only *recognized* non-sync
        built-ins (terraform, ansible, ...) are required to have a `source`.
        """
        if self.tool in {t.value for t in SYNC_PROVISIONER_TYPES}:
            return self
        try:
            ProvisionerType(self.tool)
        except ValueError:
            return self  # unrecognized — may be a custom sync-like plugin
        if self.source is None:
            raise ValueError(
                f"Provisioner '{self.name}': 'source' is required for tool '{self.tool}' (not a sync/GitOps type)."
            )
        return self

    @model_validator(mode="after")
    def validate_backend_only_for_terraform(self) -> "ProvisionerModel":
        """`backend` is rejected only for a *recognized* non-terraform built-in.

        v1's real ADR-0071 added strict rejection after finding `backend`/
        `output` validated successfully but were silently ignored for the
        wrong *known* provisioner type (e.g. `tool: ansible`) — a genuine,
        valuable protection, kept here. But a custom/unrecognized `tool` has
        no such known-wrong answer: its own plugin code is the only consumer
        of `ProvisionerModel`, and the plugin author may legitimately reuse
        the typed `backend` shape for their own tool's state config. Adding
        official, first-party support for a brand-new tool (elevating it out
        of "unrecognized") is a code change — updating `ProvisionerType`/
        `TERRAFORM_COMPATIBLE_TYPES` alongside it is an expected part of that
        same contribution, not something to force through schema leniency.
        """
        if self.backend is not None:
            try:
                known = ProvisionerType(self.tool)
            except ValueError:
                return self  # unrecognized — plugin's own code decides
            if known not in TERRAFORM_COMPATIBLE_TYPES:
                raise ValueError(
                    f"Provisioner '{self.name}': 'backend' is only valid for tool 'terraform' or 'opentofu'."
                )
        return self

    @model_validator(mode="after")
    def validate_properties_only_for_ansible(self) -> "ProvisionerModel":
        """`properties` is rejected only for a *recognized* non-ansible built-in.

        Same reasoning as `backend` above: reject a known mismatch (real
        ADR-0071 protection), but allow an unrecognized/custom tool through
        — its own plugin code, not strata core, is the consumer.
        """
        if self.properties is not None:
            try:
                known = ProvisionerType(self.tool)
            except ValueError:
                return self
            if known != ProvisionerType.ANSIBLE:
                raise ValueError(f"Provisioner '{self.name}': 'properties' is only valid for tool 'ansible'.")
        return self

    @model_validator(mode="after")
    def validate_gitops_only_for_sync_tools(self) -> "ProvisionerModel":
        """`gitops` is rejected only for a *recognized* non-sync built-in.

        Same reasoning as `backend`/`properties` above: reject a known
        mismatch, but allow an unrecognized/custom tool through — a custom
        plugin could legitimately be its own sync/GitOps-style tool, same
        leniency `validate_source_required_unless_sync()` already applies.
        """
        if self.gitops is not None:
            try:
                known = ProvisionerType(self.tool)
            except ValueError:
                return self
            if known not in SYNC_PROVISIONER_TYPES:
                raise ValueError(f"Provisioner '{self.name}': 'gitops' is only valid for tool 'argocd' or 'flux'.")
        return self

    @model_validator(mode="after")
    def validate_no_self_dependency(self) -> "ProvisionerModel":
        """A provisioner cannot depend on itself (ADR-0029).

        Mirrors `ProvisioningStepModel.validate_no_self_dependency()` below —
        same rule, same reasoning, different name-space (provisioner names,
        not step names).
        """
        if self.depends_on and self.name in self.depends_on:
            raise ValueError(f"Provisioner '{self.name}' cannot depend on itself.")
        return self


class ProvisioningStepModel(PlatformBaseModel):
    """One recipe entry: run a Provisioner against a set of targets, after other steps.

    `targets` names Resources/Namespaces this step acts on — deliberately
    NOT a Topology reference (ADR-0011: Topology and Provisioning are
    decoupled, both independently reference the same Resource/Namespace
    pool; "this step realizes topology X" is a derived fact, not declared).
    Ordering uses `depends_on` (explicit graph), not `priority` — see
    ADR-0011.
    """

    name: PlatformName = Field(description="Unique step name")
    provisioner: PlatformName = Field(
        description="Name reference to a Provisioner (existence checked once Workspace ties these together)"
    )
    targets: list[PlatformName] = Field(
        ..., min_length=1, description="Resource/Namespace name references this step acts on"
    )
    depends_on: list[PlatformName] | None = Field(
        None, description="Names of other ProvisioningSteps that must complete before this one runs"
    )
    scope: str | None = Field(
        None,
        description="Free-form label for CLI-level step filtering (`strata deploy run --scope <label>`) — "
        "e.g. 'infra'/'apps'. Not a fixed vocabulary (matches v1's real, identically-documented field). "
        "Lives here, not on DeploymentStageModel: whether a step is 'infra' or 'apps' is a fact about what "
        "the step does, not about which environment/deployment runs it (docs/design/deploy-command.md) — "
        "same reasoning as this model's own 'topology is a derived fact, not declared' rule above. Multiple "
        "steps may share a scope label by design; uniqueness is deliberately not enforced.",
    )

    @model_validator(mode="after")
    def validate_unique_targets(self) -> "ProvisioningStepModel":
        """Validate that targets are unique within this step."""
        check_unique_names(self.targets, f"targets in provisioning step '{self.name}'")
        return self

    @model_validator(mode="after")
    def validate_no_self_dependency(self) -> "ProvisioningStepModel":
        """A step cannot depend on itself."""
        if self.depends_on and self.name in self.depends_on:
            raise ValueError(f"Provisioning step '{self.name}' cannot depend on itself.")
        return self


def validate_provisioning_steps(steps: list[ProvisioningStepModel]) -> None:
    """Cross-step validation over a full list of ProvisioningSteps.

    Not a `model_validator` on either model above — both need sibling
    awareness (all steps at once), so this is a plain function, meant to be
    called by whatever container eventually holds a
    `list[ProvisioningStepModel]` (Workspace, once built) — the same way
    `check_unique_names` is a shared utility other models' validators call,
    rather than a method on any single model.

    Validates:
    - Step names are unique.
    - Every `depends_on` name references a real step in `steps`.
    - No cycles in the `depends_on` graph (Kahn's algorithm).
    - Two steps sharing a target must have a `depends_on` edge (direct or
      transitive) between them — undefined execution order on a shared
      target is a hard error, not a warning (ADR-0011).

    Raises:
        ValueError: with all violations joined, if any are found.
    """
    if not steps:
        return

    check_unique_names([s.name for s in steps], "provisioning step names")

    step_names = {s.name for s in steps}
    errors: list[str] = []
    for step in steps:
        for dep in step.depends_on or []:
            if dep not in step_names:
                errors.append(f"Provisioning step '{step.name}': depends_on '{dep}' is not a defined step.")
    if errors:
        raise ValueError("; ".join(errors))

    graph: dict[str, set[str]] = {s.name: set(s.depends_on or []) for s in steps}
    # Order discarded — only used here to detect a cycle. `topological_order()`
    # (docs/design/provisioner-source-dependencies.md) is the same
    # Kahn's-algorithm shape this function used to run inline; build_controller.
    # ordered_by_depends_on() is the other caller, which keeps the real order.
    topological_order(
        list(graph), {name: list(deps) for name, deps in graph.items()}, label="provisioning step depends_on"
    )

    def reachable_from(start: str) -> set[str]:
        seen: set[str] = set()
        stack = list(graph[start])
        while stack:
            node = stack.pop()
            if node not in seen:
                seen.add(node)
                stack.extend(graph.get(node, ()))
        return seen

    reach = {name: reachable_from(name) for name in graph}

    target_owners: dict[str, list[str]] = {}
    for step in steps:
        for target in step.targets:
            target_owners.setdefault(target, []).append(step.name)

    ambiguous: list[str] = []
    for target, owners in target_owners.items():
        if len(owners) <= 1:
            continue
        for i in range(len(owners)):
            for j in range(i + 1, len(owners)):
                a, b = owners[i], owners[j]
                if b not in reach[a] and a not in reach[b]:
                    ambiguous.append(f"steps '{a}' and '{b}' both target '{target}' with no depends_on ordering")
    if ambiguous:
        raise ValueError("Ambiguous provisioning order: " + "; ".join(ambiguous))
