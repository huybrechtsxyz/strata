# Context — a Shared, Stage-Contributed Runtime Store

- Status: proposed
- Date: 2026-09-21
- Related: [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (Grant, Injection, Value tokens — Context is where Grant-scoped values and
  `${var:}`/`${secret:}`/`${feature:}` tokens actually resolve against at
  runtime), [ADR-0005](0005-dns-model-design-decisions.md) (removed
  `output_key` from `DnsRecordModel` as a direct consequence of this finding)

## Context and Problem Statement

While researching whether DNS's `output_key` field (a record's value sourced
from a preceding deployment stage's provisioner output) should be generalized
into the `${var:}`/`${secret:}`/`${feature:}` token syntax, v1's builders/
deployers (not just its kind-schema files) were checked for other consumers
of the same concept. Two more real, working ones turned up immediately:

- `HealthCheckModel.output_key` (`deployment_model.py`) — a health check's
  target URL/host can come from a Terraform output instead of a literal.
- `ip_output_key` (Ansible topology config, `ansible_builder.py`/
  `ansible_deployer.py`) — resolves a stage's SSH target IP from a prior
  stage's output, default key `"server_ip"`.

All three read from and are populated by one underlying runtime object,
already built in v1: `strata.utils.resolved_values.ResolvedValues`. This ADR
records that finding and the decision to build v2's equivalent as one
first-class concept — **Context** — rather than let every kind reinvent its
own "bind this field to some other stage's output" plumbing, which is what
had happened by accident in v1.

## v1 precedent: `ResolvedValues`

`ResolvedValues` (`strata/utils/resolved_values.py`) is a dataclass, not a
Pydantic model, holding:

- `variables`, `secrets`, `features` — resolved from declared stores
  (`VariableStoreModel`/`SecretStoreModel`/`FeatureStoreModel` in
  `store_models.py`, referenced from `EnvironmentModel`).
- `stage_outputs`, `stage_outputs_sensitive` — non-sensitive / sensitive
  outputs collected from preceding deployment stages via each deployer's
  `collect_outputs()` (`strata/deployers/base_deployer.py` in v1) and merged
  back in, made available to every subsequent stage.
- Provenance (`variable_sources`, `merge_order`) and errors
  (`errors`, `store_unavailable_errors`).

It is built once per deploy run by `ValueController`, threaded through every
deployer (`BaseDeployer.__init__(..., resolved_values: ResolvedValues | None)`),
and mutated as stages complete. Its own `debug_summary()` labels the two
halves **`strata_context`** (variables, features, stage_outputs — safe to
log) and **`strata_sensitive`** (secrets, stage_outputs_sensitive — masked) —
v1 already treats this as "the context," just never promoted to a first-class,
typed, user-facing concept.

Crucially, `ResolvedValues.for_stage(allowed_secrets)` already implements
**Grant** exactly as ADR-0002 concluded independently (secrets-only,
allowlist, `["*"]` escape hatch) — and `DeploymentStageModel.secrets:
list[str] | None` (*"Allowlist of secret keys this stage may access from
STRATA_SENSITIVE... Use `['*']` to grant access to all secrets"*) is the real
schema field backing it. This is strong cross-validation that ADR-0002's
Grant design matches working v1 code, found independently and after the fact.

## Decision

Adopt **Context** as v2's name for the v1 `ResolvedValues` concept, generalized
as the single mechanism any kind's Value-token fields resolve against — not
just DNS records. Concretely, once the provisioner/build and deploy/stage
layers are designed:

1. **One shared Context object, not per-kind output fields.** Any stage
   contributes to it (the `collect_outputs()` pattern); any subsequent
   stage/kind reads from it. This replaces `output_key` (DNS),
   `HealthCheckModel.output_key`, and `ip_output_key` (Ansible topology) with
   one mechanism instead of three independent ones.
2. **Context absorbs `${output:KEY}`** as the token kind that resolves
   against it (alongside `${var:}`/`${secret:}`/`${feature:}`, which resolve
   against the declared `Environment` instead) — once Context exists to
   resolve it against. Not added to `VALUE_TOKEN_KINDS` yet (see Remaining
   Work) — adding it today, with no Context to back it, would misrepresent it
   as having the same validation guarantee as `var`/`secret`/`feature`.
3. **Context is a runtime object, not a user-authored YAML kind.** Like
   `ResolvedValues`, it's assembled during a deploy run, not hand-written —
   no `ContextModel(PlatformBaseModel)` YAML schema is implied by this
   decision; only its *shape* (as a typed Python object, likely a Pydantic
   model instead of v1's dataclass, so it validates itself) is relevant here.
4. **Context does not solve output validation.** It solves *where resolved
   values live and how they flow between stages* — it does not create a
   ground truth for checking that a referenced output key will actually
   exist (that would require an "Outputs" declaration, symmetric to
   Interface's declared *inputs*, itself still undesigned). v1 never solved
   this either — `stage_outputs.get(key)` silently returns `None`/missing on
   a bad key today, and Context doesn't change that by itself.
5. **Grant is confirmed, unchanged from ADR-0002.** `for_stage(allowed_secrets)`
   is Context's Grant-scoping method — secrets-only, stage-scoped, derived
   from `stage.kind` by default with an explicit allow/deny override. No new
   decision needed here; this ADR just records the independent confirmation.

## Consequences

- Good: replaces three independent, ad hoc "read a prior stage's output"
  mechanisms (DNS `output_key`, `HealthCheckModel.output_key`, Ansible
  `ip_output_key`) with one.
- Good: Grant's design (ADR-0002) is now doubly confirmed against real v1
  code, found independently.
- Neutral: does not solve output-key validation — that remains open,
  tracked separately as Interface's missing "Outputs" twin.
- Cost: `DnsRecordModel` currently cannot express an output-sourced value at
  all (see ADR-0005) until Context is built — a real, if temporary,
  capability gap versus v1.

## Remaining Work

- Design Context's concrete shape (likely a Pydantic model mirroring
  `ResolvedValues`: variables/secrets/features/stage_outputs(+sensitive)/
  provenance) when the provisioner/build and deploy/stage layers are
  designed — not before.
- Add `"output"` to `VALUE_TOKEN_KINDS`/`VALUE_TOKEN_PATTERN` (`common_models.py`)
  at the same time, and re-add an output-sourced binding to `DnsRecordModel`
  (and any other kind that needs it, e.g. a future `HealthCheckModel`).
- The "Outputs declaration" problem (a real ground truth to validate
  `${output:KEY}`'s key against, symmetric to Interface) remains open and
  unscheduled — do not build it speculatively; wait for a concrete need once
  Context exists.
