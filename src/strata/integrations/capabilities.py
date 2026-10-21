#!/usr/bin/env python3
"""Capability ABCs (ADR-0021 D1/D9) — the contract half of the design.

`Integration` (`base.py`) owns identity, configuration, and every transport
a class may speak. These ABCs own only *what the capability requires*,
nothing about how the work travels — that split is what lets one class
(e.g. a future Consul integration) serve both a CLI-configured and an
API-configured solution without a second class.

Core, closed, dispatched-on capabilities only. An `x-`-prefixed capability
(ADR-0021 D9) is carried by `IntegrationModel` but never reaches here —
there is nothing to dispatch it to.
"""

from abc import abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from strata.integrations.base import Integration
from strata.integrations.errors import CheckPhaseUnsupportedError, IntegrationError
from strata.integrations.resolved_context import (
    KeyValueEntry,
    ResolvedModule,
    ResolvedWorkspaceGraph,
    SecretMetadata,
    ValueResolution,
)
from strata.models.integration_model import Capability
from strata.models.namespace_model import NamespaceModel
from strata.models.provisioning_model import ProvisionerModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.templater import render_template, validate_template_references
from strata.utils.transport import CommandResult


class StoreIntegration(Integration):
    """Capability: `variables` / `secrets` / `features`. Resolves a key to a value."""

    @abstractmethod
    def resolve(self, key: str) -> str:
        """Return the value for `key`.

        Raises:
            strata.integrations.errors.ValueResolutionError: `key` does not
                exist, or the store could not be reached/authenticated.
        """

    def set(self, key: str, value: str) -> None:
        """Create or overwrite `key` in the store (docs/design/
        values-secrets-command.md D3 — `values set`/`values rotate`'s write
        primitive).

        Base-implemented, not abstract, same reasoning as `InfraIntegration.
        default_output()` (ADR-0023 D5): every real store resolver
        (Infisical, Azure Key Vault, Azure App Config) can plausibly
        support writes, so a whole second capability class just to gate
        the rare resolver that cannot would be unearned ceremony —
        overriding this one method is enough, and a resolver that never
        will just inherits this default.

        Deliberately a single upsert, not v1's separate `set_*`/`update_*`
        pair: D2 (no implicit generate-on-missing/rotation-on-read) removed
        the only caller that ever needed to distinguish create-vs-overwrite
        — every real write in v2 (`values set`, `values rotate`) is fine
        either way.

        Raises:
            strata.integrations.errors.IntegrationError: This integration
                does not support writes (the base default), or the write
                itself failed (unreachable/unauthenticated store).
        """
        raise IntegrationError(f"{self.name} does not support writes")

    def metadata(self, key: str) -> SecretMetadata | None:
        """Best-effort created/updated timestamps for `key`, or `None` when
        the backend can't report them (docs/design/values-secrets-command.md
        D3 — `values status`'s rotation-age check, Phase 8).

        Base-implemented, not abstract — same reasoning as `set()` above.
        Only meaningful for a secret-capable resolver with a `rotate:` spec
        pointing at it; a variable/feature-only resolver simply never has
        this called against it in practice.

        Raises:
            strata.integrations.errors.IntegrationError: This integration
                does not report secret metadata (the base default).
        """
        raise IntegrationError(f"{self.name} does not report secret metadata")


#: Reserved `KeyValueStoreIntegration.put(if_version=...)` sentinel meaning
#: "this key must not already exist" — the create-only guard a lock's first
#: acquire needs (docs/work/key-value-store-integration.md's "Validated
#: reuse" section), distinct from an ordinary value meaning "must still
#: equal this known version." Matches Consul's/etcd's own documented CAS
#: convention (a nonexistent key's ModifyIndex/mod_revision is `0`) exactly;
#: the Azure/S3 backends special-case this string instead, since their own
#: versions are opaque ETags that could never collide with it.
NEW_KEY_VERSION = "0"


class KeyValueStoreIntegration(Integration):
    """Capability: `key_value`. Raw get/put of a single small value by key,
    with optional compare-and-swap via a backend-returned version tag
    (docs/work/key-value-store-integration.md).

    Deliberately not a `StoreIntegration` (`resolve(key) -> str`, no
    version concept at all) — a real shape mismatch for "read/write a
    small record with optional CAS," not a style preference. A class
    needing both capabilities (e.g. `AzureAppConfigResolver`,
    `AzureKeyVaultResolver`) inherits both ABCs and declares both
    capability strings; nothing about this ABC assumes it's the only one
    a class implements.
    """

    #: Whether `put(if_version=...)` is honored at all, for *any* value
    #: (including `NEW_KEY_VERSION`) — `True` by default, since every real
    #: backend built so far except one supports it natively.
    #:
    #: **Any current or future caller that relies on real compare-and-swap**
    #: (today: only `DeploymentOutputStore`'s read-modify-write; a lock's
    #: create-then-update-then-reacquire lifecycle would be another) **must
    #: check this before ever calling `put(if_version=...)`**, not just
    #: handle the resulting exception — the one known `False` case
    #: (`AzureKeyVaultResolver`, docs/work/key-value-store-integration.md's
    #: Backend 7) still raises `IntegrationError` from `put()` itself if
    #: called anyway, but silently discovering that via a runtime exception
    #: (or, worse, a caller that swallows it as a warning, the way
    #: `DeploymentOutputStore.record()`'s own failed-write handling does) is
    #: exactly the "quiet wrong value" bug class this flag exists to let a
    #: caller pre-empt instead.
    #:
    #: A backend implementer must set this to `False` unless **both**
    #: `if_version=NEW_KEY_VERSION` (create-only) *and* `if_version=<a real
    #: prior version>` (update-only-if-unchanged) are genuinely honored —
    #: a backend supporting only the create-only case (the file backend's
    #: planned design, docs/work/key-value-store-integration.md's Backend 1:
    #: a real atomic create via `O_EXCL`, but no atomic primitive at all for
    #: an *existing* key) is not "supports CAS" for this flag's purpose,
    #: since `DeploymentOutputStore.record()` needs the update case on
    #: every write after the first.
    SUPPORTS_CAS: ClassVar[bool] = True

    @abstractmethod
    def get(self, key: str) -> KeyValueEntry | None:
        """Return the current value and version for `key`, or `None` if it
        does not exist in the store.

        Callers must check `is None`, never truthiness — `None` ("key does
        not exist") and `KeyValueEntry(value=b"", ...)` ("key exists, value
        is empty") are both falsy in Python, but mean genuinely different
        things. `if not entry:` or `if not entry.value:` silently conflate
        them; `if entry is None:` does not.

        Raises:
            strata.integrations.errors.IntegrationError: the store is
                unreachable or unauthenticated.
        """

    @abstractmethod
    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        """Write `value` to `key`, returning the new version.

        Args:
            if_version: three distinct modes, matching the three real
                states a caller needs (a lock's create-then-update-then-
                reacquire lifecycle needs all three):
                - omitted/`None` — unconditional write, no guard.
                - `NEW_KEY_VERSION` — the write only succeeds if `key`
                  does **not already exist**.
                - any other value — the write only succeeds if the
                  backend's current version for `key` still equals it
                  exactly (an ordinary CAS update).

        Raises:
            strata.integrations.errors.KeyValueConflictError: `if_version`
                was given and the condition above didn't hold — the caller
                should re-read and retry its own merge, never force the
                write through by retrying without `if_version`.
            strata.integrations.errors.IntegrationError: the store is
                unreachable/unauthenticated, or this backend cannot honor
                `if_version` at all for the case given.
        """


class InfraIntegration(Integration):
    """Capability: `infrastructure` / `container`. Plans and applies change.

    One ABC for both capability strings (ADR-0021 D6): the label says what
    *kind* of thing is provisioned (Terraform/Ansible/Bicep vs. Compose/
    Helm) for a human reading the document; the contract is identical.
    """

    def prepare(
        self,
        path: Path,
        *,
        resolved: ValueResolution,
        provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph,
        template_path: Path | None = None,
        **kwargs: Any,
    ) -> Path:
        """Render whatever this tool needs into `path` from already-resolved
        values/documents and this provisioner's own typed config. Returns
        the path `plan`/`deploy`/`destroy` should be called against.

        Base-implemented, not abstract (ADR-0023 D5) — every subclass gets
        the same dispatch for free; only `default_output()` varies per
        tool. The `provisioner.backend` token-substitution step (D2) is
        deliberately **not** done here — `prepare()` runs at build time,
        before secrets/integration-backed values are available (ADR-0022
        D4: build renders, deploy resolves), so resolving `backend.
        configuration`'s tokens here would be premature. It happens at
        deploy time instead, in `deploy_controller.py`'s own step loop
        (`resolve_value_tokens_in_mapping()`), delivered straight into
        `integration.init(path, backend_config=...)` — never written back
        into this method's output.

        `provisioner.output.template` (D3, docs/design/
        value-token-resolution.md's Value Supply Mechanisms option C) is
        **validated only, never rendered** — build never has every value
        resolved (secrets, integration-backed variables/features are
        deploy-only), so a "final" render would be dishonest here. When set,
        `default_output()` is skipped entirely and nothing is written for
        this provisioner — the actual render happens at deploy time instead,
        via `render_output_template()` below, called from
        `deploy_controller.py`. `template_path` is the already-resolved
        absolute path (`build_controller.py`'s job, matching
        `sync_source()`'s own "controller resolves paths, integration
        consumes already-resolved ones" split, ADR-0021 D2) — required
        whenever `provisioner.output.template` is set.

        Raises:
            IntegrationError: `provisioner.output.template` is set but its
                template references a name that doesn't exist anywhere in
                the declared schema.
        """
        if provisioner.output and provisioner.output.template:
            assert template_path is not None, "template_path is required when provisioner.output.template is set"
            known_names: dict[str, set[str] | None] = {
                "graph": None,
                "variables": {ref.key for ref in graph.variable_refs},
                "flags": {ref.key for ref in graph.feature_refs},
                "secrets": {ref.key for ref in graph.secret_refs},
                "properties": None,
                "custom": None,
                "provisioner": None,
            }
            errors = validate_template_references(template_path.read_text(), known_names)
            if errors:
                raise IntegrationError(f"output.template '{provisioner.output.template}': " + "; ".join(errors))
            return path

        for filename, content in self.default_output(path, resolved, provisioner, graph).items():
            (path / filename).write_text(content)
        return path

    def render_output_template(
        self,
        path: Path,
        *,
        resolved: ValueResolution,
        provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph,
        template_path: Path,
    ) -> Path:
        """Actually render `provisioner.output.template` (D3) — `prepare()`'s
        deploy-time counterpart, called separately by `deploy_controller.py`
        (docs/design/deploy-command.md's Implementation Plan, phase 8).
        `prepare()` itself only ever validates this field, never renders it
        (see its own docstring) — by the time `deploy run` calls this, every
        value is fully resolved (secrets, integration-backed variables/
        features included), so a real render is finally honest.

        Base-implemented, not abstract, same reasoning as `prepare()`
        (ADR-0023 D5) — nothing about this field is tool-specific
        (`OutputModel`'s own docstring: "Valid for any tool").

        Uses the identical context shape `validate_template_references()`'s
        `known_names` already checked against at build time (`graph`,
        `variables`, `flags`, `secrets`, `properties`, `custom`,
        `provisioner`) — a template that passed that validation is
        guaranteed every name it references exists in this same context.
        `variables`/`flags`/`secrets` are keyed from `graph.variable_refs`/
        `.feature_refs`/`.secret_refs` (which keys belong to which root) but
        valued from `resolved.values` (the real, fully-resolved value) —
        never from the refs' own `.value`, which stays build-time-safe-only
        (`None` for secrets) even when `graph` was assembled for a deploy.

        The rendered file's name is `template_path`'s own basename with a
        trailing `.j2`/`.jinja2`/`.jinja` extension stripped
        (`variables.json.j2` -> `variables.json`) — unchanged otherwise.

        Returns:
            The path the rendered file was written to.

        Raises:
            jinja2.TemplateError: see `render_template()`.
        """
        context: dict[str, Any] = {
            "graph": graph,
            "variables": {ref.key: resolved.values.get(ref.key) for ref in graph.variable_refs},
            "flags": {ref.key: resolved.values.get(ref.key) for ref in graph.feature_refs},
            "secrets": {ref.key: resolved.values.get(ref.key) for ref in graph.secret_refs},
            "properties": graph.properties,
            "custom": graph.custom,
            "provisioner": provisioner,
        }
        rendered = render_template(template_path.read_text(), context)
        name = template_path.name
        for suffix in (".j2", ".jinja2", ".jinja"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        output_path = path / name
        output_path.write_text(rendered)
        return output_path

    def default_output(
        self,
        path: Path,
        resolved: ValueResolution,
        provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph,
    ) -> dict[str, str]:
        """Filename -> content pairs to write when `provisioner.output.template`
        is unset. Base default: nothing
        generated — Bicep's real behaviour (v1's `bicep_builder.py`: copy
        the source, generate nothing); subclasses override only this hook.

        `path` is the destination directory `prepare()` is about to write
        into (already holding this provisioner's synced `.tf`/etc. sources)
        — added for `TerraformIntegration` (ADR-0035 Phase 5), which must
        check whether those sources already declare their own `variable
        "strata" {}` before deciding whether to also write the generated
        `strata_variables.tf`. Unused by every other subclass, same as
        `resolved`/`provisioner`/`graph` already are for this base no-op.
        """
        del path, resolved, provisioner, graph
        return {}

    def prepare_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        resolved: ValueResolution,
    ) -> None:
        """Render every module in `modules` — all attached to `namespace`,
        all sharing one `module.spec.type` (ADR-0022 D6/D7).

        A second, independent rendering path alongside `prepare()`: the
        workload pipeline (`Namespace.spec.modules`) is a disconnected
        input shape from the provisioner pipeline (`ProvisionerModel`/
        `ProvisioningStepModel`) that `prepare()` serves — found while
        checking how v1 shaped this input (ADR-0022 D5). Not abstract:
        most `InfraIntegration` subclasses have nothing to group here
        (`TerraformIntegration` never implements it — D7) so the base
        default raises rather than forcing every subclass to stub it out.

        Unlike `prepare()`, there is no shared base rendering to provide —
        Compose merges every module in `modules` into **one** output file;
        Helm never merges, writing one per module (D6). That real
        per-tool difference is why this method has no `default_output()`-
        style hook: there is no common shape to factor out.

        Raises:
            IntegrationError: This integration does not support
                namespace-scoped module rendering.
        """
        del namespace, modules, resolved
        raise IntegrationError(f"{self.name} does not support namespace-scoped module rendering (prepare_namespace).")

    def deploy_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        tokens: dict[str, str],
        dry_run: bool,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Diagnostics:
        """Deploy every module in `modules` — all attached to `namespace`,
        all sharing one `module.spec.type` (docs/design/gap_fit_v1.md gap #13).

        The deploy-time counterpart to `prepare_namespace()`, one level
        later in the pipeline — same split, same not-abstract/raise-by-
        default shape (`TerraformIntegration` never implements this either,
        the same reason it never implements `prepare_namespace()`: it never
        goes through the namespace/module pipeline at all).

        Callers pass an already type-homogeneous `modules` list (filtered
        by the caller to this integration's own `TYPE`, mirroring v1's real
        `HelmDeployer.validate_workspace()`'s own `if module.spec.type !=
        HELM: continue` filter) — this method does not re-filter.

        Args:
            namespace: The namespace every module in `modules` is attached to.
            modules: Every module of this integration's own type in `namespace`.
            tokens: Fully-resolved `${var:}`/`${secret:}`/`${feature:}`/
                `${output:}` values (`resolved.values` merged with this
                step's dependency-scoped outputs) — same shape
                `deploy_run()`'s existing `tokens` dict already has.
            dry_run: Report what would happen without touching disk or
                running any command.
            env: Extra environment variables for the underlying command
                (matches every other `InfraIntegration` method's `env` kwarg).

        Returns:
            One error per module that failed to deploy — never raises for
            a single module's failure, so the rest of `modules` still runs.

        Raises:
            IntegrationError: This integration does not support
                namespace-scoped module deployment.
        """
        del namespace, modules, tokens, dry_run, env, kwargs
        raise IntegrationError(f"{self.name} does not support namespace-scoped module deployment (deploy_namespace).")

    def check_namespace(
        self,
        namespace: NamespaceModel,
        modules: list[ResolvedModule],
        *,
        tokens: dict[str, str],
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Diagnostics:
        """Check every module in `modules` without deploying anything — the container-capable
        counterpart to Terraform's `validate()` (ADR-0043).

        Same not-abstract/raise-by-default shape as `deploy_namespace()`: an integration with no check
        phase (Compose) raises `CheckPhaseUnsupportedError`, and `deploy_run()` reports that as "no check
        phase" instead of failing. Any other `IntegrationError` is a real failure of the check itself.

        Returns:
            One error per module that failed its check; the rest of `modules` is still checked.

        Raises:
            CheckPhaseUnsupportedError: This integration has no namespace-scoped check phase.
        """
        del namespace, modules, tokens, env, kwargs
        raise CheckPhaseUnsupportedError(
            f"{self.name} does not support namespace-scoped module checking (check_namespace)."
        )

    @abstractmethod
    def plan(self, path: Path, **kwargs: Any) -> CommandResult:
        """Preview the change `path`'s code would make, without applying it."""

    def plan_or_warn(
        self, path: Path, *, diagnostics: Diagnostics, location: str, **kwargs: Any
    ) -> CommandResult | None:
        """Call `self.plan(path, **kwargs)`, folding an `IntegrationError` into
        a warning instead of propagating it — "preview if the tool
        supports it, otherwise just say so."
        `deploy_run()`'s real `--dry-run` preview and both Helm/Compose
        `deploy_namespace()` implementations call this instead of `plan()`
        directly, so the graceful-degradation rule lives in exactly one
        place rather than being duplicated per call site.

        Base-implemented, not abstract, same "every subclass gets this for
        free" precedent as `prepare()`/`default_output()` (ADR-0023 D5).
        Public (no leading underscore) despite being a thin wrapper around
        `plan()` — unlike a true private implementation detail, this is
        genuinely called across module boundaries (`deploy_controller.py`),
        matching every other extension point on this class.

        Since `plan()` is `@abstractmethod` here, every current subclass
        (Terraform/Helm/Compose/GitOps) must implement *something* — none
        of them are expected to actually raise `IntegrationError` from
        `plan()` today; this exists for a *future* integration that might,
        so today's behaviour for all four is unchanged by its existence.

        Returns:
            `None` when `plan()` itself isn't meaningfully supported —
            callers should treat `None` as "skip, a diagnostic was already
            recorded" — or the real `CommandResult` otherwise. Callers
            still check `.is_successful` themselves: a `plan()` that runs
            but fails for a real, tool-specific reason (bad backend
            config, invalid HCL, unreachable remote) stays a hard error,
            same severity as a real `plan()` failure on the apply path —
            only a missing *capability* is graceful.
        """
        try:
            return self.plan(path, **kwargs)
        except IntegrationError as exc:
            diagnostics.warning(f"no plan preview available for '{self.name}' — {exc}", location=location)
            return None

    @abstractmethod
    def deploy(self, path: Path, **kwargs: Any) -> CommandResult:
        """Apply the change `path`'s code describes."""

    @abstractmethod
    def destroy(self, path: Path, **kwargs: Any) -> CommandResult:
        """Tear down what `path`'s code previously created."""


class AuditSinkIntegration(Integration):
    """Capability: `audit`. Delivers one rendered audit event to a destination.

    The contract half of docs/design/audit-sink-dispatch.md's Layer 4
    dispatch. Deliberately takes an already-rendered `dict`, not a manifest/
    metrics model: `controllers/audit_event_rendering.py` owns the
    CloudEvents 1.0 + ECS envelope for *every* sink, so a concrete class
    never re-derives the wire shape and two sinks can never disagree about
    what an event looks like.

    One event per call, never a batch (that design's D5) — matches the
    renderers' own one-event-per-call shape and the per-type `events`
    filter already applied upstream in `_dispatch_sink()`.
    """

    @abstractmethod
    def send(self, event: dict[str, Any]) -> None:
        """Deliver one rendered audit event.

        Args:
            event: A rendered CloudEvents 1.0 + ECS envelope (see
                `controllers/audit_event_rendering.py`).

        Raises:
            strata.integrations.errors.IntegrationError: The event could not
                be delivered. The caller (`_dispatch_sink()`) decides whether
                that warns or fails the run, from the sink's own `required`
                flag — a concrete class never makes that call itself.
        """


class ScannerIntegration(Integration):
    """Capability: `scanner`. Detects an external security scanner and
    raises a clear, actionable error when it isn't installed.

    Deliberately thin — unlike `StoreIntegration.resolve()` or
    `InfraIntegration.plan()`/`.deploy()`/`.destroy()`, this ABC does NOT
    force a shared `scan()` method signature across every scanner-type
    integration. `CveScannerIntegration` (an SBOM file in,
    `CveAuditResultModel` out) and a future Checkov integration (an IaC
    directory in, a completely different findings model out, docs/archive/
    checkov-integration.md) have genuinely different inputs and outputs —
    that doc's own explicit stance is "do not conflate the two or build
    one in a way that assumes it's the other." Forcing one `scan()`
    signature here would be exactly that conflation. What IS genuinely
    identical across both: the "the tool isn't installed" failure mode —
    `CveScannerIntegration`'s own real, already-shipped `ensure_available()`
    is the contract every scanner-type integration implements the same way.
    """

    @abstractmethod
    def ensure_available(self) -> None:
        """Raise when the underlying scanner tool(s) are not installed.

        Raises:
            strata.integrations.errors.IntegrationError: Always, when
                unavailable — with an actionable message (what to install,
                from where).
        """


#: Capability -> the ABC a class declaring it must implement. Keyed by
#: `str`, not `Capability`, even though every key is a `Capability` member
#: (`Capability <: str`, so this stays assignable) — `find_capability_
#: mismatches()` below looks up an `Integration.CAPABILITIES` entry, which
#: stays `frozenset[str]` (it must also accept `x-`-prefixed extensions
#: with no enum member), so a `dict[Capability, ...]` key type would reject
#: a plain-`str` lookup under strict mypy.
#: `Capability.SOURCES` has no entry yet — remote fetching is not built
#: (ADR-0021 D9); a capability with no entry here is declarable but not
#: dispatchable, which `find_capability_mismatches` treats as compliant,
#: not an error.
CAPABILITY_ABCS: dict[str, type[Integration]] = {
    Capability.VARIABLES: StoreIntegration,
    Capability.SECRETS: StoreIntegration,
    Capability.FEATURES: StoreIntegration,
    Capability.INFRASTRUCTURE: InfraIntegration,
    Capability.CONTAINER: InfraIntegration,
    Capability.AUDIT: AuditSinkIntegration,
    Capability.SCANNER: ScannerIntegration,
    Capability.KEY_VALUE: KeyValueStoreIntegration,
}


def find_capability_mismatches(integration_cls: type[Integration]) -> list[str]:
    """Every core capability `integration_cls` declares whose ABC it does not implement.

    A class declaring a capability whose contract it doesn't satisfy is a
    programming error, not something a document author can trigger — this
    is what catches it. Used directly here (Phase 3, against fake test
    classes) and reused by the registry's own test once real classes exist
    to walk (ADR-0021 Phase 4).

    Args:
        integration_cls: The class to check, not an instance — the pairing
            is a property of the class, checkable before anything is
            configured or constructed.

    Returns:
        One message per mismatch. Empty when `integration_cls` is compliant,
        which includes declaring only capabilities with no ABC yet.
    """
    mismatches = []
    for capability in integration_cls.CAPABILITIES:
        abc = CAPABILITY_ABCS.get(capability)
        if abc is not None and not issubclass(integration_cls, abc):
            mismatches.append(
                f"{integration_cls.__name__} declares '{capability}' but does not implement {abc.__name__}"
            )
    return mismatches
