# Integration Layer — Design

- Status: current — Phases 1-6 implemented (transport primitives, model
  fields, base/capability ABCs, registry + store retrofit, Terraform,
  Compose + Helm)
- Last updated: 2026-09-24

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

| Class | Type | Transport(s) | Capability | Notes |
| --- | --- | --- | --- | --- |
| `InfisicalResolver` | `infisical` | — | `variables`/`secrets`/`features` | Retrofit of the pre-existing resolver, unchanged behaviour |
| `AzureKeyVaultResolver` | `azure-keyvault` | SDK | `secrets` | Retrofit |
| `AzureAppConfigResolver` | `azure-appconfig` | SDK | `variables`/`features` | Retrofit |
| `TerraformIntegration` | `terraform` | `cli` | `infrastructure` | `plan`/`deploy`/`destroy` + real v1 extras (`init`/`validate`/`output`/`show`); secrets injected via `TF_VAR_*` env vars, never argv; `default_output()` built |
| `ComposeIntegration` | `compose` | `cli` (`docker`) | `container` | Deploys via `docker stack` (Swarm), not standalone Compose CLI — matches real v1 usage. `plan()` has no true dry-run (`docker stack config`, admitted v1 limitation) |
| `HelmIntegration` | `helm` | `cli` (`helm`) | `container` | Argv shapes copied from v1's real `HelmDeployer` |

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

- `ComposeIntegration`/`HelmIntegration` do not override `default_output()`
  yet — they currently render nothing, which is correct for Bicep but not
  intentional for these two. Needs the workload pipeline
  (`prepare_namespace()`, ADR-0022 D5-D7) to actually be meaningful — see
  [build-command.md](build-command.md).
- `output.template` (the Jinja2 escape hatch, ADR-0023 D3) and
  `provisioner.backend` token substitution (D2) are not wired into
  `InfraIntegration.prepare()` yet — it currently only calls
  `default_output()` unconditionally.
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
