#!/usr/bin/env python3
"""Pydantic model for deployment configuration validation.

A `Deployment` is the "container instance" of ADR-0011's image/container
framing: it executes a `Workspace`'s provisioning recipe against a specific
`Environment`, for a specific `Tenant`. The Workspace says *what* runs and in
what order; the Deployment says *where, with which values, and with what
runtime behaviour*.

Ported from the **15 real deployment documents** across two production repos.
Spec field usage there:

==================  =====
field               uses
==================  =====
``environments``      15
``locking``           12
``layers``            10
``stages``             9
``workspace``          9
``extends``            6
``partial``            4
``tenant``             4
``configurations``     3
``lifecycle``          3
``properties``         3
``versions``           0
``promotion``          0
``gates``              0
==================  =====

`versions`, `promotion` and `gates` are not ported — zero usage, same
treatment Environment's unused subtree got.

**Stages reference the recipe rather than redefining it.** v1's
`DeploymentStageModel` carried `provisioner` and `topology`, i.e. it bound
tools at deploy time — and v1's workspaces have no recipe at all, so stages
*were* the recipe. v2 moved that to `Workspace.execution` (ADR-0011), and
the real usage supports it: the stage list lives on the reusable
`partial: true` base and never varies per leaf, while leaves supply only
layers/environments/locking. So here a stage names a
`ProvisioningStepModel` via `step` and carries only runtime knobs — the same
class/instance split as `WorkspaceResourceModel.resource` and
`ModuleReferenceModel.module`.
"""

from collections.abc import Sequence
from typing import Annotated, Any, ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from strata.models.common_models import (
    CommonLifecycleModel,
    PlatformBaseModel,
    PlatformKind,
    PlatformName,
    PlatformVersion,
    ScriptsModel,
    validate_kind_matches,
)
from strata.models.reference_fields import References
from strata.models.template_model import DocumentTemplateModel, ParamSetNames, check_name_rule
from strata.utils.names import check_unique_names

#: `Deployment.spec.version`: one Version document, or a list of them whose pins apply together
#: (docs/design/version-composition.md V1). The `ParamSetNames` pattern: each name is a reference.
VersionNames = (
    Annotated[PlatformName, References(PlatformKind.VERSION)]
    | list[Annotated[PlatformName, References(PlatformKind.VERSION)]]
)


#: `--pin` as the controllers take it: one version document, several (`--pin a --pin b`), or `None` when the flag
#: was not given and the deployment's own `spec.version` applies.
VersionPins = str | Sequence[str] | None


def version_name_list(value: str | Sequence[str] | None) -> list[str]:
    """`Deployment.spec.version` (or a `--pin` set) as a list in written order, duplicates removed: `None` is `[]`."""
    if value is None:
        return []
    names = [value] if isinstance(value, str) else list(value)
    return list(dict.fromkeys(name for name in names if name))


def _health_check_replacement(probe: Any) -> str:
    """A removed `health_checks` entry written as the `verify.checks` entry that replaces it."""
    if not isinstance(probe, dict):
        return "{...}"
    keys = ("url", "expect_status") if probe.get("type") == "http" else ("host", "port")
    fields = [f"name: {probe.get('name')}", f"type: {probe.get('type')}"]
    fields += [f"{key}: {probe[key]}" for key in (*keys, "timeout") if probe.get(key) is not None]
    return "{" + ", ".join(fields) + "}"


class DeploymentStageTimeoutsModel(PlatformBaseModel):
    """Per-phase timeouts in seconds that override a provisioner's defaults. The environment variable
    `STRATA_TIMEOUT_<STEP>_<PHASE>` overrides them for a single run.

    Design notes:
    Per-phase timeouts in seconds, overriding provisioner defaults.
    """

    setup: int | None = Field(None, ge=1, description="Timeout for the setup phase")
    check: int | None = Field(None, ge=1, description="Timeout for the check/validate phase")
    plan: int | None = Field(None, ge=1, description="Timeout for the plan phase")
    apply: int | None = Field(None, ge=1, description="Timeout for the apply phase")
    destroy: int | None = Field(None, ge=1, description="Timeout for the destroy phase")
    verify: int | None = Field(
        None,
        ge=1,
        description="Timeout for the verify phase: every check of the stage's `verify` block together. Without it, "
        "the checks' own timing bounds the phase.",
    )


#: The timing fields a `verify` block sets and a check may override: Kubernetes probe fields, in seconds
#: (docs/design/post-deploy-verification.md, D4).
VERIFY_TIMING_FIELDS = ("initial_delay", "period", "timeout", "success_threshold", "failure_threshold")


#: A verify check's `when`: after an apply (and under `deploy verify`), or after a destroy.
VerifyWhen = Literal["apply", "destroy"]

#: The fields each check `type` may set, beyond the shared ones (`name`, `enabled`, `when`, the timing fields).
_VERIFY_TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    "script": ("run", "env", "exit_codes"),
    "http": ("url", "method", "headers", "body", "expect_status", "ca_file", "insecure", "expect"),
    "tcp": ("host", "port", "expect"),
    "helm": ("release", "namespace", "tests", "env"),
}


class DeploymentVerifyCheckModel(PlatformBaseModel):
    """One check run after the step is applied (or destroyed, with `when: [destroy]`).

    - `script` runs `run` and reads its exit code: with `exit_codes: binary` (default) 0 passes and anything else
      fails; with `monitoring` (the Nagios/Icinga plugin convention) 0 passes, 1 warns, 2 fails and 3 is unknown. A
      command that times out or cannot start is unknown. The first line it prints is the check's message.
    - `http` requests `url` and passes when the status is in `expect_status`; an `application/health+json` answer's
      `status` (`pass`, `warn`, `fail`) decides instead. No answer at all fails. `ca_file` trusts a private CA;
      `insecure` skips the certificate check.
    - `tcp` passes when a connection to `host`:`port` opens.
    - `helm` runs the chart's own tests (`helm test release`) once, against the deployment's `kube_context`: 0
      passes, anything else fails. Only after an apply.

    `expect: down` turns an `http` or `tcp` check around, for a destroy: it passes when there is no answer (or not
    the expected status). Timing fields left out come from the `verify` block.

    Design notes:
    docs/design/post-deploy-verification.md, D5, D6, D10. `run` is an argument list, never a shell string. `when`, not
    `on`: YAML 1.1 (PyYAML) reads an `on:` key as `true`.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "`run` is an argument list: the executable, then its arguments.",
        "An enabled `script` check needs `run`; an `http` check needs `url`; a `tcp` check needs `host` and `port`; "
        "a `helm` check needs `release`. A field of another type is refused. Judged on the merged deployment: in a "
        "deployment that `extends` a base, a check writes only what it changes in the base's check of that `name`.",
        "A `helm` check runs only after an apply: `when: [destroy]` is refused.",
    )

    name: PlatformName = Field(description="Unique check name within the `verify` block")
    enabled: bool = Field(
        default=True,
        description="Run this check. `false` skips it: how a deployment that `extends` a base turns off one of the "
        "base's checks (checks merge by `name`).",
    )
    type: Literal["script", "http", "tcp", "helm"] = Field(
        default="script",
        description="Check type: `script` (a command's exit code), `http` (a request's status), `tcp` (a "
        "connection) or `helm` (the chart's own tests, `helm test`).",
    )
    when: list[VerifyWhen] = Field(
        default_factory=lambda: list[VerifyWhen](["apply"]),
        min_length=1,
        description="When the check runs: after an `apply` (default; also under `deploy verify`), after a "
        "`destroy`, or both. A destroy check confirms the workload is gone.",
    )
    run: list[str] | None = Field(
        None,
        min_length=1,
        description="`script`: the command, as a list: the executable (on PATH, or a path relative to the solution "
        "root), then its arguments. Tokens (`${var:}`, `${secret:}`, `${output:STEP.KEY}`, ...) are resolved. Run "
        "from the solution root, never through a shell.",
    )
    env: dict[str, str] | None = Field(
        None,
        description="Extra environment variables for the command; tokens are resolved. Prefer this to `run` for a "
        "secret: an argument shows in the process list.",
    )
    exit_codes: Literal["binary", "monitoring"] = Field(
        default="binary",
        description="`script`: how the exit code is read: `binary` (0 passes, anything else fails) or `monitoring` "
        "(0 passes, 1 warns, 2 fails, 3 is unknown).",
    )
    url: str | None = Field(
        None, description="`http`: the `http://` or `https://` URL to request; tokens are resolved."
    )
    method: Literal["GET", "HEAD", "OPTIONS", "POST"] = Field(
        default="GET",
        description="`http`: the request method. `POST` is for a health endpoint that takes a query (GraphQL, a "
        "JSON-RPC ping); a check should never change what it checks.",
    )
    headers: dict[str, str] | None = Field(
        None,
        description="`http`: request headers; tokens are resolved, so `Authorization: Bearer ${secret:TOKEN}` works.",
    )
    body: str | None = Field(
        None,
        description="`http`: the request body, sent with `method: POST`; tokens are resolved. Set its "
        "`Content-Type` in `headers`.",
    )
    # At least one status: with none, an `expect: up` check could never pass and an `expect: down` one never fail.
    expect_status: (
        Annotated[int, Field(ge=100, le=599)]
        | Annotated[list[Annotated[int, Field(ge=100, le=599)]], Field(min_length=1)]
    ) = Field(
        default=200,
        description="`http`: the status (or statuses, at least one) that mean the workload answers. A redirect is "
        "followed first.",
    )
    ca_file: str | None = Field(
        None,
        description="`http`: a PEM file of the certificate authorities to trust instead of the system's, for a "
        "private CA or a self-signed certificate; a path relative to the solution root. The certificate and host "
        "name are still checked.",
    )
    insecure: bool = Field(
        default=False,
        description="`http`: `true` skips the certificate check altogether. The check's message says so. Prefer "
        "`ca_file`, which still checks who answers.",
    )
    host: str | None = Field(None, description="`tcp`: the host name or address to connect to; tokens are resolved.")
    port: int | None = Field(None, ge=1, le=65535, description="`tcp`: the port to connect to")
    release: str | None = Field(
        None, description="`helm`: the release whose tests run, as the step installed it; tokens are resolved."
    )
    namespace: str | None = Field(
        None, description="`helm`: the release's Kubernetes namespace; tokens are resolved. Helm's default without it."
    )
    tests: list[str] | None = Field(
        None,
        min_length=1,
        description="`helm`: run only these tests (their pod names, `helm test --filter name=...`); all of them "
        "without it.",
    )
    expect: Literal["up", "down"] = Field(
        default="up",
        description="`http`/`tcp`: `up` (default) passes when the workload answers; `down` passes when it does not "
        "(no connection, or for `http` a status not in `expect_status`), for a check after a destroy.",
    )
    initial_delay: int | None = Field(None, ge=0, description="Seconds to wait before this check's first attempt")
    period: int | None = Field(None, ge=1, description="Seconds from one attempt's start to the next")
    timeout: int | None = Field(None, ge=1, description="Seconds one attempt may take; the command is then stopped")
    success_threshold: int | None = Field(None, ge=1, description="Consecutive passes needed")
    failure_threshold: int | None = Field(
        None, ge=1, description="Attempts that fail or are unknown before it gives up"
    )

    @field_validator("run")
    @classmethod
    def validate_run(cls, v: list[str] | None) -> list[str] | None:
        """The executable must be named."""
        if v is not None and not v[0].strip():
            raise ValueError("run: the first element is the executable and must not be empty")
        return v

    @field_validator("when")
    @classmethod
    def validate_when(cls, v: list[VerifyWhen]) -> list[VerifyWhen]:
        """Each action once."""
        return list(dict.fromkeys(v))

    def problems(self) -> list[str]:
        """What makes the check unusable: a required field of its type missing (only when enabled: a disabled check
        never runs), a field of another type set, or an option combination that cannot work.

        Judged on a complete deployment (`DeploymentSpecModel.validate_verify_checks()`), not on the check alone: in
        a document that `extends` a base, a check may name only what it changes (`{name: web, enabled: false}`), its
        type and other fields coming from the base's check of the same name."""
        problems: list[str] = []
        required = {"script": ("run",), "http": ("url",), "tcp": ("host", "port"), "helm": ("release",)}[self.type]
        missing = [name for name in required if getattr(self, name) is None]
        if missing and self.enabled:
            problems.append(f"type '{self.type}' needs {' and '.join(repr(n) for n in missing)}")
        own = _VERIFY_TYPE_FIELDS[self.type]
        # By value, not `model_fields_set`: a merged check holds every field the base or the child set.
        fields = type(self).model_fields
        foreign = sorted(
            {
                name
                for other, names in _VERIFY_TYPE_FIELDS.items()
                if other != self.type
                for name in names
                if name not in own and getattr(self, name) != fields[name].get_default(call_default_factory=True)
            }
        )
        if foreign:
            problems.append(f"{', '.join(repr(n) for n in foreign)} do not apply to type '{self.type}'")
        if self.type == "helm" and "destroy" in self.when:
            problems.append(
                "a 'helm' check runs the release's tests, so it cannot run after a destroy; check that the workload "
                "is gone with an 'http' or 'tcp' check and 'expect: down'"
            )
        if self.body is not None and self.method != "POST":
            problems.append("'body' is sent only with method 'POST'")
        if self.ca_file is not None and self.insecure:
            problems.append(
                "'ca_file' and 'insecure' exclude each other: 'ca_file' checks the certificate against your CA, "
                "'insecure' skips the check"
            )
        return [f"verify check '{self.name}': {problem}" for problem in problems]

    def expected_statuses(self) -> list[int]:
        """`expect_status` as a list."""
        return [self.expect_status] if isinstance(self.expect_status, int) else list(self.expect_status)

    def runs_on(self, action: str) -> bool:
        """Whether the check runs for `action`: `apply` and `verify` (the stand-alone command) run the `apply`
        checks, `destroy` the `destroy` ones."""
        return ("destroy" if action == "destroy" else "apply") in self.when

    def token_fields(self) -> list[tuple[str, str]]:
        """Every field whose `${...}` tokens are resolved, as `(path below the check, text)`."""
        fields = [(f"run.{i}", text) for i, text in enumerate(self.run or [])]
        fields += [(f"env.{key}", text) for key, text in (self.env or {}).items()]
        if self.url is not None:
            fields.append(("url", self.url))
        fields += [(f"headers.{key}", text) for key, text in (self.headers or {}).items()]
        if self.body is not None:
            fields.append(("body", self.body))
        if self.host is not None:
            fields.append(("host", self.host))
        if self.release is not None:
            fields.append(("release", self.release))
        if self.namespace is not None:
            fields.append(("namespace", self.namespace))
        return fields


class DeploymentVerifyModel(PlatformBaseModel):
    """Checks that the deployment works once applied: a stage's block after its step, `spec.verify` after every
    step. Each check is retried until it passes, or until
    `failure_threshold` attempts have failed. The timing follows Kubernetes probes: wait `initial_delay` seconds,
    then try every `period` seconds, each attempt limited to `timeout`. With the defaults that is about five minutes
    for a workload to come up. A check that fails is an error with `enforcement: deny` (the stage fails) or a
    warning with `warn`; one that cannot answer follows `on_unknown`. Runs on `deploy run` and `deploy apply`, and
    its `when: [destroy]` checks on `deploy destroy`; `--no-verify` skips it. `deploy verify` runs the `apply`
    checks on their own, against what is deployed.

    Design notes:
    docs/design/post-deploy-verification.md, D1 (policy semantics), D2, D3, D4, D6, D7; ADR-0066.
    """

    schema_rules: ClassVar[tuple[str, ...]] = ("Check names must be unique within the block.",)

    enabled: bool = Field(default=True, description="Run these checks. `false` keeps them declared but skipped.")
    enforcement: Literal["deny", "warn"] = Field(
        default="deny",
        description="What a failed check does: `deny` fails the stage, `warn` only reports it (for a check being "
        "introduced).",
    )
    on_unknown: Literal["block", "warn", "skip"] = Field(
        default="block",
        description="What a check that could not answer (timed out, could not start, exit 3 with `monitoring`) "
        "does: `block` fails the stage, `warn` reports it, `skip` only notes it.",
    )
    initial_delay: int = Field(default=10, ge=0, description="Seconds to wait after the apply, before the first check")
    period: int = Field(default=10, ge=1, description="Seconds from one attempt's start to the next")
    timeout: int = Field(default=30, ge=1, description="Seconds one attempt may take; the command is then stopped")
    success_threshold: int = Field(default=1, ge=1, description="Consecutive passes a check needs")
    failure_threshold: int = Field(
        default=30, ge=1, description="Attempts that fail or are unknown before a check gives up"
    )
    checks: list[DeploymentVerifyCheckModel] = Field(min_length=1, description="The checks, run in order")

    @model_validator(mode="after")
    def validate_unique_check_names(self) -> "DeploymentVerifyModel":
        """Check names must be unique within the block."""
        check_unique_names([check.name for check in self.checks], "verify check names")
        return self


class DeploymentStageModel(PlatformBaseModel):
    """Runtime parameters for one provisioning step of the workspace, named by `step`. A stage never
    redefines what runs. Today strata applies `timeouts` and `verify`; the other fields are accepted and
    validated but not used yet.

    Design notes:
    Runtime parameters for one provisioning step. `verify` is applied too (docs/design/post-deploy-verification.md).

    Names a `ProvisioningStepModel` from the workspace's recipe via `step`;
    it does not redefine what runs (see module docstring).
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "`health_checks` was removed in 2.9: write each probe as a `verify.checks` entry of `type: http` or `tcp`.",
    )

    @model_validator(mode="before")
    @classmethod
    def reject_removed_health_checks(cls, value: Any) -> Any:
        """`health_checks` was removed (ADR-0068): say so and give the `verify.checks` entries that replace it,
        instead of pydantic's generic "extra inputs are not permitted"."""
        if isinstance(value, dict) and "health_checks" in value:
            probes = value["health_checks"] if isinstance(value["health_checks"], list) else []
            replacement = ", ".join(_health_check_replacement(probe) for probe in probes) or "[]"
            raise ValueError(
                "'health_checks' was removed in strata 2.9: it never ran. Move each probe to the stage's "
                f"'verify.checks', where it runs: checks: [{replacement}]."
            )
        return value

    step: PlatformName = Field(
        description="Name of the workspace `execution` step these parameters apply to.\n\n"
        "Design notes: Name of the workspace execution step these parameters apply to "
        "(WorkspaceSpecModel.execution[].name)"
    )
    description: str | None = Field(None, description="Optional description for documentation purposes")
    enabled: bool | str = Field(
        default=True,
        description="Intended to switch the stage on or off. Accepted and validated, but strata "
        "does not use it yet. Every step runs. A string is a conditional expression, "
        "which is not evaluated either.\n\n"
        "Design notes: Whether this stage runs. A bool is used directly; a string is a "
        "conditional expression evaluated at deploy time. The expression engine is NOT "
        "built in v2 — a string is accepted and carried, but nothing evaluates it yet, "
        "so it must not be relied on for behaviour.",
    )
    on_failure: str = Field(
        default="stop",
        description="What a failed stage should do: `stop` (default), `rollback` or `continue`. "
        "Accepted and validated, but strata does not use it yet.\n\n"
        "Design notes: What to do when this stage fails: 'stop', 'rollback' or "
        "'continue'",
    )
    timeouts: DeploymentStageTimeoutsModel | None = Field(
        None,
        description="Per-phase timeout overrides, applied by `deploy run`.\n\n"
        "Design notes: Per-phase timeout overrides for this stage",
    )
    verify: DeploymentVerifyModel | None = Field(
        None,
        description="Checks run after this stage's step is applied (or, with `when: [destroy]`, destroyed), which "
        "decide whether the deploy worked.",
    )
    scripts: ScriptsModel | None = Field(
        None,
        description="Additional scripts for this stage. Accepted and validated, but strata does "
        "not use it yet.\n\n"
        "Design notes: Additional scripts to run for this stage",
    )
    secrets: list[str] | None = Field(
        None,
        description="Secret keys this stage needs injected from the Environment. Accepted and "
        "validated, but strata does not use it yet.\n\n"
        "Design notes: Secret keys this stage needs injected (resolved from the "
        "Environment)",
    )
    namespace: PlatformName | None = Field(
        None,
        description="Restrict this stage to a single namespace. Accepted and validated, but strata "
        "does not use it yet.\n\n"
        "Design notes: Restrict this stage to a single namespace",
    )
    helm_namespaces: list[PlatformName] | None = Field(
        None,
        description="Restrict a Helm or sync stage to these namespaces. Accepted and validated, "
        "but strata does not use it yet. A Helm step deploys every namespace of its "
        "step.\n\n"
        "Design notes: Restrict a Helm/sync stage to these namespaces. Omit to deploy "
        "all of them.",
    )

    @field_validator("on_failure")
    @classmethod
    def validate_on_failure(cls, v: str) -> str:
        """Only three failure behaviours exist."""
        allowed = ("stop", "rollback", "continue")
        if v not in allowed:
            raise ValueError(f"on_failure must be one of {allowed}, got '{v}'")
        return v

    @field_validator("enabled")
    @classmethod
    def validate_enabled_expression(cls, v: bool | str) -> bool | str:
        """A string `enabled` must at least be non-empty.

        Full expression validation needs the engine, which is deliberately
        not built yet — this only catches an obviously-empty value.
        """
        if isinstance(v, str) and not v.strip():
            raise ValueError("enabled: a conditional expression must not be empty")
        return v


class DeploymentDocumentationModel(PlatformBaseModel):
    """Render the handover document at the end of every successful `deploy run` and store it with the audit
    record: the same document `strata deploy report --format markdown` renders, with the run's own audit entry.
    Secret values are never in it; secrets are listed by where to find them.

    Design notes:
    docs/design/deployment-documentation.md, section 4. A render or push failure is a warning: the deploy has
    succeeded, and `strata deploy report` can render the document again.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "Needs the workspace's `output_store`: the document is built from what it recorded.",
        "`sink: audit` needs an enabled `git` audit sink in the Configuration: only a git sink stores files.",
        "`template` must exist and may only use the report's data (`deployment`, `workspace`, `environments`, "
        "`steps`, `secrets`, `images`, `audit`, `generated_at`, `MASK`).",
    )

    template: str | None = Field(
        None,
        description="A Jinja2 template, relative to the solution root, that replaces the built-in Markdown. It "
        "receives the same data as `strata deploy report --output json`; no field in it holds a secret value.",
    )
    sink: Literal["audit"] = Field(
        "audit",
        description="Where the document goes. `audit`: written as `_handover.md` next to the audit manifest and "
        "pushed with it by every enabled `git` audit sink.",
    )


class DeploymentLockingModel(PlatformBaseModel):
    """Pipeline-level state locking, to stop concurrent runs from colliding. Accepted and validated, but
    strata does not use it yet.

    Design notes:
    Pipeline-level state locking, preventing concurrent runs from colliding.
    """

    enabled: bool = Field(default=False, description="Enable pipeline-level state locking")
    strategy: str = Field(
        default="delegate",
        description="'wrap' makes strata take its own lock around the whole run; 'delegate' leaves locking "
        "to the provisioner's own state backend (e.g. Terraform's).",
    )
    wait_timeout: str | None = Field(None, description="How long to wait for a held lock before failing (e.g. '10m')")
    force_unlock_after: str | None = Field(None, description="Age after which a stale lock may be broken (e.g. '1h')")

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        """Only two locking strategies exist."""
        if v not in ("wrap", "delegate"):
            raise ValueError(f"Locking strategy must be 'wrap' or 'delegate', got '{v}'")
        return v


class DeploymentLayersModel(PlatformBaseModel):
    """Position of this deployment in a hierarchy. `follows` names a path convention from the
    Configuration's `spec.paths`, and `segments` gives the values for that convention's segments.

    Strata compares `segments` with the values captured from the directory the document lives in: an
    unknown `follows` is an error, and a disagreeing value is a warning or an error depending on the
    convention's `enforcement`. The segments also shape the audit output path. They are never filled in
    from the directory when absent.

    Design notes:
    Position of this deployment in a hierarchy (v1 ADR-0072).

    **Cross-checked in v2** (docs/design/path-conventions.md,
    `semantic_checks.py`'s `_check_deployment_layers()`): `follows` names a
    `Configuration.spec.paths` convention (`PathConventionModel`); when both
    it and `segments` are declared, `segments` is compared against the
    values captured from this deployment document's own real containing
    directory (`PathConventionModel.pattern`) — an unknown `follows` name
    is an error, a disagreeing segment value is a warning/error per that
    convention's own `enforcement`. Validation only — `segments` is never
    auto-populated from the real path when absent (no consumer of a
    derived value exists yet).
    """

    follows: PlatformName | None = Field(
        None, description="Name of the path convention this deployment's hierarchy follows"
    )
    segments: dict[str, str] | None = Field(
        None, description="Resolved values for that convention's segments (e.g. {'control': 'dev'})"
    )


class DeploymentProviderBindingModel(PlatformBaseModel):
    """Rebind one workspace provider slot to another Provider for this deployment, for example the same
    workspace deployed to a second Azure region. The bound document is used as it is, never patched.
    There is no bare-string shorthand, because a binding only matters when it differs from the
    workspace's default.

    Design notes:
    Rebind one workspace provider slot to another Provider document for this
    deployment (docs/design/provider-slots.md) — e.g. the same workspace
    deployed to a second Azure region. Rebind only: the bound document is used
    as-is, never field-patched. No bare-string shorthand: a binding is only
    meaningful when it differs from the workspace's default.
    """

    slot: PlatformName = Field(description="Workspace provider slot to rebind (spec.providers[].slot)")
    provider: Annotated[PlatformName, References(PlatformKind.PROVIDER)] = Field(
        description="Provider document bound to the slot for this deployment"
    )


class DeploymentAssetBindingModel(PlatformBaseModel):
    """Rebind one workspace asset slot to another Asset for this deployment, for example a customer's own
    API definitions. The bound document is used as it is, never patched. There is no bare-string
    shorthand.

    Design notes:
    Rebind one workspace asset slot to another Asset document for this
    deployment (docs/design/asset-management.md) — e.g. a customer's own API
    definitions. Rebind only: the bound document is used as-is, never
    field-patched. No bare-string shorthand, same as provider bindings.
    """

    slot: PlatformName = Field(description="Workspace asset slot to rebind (spec.assets[].slot)")
    asset: Annotated[PlatformName, References(PlatformKind.ASSET)] = Field(
        description="Asset document bound to the slot for this deployment"
    )


class DeploymentSpecModel(PlatformBaseModel):
    """What to deploy, where and how: the `workspace` whose recipe runs, the `environments` supplying
    values, an optional `tenant` and `version` pins, provider and asset rebinding, and per-step runtime
    `stages`. `partial` and `extends` share a base between deployments.

    Design notes:
    Deployment specification: what to deploy, where, and how it behaves at runtime.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "A step may have `stages` parameters declared only once.",
        "An environment may be listed only once, and a provider slot or asset slot may be bound only once.",
        "`kube_context` and `docker_context` are context names as written; a value token is rejected.",
        "A deployable document needs `workspace` and at least one entry in `environments`. A "
        "`partial: true` document, or one with `extends`, is exempt because its missing fields "
        "come from the base.",
    )

    partial: bool = Field(
        default=False,
        description="When true this is a reusable base that is not deployed on its own: required "
        "fields may be absent and it is not checked against the rest of the solution. "
        "Another deployment uses it through `extends`.\n\n"
        "Design notes: When True this is a reusable base, not deployable on its own: "
        "required fields may be absent and Phase 2 validation is skipped. Referenced "
        "by another deployment's `extends`.",
    )
    extends: Annotated[PlatformName, References(PlatformKind.DEPLOYMENT)] | None = Field(
        None,
        description="Name of a base Deployment document whose spec is merged into this one before "
        "validation. Child always wins: nested blocks deep-merge per leaf key, `stages` merge by `step`, "
        "`environments` append after the base's, other lists are replaced. Circular chains are rejected. "
        "Distinct from "
        "`environments` layering, which composes *values*; this composes *structure*.",
    )
    workspace: Annotated[PlatformName, References(PlatformKind.WORKSPACE)] | None = Field(
        None, description="Name of the Workspace document whose provisioning recipe this deployment runs"
    )
    environments: list[Annotated[PlatformName, References(PlatformKind.ENVIRONMENT)]] | None = Field(
        None,
        description="Names of Environment documents supplying values, merged in order — later entries win. "
        "A tenant's own `environments` merge in before these.",
    )
    tenant: Annotated[PlatformName, References(PlatformKind.TENANT)] | None = Field(
        None,
        description="Name of the Tenant this deployment belongs to. Omit for shared/platform deployments "
        "that serve all tenants.",
    )
    kube_context: str | None = Field(
        None,
        min_length=1,
        description="Name of the context in the caller's kubeconfig that this deployment's Helm steps deploy to. "
        "When set, every `helm` call runs against this context, whichever context is active, and the deploy "
        "stops before the first `helm` call when the kubeconfig has no such context. The caller still provides "
        "the kubeconfig. Omit to use the active context; `strata validate` then warns for a deployment with a "
        "Helm step. Value tokens are not allowed here.",
    )
    docker_context: str | None = Field(
        None,
        min_length=1,
        description="Name of the Docker context (`docker context create`) that this deployment's Compose steps "
        "deploy to. When set, every `docker` call runs against this context, and the deploy stops before the first "
        "step when Docker has no such context or `DOCKER_HOST` is set (it would override the context). Omit to use "
        "whatever engine the environment points at; `strata validate` then warns for a deployment with a Compose "
        "step. Value tokens are not allowed here.",
    )
    configurations: list[Annotated[PlatformName, References(PlatformKind.CONFIGURATION)]] | None = Field(
        None,
        description="Names of additional Configuration documents that apply to this deployment. "
        "Accepted and validated, but strata does not use it yet.\n\n"
        "Design notes: Names of additional Configuration documents that apply to this "
        "deployment",
    )
    version: VersionNames | None = Field(
        None,
        description="The Version document, or list of Version documents, supplying pins for this deployment. "
        "The pins of every listed document apply together; two listed documents pinning the same target is "
        "an error (`version_pin_conflict`), so a shared document (`prd-core`) and a product document "
        "(`prd-datahub`) each own their own pins. Through `extends` the lists append, base first. When a "
        "pinned target (image, chart, remote ref, artifact tag, asset ref) is declared elsewhere, the pin "
        "wins. Omit to use each document's own declared versions.",
    )
    layers: DeploymentLayersModel | None = Field(
        None,
        description="Hierarchy position: a path convention to follow and the segment values for "
        "it.\n\n"
        "Design notes: Hierarchy position (inert in v2 — see DeploymentLayersModel)",
    )
    documentation: DeploymentDocumentationModel | None = Field(
        None,
        description="Render the handover document (step outputs, secrets by location, images) at the end of each "
        "successful `deploy run` and store it with the audit record. Off when unset.",
    )
    locking: DeploymentLockingModel | None = Field(
        None,
        description="Pipeline-level state locking. Accepted and validated, but strata does not use "
        "it yet.\n\n"
        "Design notes: Pipeline-level state locking",
    )
    stages: list[DeploymentStageModel] | None = Field(
        None, description="Runtime parameters per workspace provisioning step"
    )
    verify: DeploymentVerifyModel | None = Field(
        None,
        description="Checks for the deployment as a whole (an end-to-end request, a smoke transaction), run once "
        "after every step was applied. Skipped when `--stage`/`--scope` selects only some steps; `deploy verify` "
        "runs them on their own. Checks merge by `name` across `extends`.",
    )
    providers: list[DeploymentProviderBindingModel] | None = Field(
        None,
        description="Rebind workspace provider slots to other Provider documents for this deployment. "
        "Merged by `slot` across `extends` (child wins), so a partial base can bind a region once.",
    )
    assets: list[DeploymentAssetBindingModel] | None = Field(
        None,
        description="Rebind workspace asset slots to other Asset documents for this deployment. "
        "Merged by `slot` across `extends` (child wins), so a partial base can bind some slots and a "
        "leaf others.",
    )
    lifecycle: CommonLifecycleModel | None = Field(
        None,
        description="Hook scripts per phase name. Accepted and validated, but strata does not run "
        "lifecycle hooks yet.\n\n"
        "Design notes: Deployment lifecycle phases",
    )
    properties: dict[str, Any] | None = Field(
        None,
        description="Deployment properties, the last merge layer over the tenant's and the "
        "environments' properties, passed to Terraform as `strata.properties`. Add an "
        "`attributeconfig` with `target.kind: deployment` to validate them.\n\n"
        "Design notes: Deployment properties, the last merge layer over tenant and "
        "environment properties",
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Free-form values merged over the tenant's and the environments' `custom` "
        "values (the deployment wins), passed to Terraform as `strata.custom`. Strata "
        "does not interpret them.\n\n"
        "Design notes: Custom user-defined data for scripts or extensions. Merges "
        "across the Tenant/Environment/Deployment chain like `properties`, delivered "
        "as one JSON-encoded `TF_VAR_custom` env var for the whole merged dict — not "
        "split into a separate env var per key.",
    )

    def version_names(self) -> list[str]:
        """The Version documents this deployment lists, in written order, each once."""
        return version_name_list(self.version)

    @field_validator("kube_context", "docker_context")
    @classmethod
    def validate_literal_context(cls, v: str | None) -> str | None:
        """A context name is used as written: a value token would reach the tool as its literal text."""
        if v is not None and "${" in v:
            raise ValueError(f"'{v}' contains a value token, which is not resolved here; write the context name")
        return v

    @model_validator(mode="after")
    def validate_unique_stage_steps(self) -> "DeploymentSpecModel":
        """Each provisioning step may have parameters declared only once."""
        if self.stages:
            check_unique_names([s.step for s in self.stages], "stage step references in deployment")
        return self

    @model_validator(mode="after")
    def validate_unique_environments(self) -> "DeploymentSpecModel":
        """A repeated environment reference would merge twice."""
        if self.environments:
            check_unique_names(self.environments, "environment references in deployment")
        return self

    @model_validator(mode="after")
    def validate_unique_provider_slots(self) -> "DeploymentSpecModel":
        """A slot bound twice would be ambiguous."""
        if self.providers:
            check_unique_names([b.slot for b in self.providers], "provider slot bindings in deployment")
        return self

    @model_validator(mode="after")
    def validate_unique_asset_slots(self) -> "DeploymentSpecModel":
        """A slot bound twice would be ambiguous."""
        if self.assets:
            check_unique_names([b.slot for b in self.assets], "asset slot bindings in deployment")
        return self

    @model_validator(mode="after")
    def validate_complete_unless_partial(self) -> "DeploymentSpecModel":
        """A deployable document needs a workspace and at least one environment.

        Two kinds of document are exempt, because neither is deployable as
        written:

        - a `partial: true` base — that is exactly what the flag means;
        - a document with `extends` — its missing fields are expected to come
          from the base. The merged result is validated without `extends`
          (`merge_deployment_specs` strips it), so the check still applies to
          whatever is actually deployed.
        """
        if self.partial or self.extends:
            return self
        missing = [
            field for field, value in (("workspace", self.workspace), ("environments", self.environments)) if not value
        ]
        if missing:
            raise ValueError(
                f"Deployment is missing required field(s) {missing}. Set 'partial: true' if this is a "
                "reusable base, or 'extends' if another document supplies them."
            )
        return self

    @model_validator(mode="after")
    def validate_verify_checks(self) -> "DeploymentSpecModel":
        """Every `verify` check is usable (`DeploymentVerifyCheckModel.problems()`).

        A document with `extends` is exempt, like `validate_complete_unless_partial()`: its checks may name only what
        they change in the base's check of the same name (`{name: web, enabled: false}`, `{name: web, timeout: 60}`),
        and their type comes from the base. The merged result is validated without `extends`, so every check that is
        deployed is judged. A `partial: true` base without `extends` writes its checks in full and is judged here.
        """
        if self.extends:
            return self
        blocks = [(f"stages[{index}].verify", stage.verify) for index, stage in enumerate(self.stages or [])]
        blocks.append(("verify", self.verify))
        problems = [
            f"{path}.checks[{index}]: {problem}"
            for path, block in blocks
            if block is not None
            for index, check in enumerate(block.checks)
            for problem in check.problems()
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return self


class DeploymentMetaModel(PlatformBaseModel):
    """Deployment metadata (name, annotations, labels, tags)."""

    name: PlatformName = Field(description="Unique deployment name")
    params: ParamSetNames | None = Field(
        None,
        description="Parameter sets this deployment reads, per role: `{spoke: spoke-acme-001}`, or a list per "
        "role merged in order (`{hub: [hub-defaults, hub-hub02]}`). Any document of the extends chain may name "
        "sets: per role they merge root first, leaf last, later winning per leaf key, so a base's sets are "
        "defaults and the leaf's override them. `${param:<role>.<key>}` in the spec of any document of the chain "
        "reads the merged values. Not inherited as meta: each document names its own.",
    )
    name_rule: str | None = Field(
        None,
        min_length=1,
        description="The name every deployable deployment built on this one must have, with `${param:<role>.<key>}` "
        "tokens filled from that deployment's parameter sets (`bootstrap-${param:spoke.name}`). A deployment whose "
        "meta.name differs is a `name_rule_mismatch` error that gives both names: names stay plain text, but cannot "
        "drift from their values. Only `${param:}` tokens are allowed.",
    )
    annotations: dict[str, Any] | None = Field(
        None, description="Optional annotations (key-value pairs for documentation)"
    )
    labels: dict[str, Any] | None = Field(
        None, description="Optional labels (key-value pairs for classification/filtering)"
    )
    tags: list[Any] | None = Field(None, description="Optional tags (list of values for categorization)")

    @field_validator("name_rule")
    @classmethod
    def validate_name_rule_tokens(cls, value: str | None) -> str | None:
        """Only well-formed `${param:}` tokens: a run-time token (`${var:x}`) has no value when names are checked."""
        return check_name_rule(value)


class DeploymentModel(PlatformBaseModel):
    """A deployment of a workspace: runs the workspace's recipe with the values of its environments, for an
    optional tenant and pinned version. Create one per place a workspace is deployed, such as a
    customer, a ring or a region, and share structure through a `partial: true` base and `extends`.

    Design notes:
    Root model for a deployment configuration file.
    """

    schema_rules: ClassVar[tuple[str, ...]] = (
        "A deployment cannot extend itself; longer extends cycles are rejected when the chain is resolved.",
    )

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2,
        frozen=True,
        description="API version for deployment configuration",
    )
    kind: PlatformKind = Field(
        default=PlatformKind.DEPLOYMENT,
        frozen=True,
        description="Platform kind (always 'deployment')",
    )
    meta: DeploymentMetaModel = Field(description="Deployment metadata (name, annotations, labels, tags)")
    spec: DeploymentSpecModel = Field(description="Deployment specification (workspace, environments, stages)")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.DEPLOYMENT)

    @model_validator(mode="after")
    def validate_not_self_extending(self) -> "DeploymentModel":
        """A deployment cannot extend itself — the trivial cycle.

        Longer chains need the document index and are checked by the
        controller during extends resolution.
        """
        if self.spec.extends and self.spec.extends == self.meta.name:
            raise ValueError(f"Deployment '{self.meta.name}' cannot extend itself.")
        return self


class DeploymentTemplateSpecModel(PlatformBaseModel):
    """The spec of a deployment template, as written: only `partial` and `extends` are read before substitution.

    Design notes:
    Every other field is kept as written (`extra="allow"`) and validated per instance, once `${param:}` is
    substituted from the chain of each deployable deployment (docs/design/parameter-sets.md, "Templates are
    validated per instance"). `extends` and `partial` stay typed: the chain must be known before any value is
    (rule 5), and `extends` is a reference the reference walk checks like any other.
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True, str_strip_whitespace=True)

    partial: bool = Field(
        default=False, description="A reusable base, not deployable itself. Never filled from a parameter set."
    )
    extends: Annotated[PlatformName, References(PlatformKind.DEPLOYMENT)] | None = Field(
        None, description="Base deployment to inherit from. Never filled from a parameter set."
    )


class DeploymentTemplateModel(DocumentTemplateModel):
    """A deployment whose spec reads parameter sets (`${param:<role>.<key>}`): loaded with its meta validated and
    its spec as written, and validated as a deployment once per deployable deployment whose chain it is in, after
    the values are substituted.

    Design notes:
    Not a document kind of its own and never exported as a schema: the loader picks it instead of
    `DeploymentModel` when a deployment's spec holds a `${param:}` token (`solution_controller.py`). The
    resolver turns it into a `DeploymentModel` per leaf; checks that read a deployment skip a template that did not
    resolve (`deployment_resolution.deployment_of()`), as they skip what a partial base lacks.
    """

    apiVersion: PlatformVersion = Field(
        default=PlatformVersion.v2, frozen=True, description="API version for deployment configuration"
    )
    kind: PlatformKind = Field(
        default=PlatformKind.DEPLOYMENT, frozen=True, description="Platform kind (always 'deployment')"
    )
    meta: DeploymentMetaModel = Field(description="Deployment metadata (name, params, annotations, labels, tags)")
    spec: DeploymentTemplateSpecModel = Field(description="Deployment specification, as written")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: PlatformKind) -> PlatformKind:
        """Reject a document whose `kind:` doesn't match this model (see `validate_kind_matches`)."""
        return validate_kind_matches(v, PlatformKind.DEPLOYMENT)


#: A deployment as the index holds it: validated, or a template validated per instance.
AnyDeploymentModel = DeploymentModel | DeploymentTemplateModel
