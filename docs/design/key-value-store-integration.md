# Key/Value Store Integration — Design

- Status: current
- Last updated: 2026-11-20

## Overview

A small capability: get and put one small value by key, with an optional compare-and-swap through a version tag the
backend returns. Deploy uses it to keep records across separate `deploy run` invocations and pipelines
([ADR-0036](../decisions/0036-cross-run-key-value-output-store.md)):

- `DeploymentOutputStore` ([deployment_output_store.py](../../src/strata/controllers/deployment_output_store.py)): each
  step's outputs, which a later run reads through `${output:STEP.KEY}`
  ([cross-run-step-output-delivery.md](../archive/cross-run-step-output-delivery.md));
- `DeploymentReportStore` ([deployment_report_store.py](../../src/strata/controllers/deployment_report_store.py)): what
  `deploy output` and `deploy report` read ([deployment-documentation.md](deployment-documentation.md)).

A workspace picks the backend with `output_store` (a type) or `output_store_integration` (a document);
`resolve_output_store_integration()` returns it. The capability itself knows nothing about outputs: anything needing
"store one small record, keyed, possibly contended" can use it.

### Terminology

- **Capability:** the contract, `Capability.KEY_VALUE` and the `KeyValueStoreIntegration` ABC. It exists once; a
  controller asks for "something implementing `KeyValueStoreIntegration`", never a backend class.
- **Integration:** a concrete class registered under a `type`, connecting to one system. One integration can provide
  several capabilities: `AzureAppConfigResolver` provides `variables`, `features` and `key_value`.

## The contract

In [capabilities.py](../../src/strata/integrations/capabilities.py):

- `get(key) -> KeyValueEntry | None`: the stored bytes and the backend's version for them
  (`KeyValueEntry`, a frozen dataclass in `resolved_context.py`), or `None` when the key does not exist. Callers test
  `is None`: an empty value is falsy too, and means something else.
- `put(key, value, *, if_version=None) -> str`: write and return the new version. `if_version` has three modes:
  - `None`: unconditional;
  - `NEW_KEY_VERSION` (`"0"`): only if the key does not exist yet;
  - any other value: only if the backend's current version still equals it.

  A condition that does not hold raises `KeyValueConflictError` (an `IntegrationError`, so existing handlers still
  catch it). The caller re-reads and retries its own merge, never the same write without `if_version`, which would
  discard the other writer's update.
- `SUPPORTS_CAS: ClassVar[bool]` (default `True`): `True` only when the backend honours **both** conditional modes. A
  caller that needs compare-and-swap checks it before trusting a backend, and `DeploymentOutputStore` does: its
  record after a deployment's first write is always an update against a prior version. A workspace whose
  `output_store` lacks it is a validate error, `output_store_lacks_cas` (a class-only check, no credentials), and
  `resolve_output_store_integration()` checks again for a caller that skipped validation.
- `MAX_VALUE_BYTES: ClassVar[int | None]`: the largest key plus value one `put()` stores, or `None` for no practical
  limit ([ADR-0057](../decisions/0057-output-record-one-key-per-step.md)). A caller measures before writing, so an
  oversized record gets an error naming what is too large instead of the backend's own rejection. When a provider
  documents "10 KB" without saying 1,000 or 1,024, the smaller number is used.

Only `get` and `put`: no `delete`, because not every backend has a conditional delete (S3 has none).

### Validated reuse: a lock on top

The contract is enough for a distributed lock, unchanged, using the CAS recipe Consul's and etcd's own docs describe:
acquire with `put(lock, holder, if_version=NEW_KEY_VERSION)`; release with `put(lock, b"", if_version=<acquired
version>)`; re-acquire with `get()` and then a version-matched `put()`. This holds for every backend whose CAS is a
server-side guarantee; the file backend's holds only where its share's exclusive create does.

## Backends

| Backend | `type`            | Class                    | `SUPPORTS_CAS` | `MAX_VALUE_BYTES` | Version            |
| ------- | ----------------- | ------------------------ | -------------- | ----------------- | ------------------ |
| 2       | `azure-appconfig` | `AzureAppConfigResolver` | yes            | 10,000            | the setting's ETag |
| 3       | `azure-blob`      | `AzureBlobKeyValueStore` | yes            | none              | the blob's ETag    |
| 7       | `azure-keyvault`  | `AzureKeyVaultResolver`  | **no**         | 25,000            | the secret version |

Azure was built first: real teams already hold its credentials, and some use Key Vault as a cheaper small-settings
store than App Configuration for values that are not secret. All three authenticate with `DefaultAzureCredential`.

### Backend 2: Azure App Configuration

Extends the existing `AzureAppConfigResolver`, which already had the endpoint, credential and client.
- `get`: `get_configuration_setting(key=...)`; `None` on `ResourceNotFoundError` (a missing key is a normal outcome
  here, unlike `resolve()`).
- `put`: `set_configuration_setting(...)`; `NEW_KEY_VERSION` adds `match_condition=IfMissing` (`If-None-Match: *`), a
  prior version adds `etag=..., match_condition=IfNotModified` (`If-Match`). `etag` is a separate keyword, passed
  explicitly rather than set on the setting object.
- A conflict is `ResourceModifiedError` (HTTP 412, raised by azure-core's pipeline), re-raised as
  `KeyValueConflictError`.

### Backend 3: Azure Blob Storage

`AzureBlobKeyValueStore` ([azure_blob_keyvalue_store.py](../../src/strata/integrations/azure_blob_keyvalue_store.py)),
the container URL from `spec.endpoints.address` or `AZURE_BLOB_CONTAINER_URL`; `azure-storage-blob` is a main
dependency, like the other Azure SDKs.
- `ContainerClient.from_container_url()` takes the container URL whole.
- `put`: `upload_blob(value, overwrite=True)` with the same `IfMissing` and `IfNotModified` conditions as Backend 2.
  `overwrite=True` only drops the SDK's own automatic `If-None-Match: *`; an explicit condition still applies.
  `upload_blob()` returns a dict whose ETag key is `"etag"`.
- A conflict is `ResourceModifiedError`, as for Backend 2.

### Backend 7: Azure Key Vault

Extends the existing `AzureKeyVaultResolver`. `get` reads the secret and its version; `put` without `if_version`
calls `set_secret()`, which always creates a new version. **Either conditional mode raises `IntegrationError`**: Key
Vault has no conditional write for secrets, and a `get`-then-`set` would race. So `SUPPORTS_CAS` is `False`, and Key
Vault cannot be an `output_store`. The SDK types a secret's value and version as optional; both are handled.

## Designed, not built

Four more backends are designed; none has a real consumer yet. Each would follow the table above.

| Backend | `type`   | Conditional modes                                                                      | `SUPPORTS_CAS` | Notes |
| ------- | -------- | -------------------------------------------------------------------------------------- | -------------- | ----- |
| 1       | `file`   | create-only through `O_CREAT \| O_EXCL`; an update against a prior version is refused (no atomic primitive) | **no**: create-only is not enough | Version is the content's SHA-256. On a network share, exclusive create is reliable on NFSv3+ but not guaranteed by SMB; verify the share. |
| 4       | `s3`     | `PutObject` with `IfNoneMatch="*"` / `IfMatch=<etag>`; 412 `PreconditionFailed` is a conflict | yes            | First AWS dependency, as an optional `aws` extra. Conditional writes are a 2024 S3 feature: test against real S3 or MinIO first. |
| 5       | `consul` | `kv.put(cas=0)` / `cas=<ModifyIndex>`                                                  | yes            | `put` returns `False` on a conflict instead of raising; the backend turns it into `KeyValueConflictError`. Optional `consul` extra; `CONSUL_HTTP_ADDR`, `CONSUL_HTTP_TOKEN`. |
| 6       | `etcd`   | a transaction comparing `mod_revision` with `0` / the prior revision                   | yes            | Same boolean conflict as Consul. Optional `etcd` extra (`etcd3gw`, no gRPC native code); the auth mechanism to confirm against the client. |

`consul` and `etcd` are listed in the registry as v1 types not yet ported, and leave that list when built.

## Related Decisions

- [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) — the cross-run output store and the
  `SUPPORTS_CAS` gate.
- [ADR-0057](../decisions/0057-output-record-one-key-per-step.md) — one key per step, and `MAX_VALUE_BYTES`.
- [cross-run-step-output-delivery.md](../archive/cross-run-step-output-delivery.md) — the feature this was split out
  of, with the record shape and key naming.
- `StoreIntegration` (`resolve(key) -> str`) — the sibling capability for values; a separate ABC because it has no
  version, not merged.

## History

- Split out of the cross-run step output design as its first step: the I/O primitive alone, kept generic once a
  lock was shown to need nothing more. The capability was first called `output_store`, then named for its shape.
- The three Azure backends were built first, each after checking the installed SDK's source for the real
  conditional-write parameters and the exception a 412 raises, rather than assuming them.
- A review found that nothing stopped a workspace from choosing Key Vault as its `output_store`: every record write
  would fail, and `record()`'s "never fail the run" rule made that a warning nobody saw. `SUPPORTS_CAS` and the
  validate check came from that. Re-checking the unbuilt designs against the same flag caught the file backend's
  identical gap before it was built.
- `MAX_VALUE_BYTES` came with one key per step (ADR-0057), when a record could outgrow App Configuration's limit.
- The work doc stayed open with the output store "not built" long after deploy used it. With the three backends in
  use and the other four waiting for a consumer, nothing was in progress and it moved to design.
