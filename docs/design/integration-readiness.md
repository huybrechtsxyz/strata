# Integration readiness — Design

- Status: current
- Last updated: 2026-11-20

## Overview

Two separate questions can be asked of an Integration:

| Question                 | Checks                                                                         | Needs                                   |
| ------------------------ | ------------------------------------------------------------------------------ | --------------------------------------- |
| Is it usable **here**?   | type registered, `spec.enabled`, tool on `PATH`, installed vs `spec.version`   | nothing: offline, no credentials        |
| Is it reachable **now**? | credentials resolve and the platform accepts them                              | a deployment (for credentials), network |

One offline readiness check answers the first
([ADR-0050](../decisions/0050-one-offline-integration-readiness-check.md)). `build run`, every deploy command,
`strata integration check` and `strata integration list` all call it, so they cannot disagree.
`strata integration check` then answers the second for a type with a connection check. `strata validate` checks
only what the documents say; readiness is a property of the machine, so validate never runs it and works anywhere.

User documentation: [integration.md](../config/integration.md).

## The readiness record

`assess_integration(integration, *, read_version=True)` in
[integration_readiness.py](../../src/strata/controllers/integration_readiness.py) returns a frozen
`IntegrationReadiness`: name, type, whether a document declares it, `enabled`, `required`, `registered`, whether
it is a credential holder, local tool or not, command, `tool_path`, available, required and installed version,
connection check supported, `problems` and `warnings`, and `ok`. It never resolves credentials and never calls
`check_connection()`.

| Problem code                        | When                                                                              |
| ----------------------------------- | --------------------------------------------------------------------------------- |
| `integration_disabled`              | `spec.enabled: false`                                                             |
| `integration_unavailable`           | `'<command>' is not available on PATH.`                                           |
| `integration_version_mismatch`      | the installed version does not satisfy `spec.version`, or could not be read       |
| `integration_version_unenforceable` | `spec.version` on a type with no `cli` transport, or with another transport set   |
| `integration_configuration_invalid` | a known type whose class rejects its configuration (an unsupported `transport`)   |
| `integration_type_unregistered`     | no class for the type, and the document is not a credential holder                |

- **The version is read once**, and not at all when the tool is unavailable or under `read_version=False`.
- **A credential holder** is a document whose type has no class and whose capabilities are only `sources` (and
  `x-` extensions), such as a `type: git` document a remote names for its credentials. It is never constructed, so
  it is reported as a credential holder, not as unregistered.
- **`registered`** means the type has a class, even when that class rejects the configuration.
- **Per-class version rules** ([ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md),
  [helm-chart-deployment.md](helm-chart-deployment.md#helm-versions)): a class may set `READ_VERSION` (read the
  version without a `spec.version`), return `version_problems()` (readiness problems) and `version_notices()`
  (`warnings`, never counted against `ok`). A problem marked `flags_only` is about the flags strata passes;
  `deploy destroy` reports it as a warning and lets the step run. Helm is the first user.

### Version constraints

`spec.version` takes PEP 440 specifiers, plus Terraform's `~>` read as its exact equivalent `~=` (`~> 1.9` means
`>= 1.9, < 2.0`). One parser and one comparison, `version_satisfies()` in
[version_constraints.py](../../src/strata/utils/version_constraints.py), serve the model and the check. An
unparseable constraint fails when the document loads, so `strata validate` reports it. A version `packaging`
cannot parse never satisfies a constraint. `spec.version` on a networked-only integration is a validate error
(`integration_version_unenforceable`): strata cannot read a remote service's version, and a silently ignored
assertion is worse than none.

## Where it runs

**Deploy step preflight.** Every deploy command (`deploy run`, `setup`, `check`, `plan`, `apply`, `destroy`)
assesses each filtered step's integration once per `(integration name, command)`: a script integration's command
depends on its provisioner, so two steps can share a name and run different binaries. Each problem is reported once,
prefixed `Provisioner '<p>' (step '<s>'):`, and is an error; no step runs. A provisioner naming a disabled
Integration fails here. The preflight prints `TYPE VERSION (PATH)` once per tool and hands each step's readiness to
the audit manifest (`deploy_run(on_tool=...)`). Under `--smoke-test` (no tool contact) the version is not read, and
the run says `would check <type> version '<constraint>'`; `--dry-run` checks.

**Integration preflight.** `integration_preflight()` assesses every *enabled* Integration document. A problem is an
error when the document has `spec.required: true`, otherwise a warning; disabled documents are skipped.
- `build run` runs it first, before cleaning or rendering, also under `--dry-run`. A required integration that is
  not ready stops the build (exit 3).
- Deploy runs it after the step preflight, leaving out what that already checked, so nothing is reported twice.

`readiness_diagnostics(readiness, used=...)` holds the one severity rule for both preflights and
`integration list`: error when required or used by the run, warning otherwise, nothing for a disabled document
nothing uses.

**`strata validate`** reports the contradictions the documents alone show (document-only, no `PATH`):
- `provisioner_integration_disabled` (error): an execution step's provisioner names a disabled Integration. A
  `depends_on`-only or unused provisioner is not reported, since deploy never resolves its integration.
- `integration_required_disabled` (warning): `required: true` on a disabled document, which is never checked, so
  `required` has no effect.

## `strata integration check NAME [--deployment D]`

**Lookup.** NAME is a document name first. Otherwise, when it is a registered type, the integration deploy would
bind for that type: the single enabled document of that type, or the class with its defaults. Otherwise exit 2,
listing the declared names and `registry.known_types()`; a v1 type not ported yet (`ansible`, say) says so, as a
declared document of that type does. Several enabled documents of one type: exit 2, "check one of them by name".

**Order.** Local readiness always runs first and is reported in full; a problem exits 3 and skips the connection
check. Then, for a type with a connection check: `--deployment` is needed (exit 2), credentials must resolve, and
`check_connection()` runs. A local tool without one reports "local checks only"; a networked type without one says
there is nothing to verify without a connection check.

| Exit | Meaning                                                                                                  |
| ---- | -------------------------------------------------------------------------------------------------------- |
| 0    | ready                                                                                                    |
| 1    | the platform is unreachable                                                                              |
| 2    | NAME is neither declared nor a known type; several documents of the type; `--deployment` needed; bad arguments |
| 3    | not ready locally (disabled, not on `PATH`, wrong version, misconfigured), or credentials missing, unresolved or rejected |

Connection failures are diagnostics, so the readiness line and JSON `data` survive them. The JSON `data` holds
`integration`, `type`, `declared`, `readiness` (every field, `problems` included) and `checks`.

## `strata integration list [--deployment D]`

One row per declared Integration document, plus one per type the deployment binds without a document, shown as
`<type> (type default)`. Script integrations are one row per `(name, command)`. Reading versions runs each local
tool once. The readiness record has the same JSON shape here as in `check`.

**Usage map.** With `--deployment`, each row says what that deployment's `deploy run` binds it for: execution step
provisioners, non-`direct` value stores of the reachable environments, the output store, and the two gate
policies. Build-time bindings (module types, remotes, assets, scanners) are not in the map: `build run`'s own
preflight covers them.

**Exit codes.** 0 listed. 3 when an integration the deployment uses is not ready, or, with or without
`--deployment`, when a *required* one is not ready; other problems are warnings. 2 for an unknown deployment or an
ambiguous auto-bind, as deploy. Without `--deployment` it is the stand-alone "is this machine ready?" check.

## Local tools as declarable integrations

`git`, `trivy`, `grype` and `checkov` are registered types
([ADR-0051](../decisions/0051-local-tools-as-declarable-integration-types.md)), so a document can declare them and
get `spec.version`, `spec.required`, `spec.command` and both commands like any other local tool. Undeclared, each
keeps its `PATH` behaviour.

- **`git`** (`GitIntegration`, `sources`, version from `git --version`).
- **`trivy`, `grype`** (`ScannerIntegration`): the CVE gate uses the enabled `trivy` document, else the enabled
  `grype` document, else `PATH` auto-detection (Trivy preferred). A declared scanner that is missing is never
  swapped for the other one: the gate's `on_missing_data` applies, and the preflight has already reported it.
- **`checkov`**: the Checkov gate binds the enabled `checkov` document on both its provisioner and Helm paths.

Binding is by type, through `bind_integration_config()`, as stores and module types bind; several enabled
documents of one type is the same usage error.

## Not covered

- **`tools install`** (v1's setup guidance, optionally AI-generated): no evidence of use.
- **Filter flags on `integration list`** (v1's `--required`, `--missing`, ...): `--output json` and `jq` cover them.
- **The remaining v1 types** (`ansible`, `bicep`, `vault`, ...): each is ported when a real v2 consumer appears;
  until then both commands name them as not ported yet.

## Related Decisions

- [ADR-0050](../decisions/0050-one-offline-integration-readiness-check.md) — one offline readiness check.
- [ADR-0051](../decisions/0051-local-tools-as-declarable-integration-types.md) — local tools as declarable types.
- [ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md) — per-class version rules.
- [security-gates.md](security-gates.md) — where `strata integration check` started, as a connection check for
  the gates' integrations.

## History

- Started from a review finding: `spec.version` was documented as "checked at preflight", but `ensure_version()`
  had no caller, so `version: ">= 1.7"` was never enforced; and `integration check` (built for the security gates)
  only answered the networked question, exiting 2 for Terraform. v1's `tools status|check` answered the local one,
  though neither real consumer's CI called them.
- Terraform's `~> 1.9`, the example in the field's own description, made `packaging` raise an uncaught error; `~>`
  is now read as `~=`.
- The first version cache was keyed by integration name; script integrations showed that `(name, command)` is the
  real key.
- `spec.required` was set in real documents and read by nothing, the same class of gap `spec.version` had been. The
  integration preflight is what gave it a meaning.
- `type: git` documents were reported as unregistered by `integration check`, though a remote only reads their
  credentials. That led to the credential-holder rule, and then to registering `git` as a local tool.
  `ensure_version()` was removed so there is one comparison path.
- Trivy, Grype and Checkov were built only inside their gates, so no document could pin or require them;
  registering them gave them the same handling as every other tool.
- A provisioner naming a disabled Integration passed validate and failed only at deploy, after a full build; validate
  now reports it, and a required-but-disabled document as a warning.
- An undeclared v1 type named in `integration check` got the generic "not a known type" message, though a declared
  document of that type already said "not yet ported". Both now say it; with that, nothing was open and the doc
  moved to design.
