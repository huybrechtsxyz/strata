# Integration Layer — Design

- Status: current — Phases 1-6 implemented (transport primitives, model
  fields, base/capability ABCs, registry + store retrofit, Terraform,
  Compose + Helm)
- Last updated: 2026-10-06 (refreshed stale Remaining Work against
  build-command.md/workload-pipeline.md's own since-updated status)

## Overview

The `strata.integrations` package is how strata talks to external tools
(Terraform, Helm, Docker/Compose) and stores (Infisical, Azure Key Vault,
Azure App Config) through one uniform shape, regardless of transport (CLI
vs. HTTP vs. SDK) or capability (resolving a secret vs. planning/applying
infrastructure). Decided in [ADR-0021](../decisions/0021-integration-layer.md);
this doc describes what's actually built today, grounded in the real
source under `src/strata/integrations/`.

## Current Design

### Layering

```
Integration (base.py)          — identity, config, every transport a class may speak
  ├─ StoreIntegration           — capability: variables/secrets/features → resolve(key)
  │    ├─ InfisicalResolver
  │    ├─ AzureKeyVaultResolver
  │    └─ AzureAppConfigResolver
  └─ InfraIntegration           — capability: infrastructure/container → plan/deploy/destroy
       ├─ TerraformIntegration  — CLI, TF_VAR_ env injection, default_output() built
       ├─ ComposeIntegration    — CLI (docker stack), default_output() NOT overridden yet
       └─ HelmIntegration       — CLI (helm), default_output() NOT overridden yet
```

- **`Integration` (`base.py`)** owns identity (`TYPE`, `name`), every
  transport a class may speak (`is_available()`/`run()` for `cli`,
  `request()` for networked transports), and version checking
  (`get_version()`/`parse_version()`/`ensure_version()` against a PEP 440
  specifier via `packaging`). `config: IntegrationModel | None` is always
  optional — a class works from `PATH`/env vars alone with no document.
- **`transport` is a cached, resolved property**, not a class attribute:
  explicit `spec.transport` (validated against the class's own
  `TRANSPORTS`) wins; else the sole supported transport when there's only
  one; else `"cli"` if available; else whatever's left.
- **Capability ABCs (`capabilities.py`) own only the method contract**,
  nothing about transport. `CAPABILITY_ABCS` maps each core capability
  string to its ABC (`variables`/`secrets`/`features` → `StoreIntegration`;
  `infrastructure`/`container` → `InfraIntegration`; `sources` has no ABC
  yet — not built). `find_capability_mismatches()` catches a class
  declaring a capability its base classes don't actually implement.
- **`InfraIntegration.prepare()` is base-implemented, not abstract**
  (ADR-0023 D5) — every subclass gets the same dispatch: call
  `default_output()` and write whatever it returns. Only `default_output()`
  varies per tool. `TerraformIntegration` overrides it (calls
  `build_platform_projection()`/`planned_files()` — see the build-command
  design doc); `ComposeIntegration`/`HelmIntegration` do **not** override it
  yet, so they currently inherit the empty-dict "generate nothing" default
  — a real, temporary gap (see Remaining Work), not Bicep-style intentional
  behaviour for these two.

### Registry (`registry.py`)

One function, `get(integration_type, config=None) -> Integration` — no
process-wide singleton, no separate factory/registry/service split (v1 had
three; v2 collapsed them). Lazy-imports the target module so `strata
validate` never pays for `azure-identity`/`hvac`/etc. it doesn't use.

```python
_KNOWN = {
    "infisical": (...), "azure-keyvault": (...), "azure-appconfig": (...),
    "terraform": (...), "compose": (...), "helm": (...),
}
```

Third-party plugins register via the `strata.integrations` entry-point
group (`ADR-0021` D10) — checked in `_resolve_class()` after built-ins, and
a plugin may not reuse a built-in name. `_KNOWN_V1_TYPES` is a frozenset of
v1 type names not yet ported (ansible, git, bicep, opentofu, vault, consul,
etcd, bitwarden, flagsmith, and several observability/policy tools) — used
only so `get()`'s error can say "not built yet" instead of "not a real
type" for a name a v1 user would recognize.

No instance cache in the registry itself (ADR-0021 D3) — a caller that
wants reuse across a batch (e.g. `value_controller.resolve_values()`) keeps
its own short-lived, command-scoped cache keyed by **declaration name**
(not type — two differently-configured instances of the same tool type
must not collapse into one), except the store-resolution path, which has no
name to key by and is intentionally keyed by type instead (matches v1's own
real behaviour there).

### Built classes

| Class                    | Type              | Transport(s)     | Capability                       | Notes                                                                                                                                                                                                                                                 |
| ------------------------ | ----------------- | ---------------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `InfisicalResolver`      | `infisical`       | —                | `variables`/`secrets`/`features` | Retrofit of the pre-existing resolver; now reads a bound `Integration` document's `spec.endpoints`/`.configuration` first, falling back to its original env vars ([store-integration-configuration.md](../design/store-integration-configuration.md)) |
| `AzureKeyVaultResolver`  | `azure-keyvault`  | SDK              | `secrets`                        | Retrofit; same config-first, env-fallback wiring as `InfisicalResolver`                                                                                                                                                                               |
| `AzureAppConfigResolver` | `azure-appconfig` | SDK              | `variables`/`features`           | Retrofit; same config-first, env-fallback wiring as `InfisicalResolver`                                                                                                                                                                               |
| `TerraformIntegration`   | `terraform`       | `cli`            | `infrastructure`                 | `plan`/`deploy`/`destroy` + real v1 extras (`init`/`validate`/`output`/`show`); secrets injected via `TF_VAR_*` env vars, never argv; `default_output()` built                                                                                        |
| `ComposeIntegration`     | `compose`         | `cli` (`docker`) | `container`                      | Deploys via `docker stack` (Swarm), not standalone Compose CLI — matches real v1 usage. `plan()` has no true dry-run (`docker stack config`, admitted v1 limitation)                                                                                  |
| `HelmIntegration`        | `helm`            | `cli` (`helm`)   | `container`                      | Argv shapes copied from v1's real `HelmDeployer`                                                                                                                                                                                                      |

### Version ownership

`IntegrationModel.spec.version` is the single owner of "what tool version
does this solution expect" (ADR-0021 D4) — `ProvisionerModel.version` was
removed; a provisioner names its integration instead
(`provisioner.integration`), and `ensure_version()` checks against the
integration document's `version` (a PEP 440 specifier).

## Related Decisions

- [ADR-0021](../decisions/0021-integration-layer.md) — the full design (D1-D11)
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) — `ProvisionerModel`, whose `.version` field this ADR removed
- [ADR-0022](../decisions/0022-strata-build-run.md), [ADR-0023](../decisions/0023-build-output-rendering.md) — the first real consumer (`prepare()`'s callers) — see [build-command.md](build-command.md)

## Remaining Work / Open Questions

- ~~`ComposeIntegration`/`HelmIntegration` do not override `default_output()`
  yet~~ — **resolved, 2026-10-06 refresh**: this bullet was stale.
  `default_output()`/`prepare()` is the provisioner-pipeline path only;
  Compose/Helm's real usage is as **namespace modules**, which go through
  the separate, now-fully-built workload pipeline instead
  (`prepare_namespace()`, [workload-pipeline.md](../design/workload-pipeline.md),
  done 2026-09-25 — see [build-command.md](build-command.md)). Checked
  real usage directly: no workspace in `config/` declares `compose`/`helm`
  as a direct *provisioner* (only `terraform`), so `default_output()`
  staying empty for these two classes is an unexercised theoretical gap,
  not a real one — revisit only if a real provisioner-level (not
  namespace-module) Compose/Helm usage appears.
- `output.template` (the Jinja2 escape hatch, ADR-0023 D3) and
  `provisioner.backend` token substitution (D2) are not wired into
  `InfraIntegration.prepare()` yet — it currently only calls
  `default_output()` unconditionally. Still accurate as of 2026-10-06:
  [build-command.md](build-command.md) confirms only `output.template`'s
  *build-time validation* half is done — the actual-render half and
  `backend`/`.configuration`/`dns`/`networks` token substitution are all
  deploy-time-only, blocked on `deploy run`'s own remaining work, not this
  layer's.
- No `sources` capability ABC yet — remote fetching (beyond `SourceModel`'s
  existing git/OCI/chart reference, ADR-0018) is not built.
- `ansible`/`bicep`/`opentofu`/`vault`/`consul`/`etcd`/`bitwarden`/
  `flagsmith` and observability/policy tools remain unported — added one
  `_KNOWN` entry + one file at a time, per real consumer need (ADR-0021's
  explicit "not from the start" direction).
- No capability-based "find me anything that can do X" query
  (v1's `get_integrations_with_capability`) — only direct `type` lookup.

## Changelog

- 2026-09-24: Created, grounded directly in `src/strata/integrations/` (base.py, capabilities.py, registry.py, terraform.py, compose.py, helm.py) rather than reconstructed from ADR-0021 text alone.
- 2026-09-29: Noted that store auto-bind wiring is now built — `value_controller.py`'s store resolution auto-binds a real `Integration` document by type (reusing `integration_resolution.py`'s `bind_integration_config()`), and all three store resolvers now read that document's `spec.endpoints`/`.configuration` before falling back to their original env vars. Full design, rationale, and worked examples in [store-integration-configuration.md](../design/store-integration-configuration.md) — not duplicated here.
- 2026-10-06: `store-integration-configuration.md` graduated to `docs/design/` — all three phases were already fully implemented, and the two open design questions it left (untyped `configuration` dict access; a disabled `Integration` document staying silently ignored) were both resolved as its own stated leaning, matching current behaviour. Link above updated.
- 2026-10-06: Refreshed Remaining Work against build-command.md/workload-pipeline.md, both updated since this doc's last pass. The `ComposeIntegration`/`HelmIntegration` `default_output()` bullet was stale — resolved by clarifying it describes the provisioner pipeline only, which no real workspace exercises for these two tool types; their real usage (namespace modules) goes through the separate, now-fully-built workload pipeline instead. The `output.template`/token-substitution bullet remains accurate. Also retired `build-pipeline-status.md` (deleted — its own status table had gone stale and was contradicted by this doc and build-command.md); readers should use build-command.md/this doc directly for current status instead.
