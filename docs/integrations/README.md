# Integrations — How to Configure Each One

This folder is task-focused: "I have a real Infisical project / Azure Key Vault / Azure App
Configuration instance — how do I point strata at it?" For the generic `kind: integration` schema
that applies uniformly to every integration type, see
[docs/config/integration.md](../config/integration.md) instead; for the internal design/rationale
behind how a store finds and binds to one of these documents, see
[docs/design/store-integration-configuration.md](../design/store-integration-configuration.md).

## Secret/variable/feature stores

An [`environment`](../config/environment.md) document's `variables`/`secrets`/`features` entries
set `store:` to one of these types to resolve a real value from an external system, instead of a
`constant` literal or a bare `environment` variable read:

- [infisical.md](infisical.md) — `store: infisical` (variables, secrets)
- [azure-keyvault.md](azure-keyvault.md) — `store: azure-keyvault` (secrets)
- [azure-appconfig.md](azure-appconfig.md) — `store: azure-appconfig` (variables, features)

## How binding works, in one paragraph

A store names a **type** (`store: infisical`), never a specific document — strata looks for a
single *enabled* `kind: integration` document whose `spec.type` matches and uses it automatically;
zero matches falls back to environment-variable-only configuration (each guide below documents
exactly which env vars); more than one enabled match is a validation error naming every candidate,
never a silent guess. You never need an explicit reference field on the store itself.

## What's real vs. what's not yet

The worked `kind: integration` examples in each guide below are the **designed** end state
([docs/design/store-integration-configuration.md](../design/store-integration-configuration.md));
until that design is implemented, only the environment-variable configuration each guide documents
is live. Each guide is written against the designed end state on purpose, so it doesn't need a
rewrite once implementation lands — check that design doc's own Status line for current progress.

None of the three integrations below use `spec.authentication` — every guide explains why up
front, so it isn't mistaken for an oversight.
