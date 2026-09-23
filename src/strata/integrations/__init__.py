#!/usr/bin/env python3
"""External store backends — the runtime resolvers `strata.models.store_model`
documents as recognized-but-inert.

Sits below `strata.services` in the import-linter layering (ADR-0003, mirrors
v1's `strata.services > strata.integrations > strata.models`): a resolver
only needs a store's `value` identifier and its own environment-variable
configuration, never a loaded document or the solution index.

Deliberately env-var-configured, not bound to an `Integration` document.
`IntegrationModel` has had an `endpoints` field since ADR-0021 Phase 2 — the
gap now is that nothing *looks one up*: no store model carries a reference
to a named `Integration` document (confirmed against v1 too — its own
store-to-integration lookup, `ValueController._get_integration_by_type`,
resolves by `type` string as well, never by declaration name; see ADR-0021
Phase 4). Each resolver below documents the exact environment variables it
reads; binding one to a specific `Integration` document is future work, not
a regression, for whenever a store grows a name to bind with.

Only the three backends with proven production usage are implemented
(see `/memories/repo/v1-consumer-usage.md`): Infisical (variables/secrets),
Azure Key Vault (secrets), Azure App Configuration (variables/features).
Every other `store` value recognized by the models (`vault`, `consul`,
`etcd`, `flagsmith`, `bitwarden`) raises a clear "no resolver implemented
yet" `ValueResolutionError` rather than silently returning nothing.
"""
