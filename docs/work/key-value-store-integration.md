# Key/Value Store Integration — Work

- Status: **capability built, plus all three "Real build priority"
  backends built** — Azure Key Vault (Backend 7), Azure App Configuration
  (Backend 2), Azure Blob Storage (Backend 3). `Capability.KEY_VALUE`,
  `KeyValueStoreIntegration` ABC, `KeyValueEntry`, `KeyValueConflictError`,
  `NEW_KEY_VERSION`, `SUPPORTS_CAS` all implemented (see each "Built" note
  below). File, Consul, etcd, S3 remain fully designed but not built.
- `SUPPORTS_CAS: ClassVar[bool]` (default `True`, `AzureKeyVaultResolver`
  overrides to `False`) — a real gap found via code review (2026-10-12):
  nothing previously stopped a workspace from picking Key Vault as its
  `output_store`, which would then silently never persist a single durable
  output (`DeploymentOutputStore.record()` always passes `if_version`, and
  Key Vault's `put()` rejects it unconditionally — downgraded to a mere
  warning by `record()`'s own "never fail the run" design, making the
  failure practically invisible). Now caught two ways: a static,
  credential-free validate-time check (`semantic_checks.py`'s
  `_check_workspace_key_value_store_supports_cas()`, via the new
  `registry.get_class()`) and a defensive runtime check in
  `resolve_output_store_integration()` for a caller that skipped
  validation.
- Re-checked every other backend design against this same gap
  (2026-10-12, "only way to find out is to look"): S3/Consul/etcd
  (Backends 4-6) all genuinely support *both* CAS modes natively, so
  `SUPPORTS_CAS = True` will be accurate for them once built — no
  further gap found there. The **file backend (Backend 1) does have the
  same class of gap, caught before being built this time**: it only
  supports the create-only case, not the update case
  `DeploymentOutputStore.record()` actually depends on after a
  deployment's first write — its own section below is annotated with
  `SUPPORTS_CAS = False` as a build requirement, not left to be
  discovered again the way Key Vault was. `DeploymentOutputStore` is
  still the only real `KeyValueStoreIntegration` consumer today; any
  future CAS-dependent caller must perform the same per-backend check
  before trusting a new one — `SUPPORTS_CAS`'s own docstring in
  `capabilities.py` states this as a general rule, not a
  `DeploymentOutputStore`-specific one.
- Last updated: 2026-10-12
- Split out from: [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md)
  (that doc's "Proposed interface" section) — this is "first step": the
  reusable I/O primitive on its own, independent of the
  `DeploymentOutputStore` business logic, the workspace/deployment
  configuration field, and the `deploy_controller.py` wiring that will
  consume it, all of which stay in the parent doc as later steps.

## Overview

A new, minimal capability — get/put a single small value by key, with
optional compare-and-swap via a backend-returned version tag. The direct
motivation is [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md)
(a deployment's step outputs need to live somewhere durable and shared
across separate `deploy run` invocations/pipelines), but the capability
itself is deliberately generic and reusable — any future feature needing
"store one small record, keyed, possibly contended" can use it without
inventing its own storage layer.

Seven backends are designed in full here, ready to implement: a plain
**file** (network drive/shared filesystem — zero new infrastructure, no
compare-and-swap support), **Azure Key Vault** and **Azure App
Configuration** (both reusing SDK/auth wiring the existing secrets/
variables resolvers already have), **Azure Blob Storage**, **S3**,
**Consul**, and **etcd** — all six of the latter with real
compare-and-swap support except Key Vault (see Backend 7's own note),
via two genuinely different native mechanisms (ETag-conditional-write
for the cloud SDKs vs. index/revision-compare-and-set for Consul/etcd —
see Backends 5-6's own note on this split). Every backend here was
already a recognized v1 integration type (`_KNOWN_V1_TYPES` in
`registry.py`) never ported to v2, or (Key Vault/App Configuration) an
existing v2 integration gaining a second capability — this design is
what finally ports consul/etcd specifically, alongside the cloud-storage
backends that have no v1 precedent at all.

**Real build priority** (settled, supersedes any ordering implied by the
numbering below, which is purely for cross-reference): **Azure Key
Vault, Azure App Configuration, Azure Blob Storage are first** — real
teams already hold Azure credentials/infra for these, and some teams
specifically use Key Vault as a cheaper small-settings store than App
Configuration for non-secret values, which is exactly this capability's
use case, not just secrets. File/Consul/etcd/S3 are fully designed here
too but are not next in line to build.
the three cloud-storage backends that have no v1 precedent at all.

### Terminology: integration vs. capability

These two words get used precisely throughout this doc, not
interchangeably with "backend":

- **Capability** — the behavior *contract*: `Capability.KEY_VALUE` (the
  enum string) + `KeyValueStoreIntegration` (the ABC, `get`/`put`). Exists
  exactly once, independent of any backend. A controller that wants to
  read/write a small record asks for "something implementing
  `KeyValueStoreIntegration`" — it never names a specific backend class.
- **Integration** — a concrete *class*, registered in `registry.py`'s
  `_KNOWN` under one `type` string (e.g. `"file"`, `"azure-appconfig"`,
  `"consul"`), representing a connection to one specific external system.
  One integration can implement *multiple* capabilities at once —
  `AzureAppConfigResolver` already does (`VARIABLES` + `FEATURES` today,
  gaining `KEY_VALUE` here) — and a capability says nothing about which
  integration(s) provide it.
- Each of "Backend 1" through "Backend 6" below is an **integration**
  implementing the **`KEY_VALUE` capability** — never "a capability" on
  its own. This distinction is what makes the lock-reuse question below
  a non-issue: a different future feature wanting the same capability
  doesn't need a new capability, a new ABC, or even a new integration —
  it can reuse any of these six as-is, or add a seventh, independently.

### Validated reuse: this capability is enough to build a lock on top, unchanged

Confirmed before finalising the ABC below (the file/Consul/etcd backend
sections each note the specific mechanism): a **distributed lock**
— e.g. a future "backend locks" feature serialising concurrent access to
the same deployment's state — needs nothing from this capability beyond
what's already here. The standard CAS-based lock recipe (the same one
Consul's and etcd's own official docs describe, built on nothing but
their plain KV compare-and-set) is just:

- **Acquire**: `put(lock_key, holder_id, if_version=NEW_KEY_VERSION)` —
  succeeds only if the key doesn't exist yet; `KeyValueConflictError`
  means someone else holds it, caller backs off/retries.
- **Release**: `put(lock_key, b"", if_version=<the version acquire
  returned>)` — succeeds only if still held by that same acquisition;
  `KeyValueConflictError` here would mean releasing a lock the caller no
  longer actually holds (a bug to surface loudly, not swallow).
- **Re-acquire after a release**: `get(lock_key)` first (the key now
  exists with an empty/"released" value), then `put(lock_key, holder_id,
  if_version=<the version get() just returned>)` — an ordinary CAS
  update, not the create-only path.

No third ABC method (e.g. `delete()`) is needed for this — confirmed
deliberately, since not every backend here can support a *conditional*
delete as cleanly as a conditional put (S3 in particular has no
conditional `DeleteObject` even after its 2024 conditional-writes
feature, which only covers `PutObject`). Keeping the ABC at just
`get`/`put` means every backend's lock support rests on exactly the same
primitive its KEY_VALUE capability already has to provide regardless.

This holds unconditionally for Backends 2-6 (their CAS is a server-side
protocol guarantee). Backend 1 (file) can run this same recipe, but only
with the caveat its own section spells out — real lock correctness over
a genuine network share depends on that share's exclusive-create
behavior actually holding, not just on the recipe being followed.

## Current Design

### Capability

New `Capability` enum member (`integration_model.py`):

```python
KEY_VALUE = "key_value"  #: Small, keyed records needing optional compare-and-swap writes
```

Named for the capability's own shape (get/put-with-CAS), not the first
feature that needed it (`output_store` was the original working name —
renamed once the lock-reuse question above confirmed this capability
serves more than one feature; matches this enum's own "generic
behavior, not one feature" style already used by `VARIABLES`/`SECRETS`/
`FEATURES`).

### The ABC

```python
# capabilities.py

class KeyValueStoreIntegration(Integration):
    """Capability: `key_value`. Raw get/put of a single small value by
    key, with optional compare-and-swap via a backend-returned version tag.

    Deliberately not a `StoreIntegration` (`resolve(key) -> str`, no
    version concept at all) — a real shape mismatch for "read/write a
    small record with optional CAS," not a style preference. A class
    needing both capabilities (e.g. a future `AzureAppConfigResolver`
    extension) can inherit both ABCs and declare both capability strings;
    nothing about this ABC assumes it's the only one a class implements.
    """

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
            IntegrationError: the store is unreachable or unauthenticated.
        """

    @abstractmethod
    def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
        """Write `value` to `key`, returning the new version.

        Args:
            if_version: three distinct modes, matching the three real
                states a caller needs (a lock's create-then-update-then-
                reacquire lifecycle needs all three, see "Validated reuse"
                above):
                - omitted/`None` — unconditional write, no guard.
                - `NEW_KEY_VERSION` (the reserved sentinel `"0"`) — the
                  write only succeeds if `key` does **not already exist**
                  (a lock's first-ever acquire; also what the Azure/S3
                  backends map to `IfNoneMatch`/`IfMissing` instead of an
                  ETag match, and what Consul/etcd already use `0` for
                  natively in their own documented CAS conventions).
                - any other value — the write only succeeds if the
                  backend's current version for `key` still equals it
                  exactly (an ordinary CAS update).

        Raises:
            KeyValueConflictError: `if_version` was given and the
                condition above didn't hold — the caller should re-read
                and retry its own merge, never force the write through
                by retrying without `if_version`.
            IntegrationError: the store is unreachable/unauthenticated, or
                this backend cannot honor `if_version` at all for the
                case given (e.g. the file backend rejects an ordinary
                version-matched update, though it DOES support the
                create-only case — see Backend 1).
        """
```

**Built (2026-10-12), exactly as designed above**: `Capability.KEY_VALUE`
(`integration_model.py`), `KeyValueStoreIntegration`/`NEW_KEY_VERSION`
(`capabilities.py`), `KeyValueEntry` (`resolved_context.py`),
`KeyValueConflictError` (`errors.py`, a real `IntegrationError` subclass),
and a `Capability.KEY_VALUE: KeyValueStoreIntegration` entry in
`CAPABILITY_ABCS` (so `find_capability_mismatches()` covers it like every
other core capability). One deviation from the sketch above: `NEW_KEY_VERSION`
is a plain module-level `str` constant, not `Final`-annotated — this
repo's mypy strict config doesn't require it and no other constant here
uses the annotation either. Covered by new tests in
`test_integrations_capabilities.py` (ABC completeness, `CAPABILITY_ABCS`
pairing, the sentinel's value, `KeyValueEntry`'s frozen/value-equality,
`KeyValueConflictError`'s subclass relationship) and
`test_models_integration.py` (the capability string validates on an
`Integration` document). Full suite green (2316 passed), mypy/ruff/
lint-imports clean. No backend implements this yet — Key Vault/App
Configuration/Blob are next, per "Real build priority" above.

`NEW_KEY_VERSION` — the reserved `if_version` sentinel (`capabilities.py`,
alongside the ABC):

```python
NEW_KEY_VERSION: Final = "0"
```

`KeyValueEntry` — a small frozen dataclass, same home and convention as
`SecretMetadata` (`resolved_context.py`):

```python
@dataclass(frozen=True)
class KeyValueEntry:
    """One `KeyValueStoreIntegration.get()` result — the stored bytes and
    the backend's own version/ETag for them, consumed by a caller's
    compare-and-swap retry loop."""

    value: bytes
    version: str
```

New exception (`errors.py`), a subclass of `IntegrationError` so existing
`except IntegrationError` call sites still catch it, while a caller that
specifically wants to retry on conflict can catch it by name:

```python
class KeyValueConflictError(IntegrationError):
    """`KeyValueStoreIntegration.put(if_version=...)` lost a compare-and-
    swap race — another writer updated the key first. The caller should
    re-read the current value and retry its own merge, never retry the
    same write unconditionally (that would silently discard the other
    writer's update)."""
```

### Backend 1: file

New `strata/integrations/file_keyvalue_store.py`, `TYPE = "file"`. The
simplest possible backend — a plain path on a network drive/shared
filesystem/single persistent agent, no new dependency at all.

- **Path** comes from `IntegrationModel.spec.configuration["path"]` (the
  existing free-form passthrough field every integration already has —
  no new model field needed) — one file per `key` (`key` itself is the
  filename, sanitised the same way `path_safety.py`'s existing helpers
  already sanitise a user-supplied path segment elsewhere in this
  codebase, not reinvented here).
- `get()`: read the file if present; `version` is `sha256(data).hexdigest()`
  — always cheaply derivable from the content itself, no separate
  bookkeeping file needed. Returns `None` if the file doesn't exist.
- `put(if_version=None)`: write unconditionally (plain
  `path.write_bytes(value)`).
- `put(if_version=NEW_KEY_VERSION)`: **does have a real atomic primitive
  — on a local disk.** Open with `os.open(path, os.O_CREAT | os.O_EXCL |
  os.O_WRONLY)`, which a local filesystem fails atomically if the file
  already exists; a failed open raises `FileExistsError`, caught and
  re-raised as `KeyValueConflictError`. **Caveat specific to this
  backend actually being "a file on a network drive" (its own stated
  deployment mode), not local disk**: `O_EXCL`'s atomicity is a POSIX
  local-filesystem guarantee, not a protocol-level one — it carries over
  reasonably on NFSv3+/NFSv4 (modern clients translate it into a real
  server-side exclusive-create check), but SMB/CIFS (the common case for
  a Windows-mapped "network drive") has no equivalently well-defined
  exclusive-create guarantee in the protocol itself; real behavior
  depends on the specific server/client redirector and should be
  verified against the actual target share, not assumed. Net effect:
  this backend's create-only CAS — and therefore its "Validated reuse"
  lock-acquire support above — is **best-effort** when `path` is a true
  network share, not a hard guarantee the way Backends 2-6's server-side
  CAS is. For genuine multi-writer/multi-machine lock correctness,
  prefer one of the real backends; this one's practical niche is
  single-writer use, or a share/protocol whose exclusive-create behavior
  has actually been verified.
- `put(if_version=<a real prior version>)`: **raises `IntegrationError`**
  — unlike the create-only case, matching an *existing* file's prior
  content hash before overwriting has no atomic OS primitive to rest on
  (a plain write has no optimistic-concurrency check), so this specific
  case is honestly unsupported rather than faking a weaker guarantee.
  Matches this doc's own accepted trade-off from the parent doc's Open
  Questions: no concurrency protection for *updates* on this backend,
  documented, not blocking — dependent steps never race with each other
  regardless, and this backend is the single-agent/low-concurrency case
  by nature (a consumer that needs full CAS picks a backend that
  supports it).
- **`SUPPORTS_CAS` must be `False` on this backend when built** (found via
  code review, 2026-10-12, re-checking every backend design against the
  flag added after Key Vault's own gap) — even though create-only
  (`NEW_KEY_VERSION`) genuinely works, the update case does not, and
  `DeploymentOutputStore.record()` needs *both* (`NEW_KEY_VERSION` only
  for a deployment's very first write ever; every write after that is an
  update against a real prior version). Reporting `True` here because
  "create-only works" would be exactly as misleading as Key Vault's own
  original gap — a caller would discover the real limitation only on a
  deployment's *second* step/run, not its first. See `SUPPORTS_CAS`'s own
  docstring in `capabilities.py` for the general rule this generalizes:
  both modes, not just one, or it's `False`.

### Backend 2: Azure App Configuration — BUILT (2026-10-12)

Extends the **existing** `AzureAppConfigResolver`
(`azure_appconfig_resolver.py`) rather than adding a new class/config —
it already has the endpoint/credential/client wiring this needs. Adds
`Capability.KEY_VALUE` to its `CAPABILITIES` frozenset alongside the
existing `VARIABLES`/`FEATURES`, and implements `KeyValueStoreIntegration`
as a second base class.

Implemented exactly as designed below, with mypy/ruff/lint-imports clean
and 22 tests green in `test_azure_appconfig_resolver.py` (14 new,
covering `get()`/`put()` success, not-found, no-value,
unreachable-store, and — unlike Key Vault — real CAS conflict scenarios
for both `NEW_KEY_VERSION` and an explicit prior ETag, via a fake client
that actually enforces `match_condition` semantics). Confirmed
empirically before implementing (inspected the installed
`azure-appconfiguration` source directly, not assumed): `set_configuration_setting(
configuration_setting, match_condition=MatchConditions.Unconditionally, *,
etag=None, **kwargs)` — `etag` is a separate keyword-only parameter, not
read off `configuration_setting.etag` unless `etag` itself is omitted;
`put()` passes it explicitly rather than mutating the `ConfigurationSetting`
object.

- `get(key)`: `client.get_configuration_setting(key=key)` (already used
  by `resolve()`) — on success, `KeyValueEntry(value=setting.value.encode(),
  version=setting.etag)`; `None` on `ResourceNotFoundError` (not an error
  here, unlike `resolve()`, which raises — a missing key is an expected,
  normal `get()` outcome for this capability, not a resolution failure).
- `put(key, value, if_version=None)`: unconditional
  `set_configuration_setting(ConfigurationSetting(key=key,
  value=value.decode()))`.
- `put(key, value, if_version=NEW_KEY_VERSION)`: same call with
  `match_condition=azure.core.MatchConditions.IfMissing` (maps to HTTP
  `If-None-Match: *`) and no `etag` — succeeds only if `key` doesn't
  exist yet.
- `put(key, value, if_version=<a real prior etag>)`: same call with
  `etag=if_version, match_condition=azure.core.MatchConditions.IfNotModified`
  (maps to `If-Match: <etag>`) — succeeds only if the setting's current
  ETag still matches. **Confirmed empirically** (checked the installed
  `azure-appconfiguration`/`azure-core` source directly): the exception
  on either conditional-write mismatch is `azure.core.exceptions.
  ResourceModifiedError` (HTTP 412 Precondition Failed, mapped by
  azure-core's own HTTP pipeline — "will not be raised directly," per
  its own docstring, only ever via a failed request) — that's what
  `put()` catches and re-raises as `KeyValueConflictError`.
- Values are stored as plain configuration values (strings) — the small
  JSON record `DeploymentOutputStore` (parent doc) will serialise fits
  comfortably within Azure App Configuration's per-value size limit.

### Backend 3: Azure Blob Storage — BUILT (2026-10-12)

New `strata/integrations/azure_blob_keyvalue_store.py`, `TYPE =
"azure-blob"`, `TRANSPORTS = {"sdk"}` — same shape as
`AzureKeyVaultResolver`/`AzureAppConfigResolver`: endpoint from
`config.spec.endpoints.address` (the container URL, e.g.
`https://<account>.blob.core.windows.net/<container>`) falling back to an
`AZURE_BLOB_CONTAINER_URL` env var, `DefaultAzureCredential()` auth, lazy
`_client`/`_get_client()` building a `ContainerClient`. New hard
dependency `azure-storage-blob` — added alongside `azure-identity`/
`azure-keyvault-secrets`/`azure-appconfiguration` in `pyproject.toml`'s
main `dependencies` (same precedent: this repo already commits to the
Azure SDK family unconditionally for its proven backends, not behind an
optional extra). Registered in `registry.py`'s `_KNOWN` (new entry, no
existing class/type to extend here, unlike Backends 2/7).

Implemented exactly as designed below, with mypy/ruff/lint-imports clean
and 15 new tests in `test_azure_blob_keyvalue_store.py`, plus
`test_integrations_registry.py`/`test_integrations_capabilities.py`
updated to cover the new registry entry and its pass through
`find_capability_mismatches()`. Two real SDK facts confirmed empirically
before implementing (inspected the installed `azure-storage-blob` source
directly, not assumed): `ContainerClient.from_container_url(container_url,
credential=...)` accepts the combined container URL directly (no manual
account-URL/container-name split needed, despite `ContainerClient.__init__`
itself taking them separately); and `upload_blob()`'s `overwrite=True`
only skips the SDK's own *automatic* `If-None-Match: *` guard (added only
when `overwrite=False` and no caller-supplied condition exists — traced
into `upload_block_blob()`'s real source) — it does not strip out an
explicitly passed `etag`/`match_condition`, confirming `overwrite=True`
composes safely with both conditional branches below rather than
conflicting with them. `upload_blob()`'s return value is a plain
`Dict[str, Any]` (not an object with a `.etag` attribute, unlike
`download_blob()`'s result) — confirmed its key is `"etag"` by tracing
into `return_response_headers()`'s real header-normalisation logic
(lowercases and strips the `x-ms-` prefix from the raw HTTP response
headers).

- `get(key)`: `container_client.get_blob_client(key).download_blob()` —
  `KeyValueEntry(value=downloader.readall(), version=properties.etag)`;
  `None` on `ResourceNotFoundError`.
- `put(key, value, if_version=None)`: unconditional
  `blob_client.upload_blob(value, overwrite=True)`.
- `put(key, value, if_version=NEW_KEY_VERSION)`: same call with
  `match_condition=azure.core.MatchConditions.IfMissing` — succeeds only
  if the blob doesn't exist yet (no `etag` passed).
- `put(key, value, if_version=<a real prior etag>)`: same call with
  `etag=if_version, match_condition=azure.core.MatchConditions.IfNotModified`
  — succeeds only if the blob's current ETag still matches. Same
  conditional-write pattern as Backend 2, native to the Blob SDK
  (`upload_blob` accepts `etag`/`match_condition` directly, no separate
  conditional-request plumbing needed). Conflict exception type:
  `ResourceModifiedError`, now re-confirmed directly against the
  installed `azure-storage-blob` package itself (no longer inherited
  reasoning only).

### Backend 4: S3 (and S3-compatible)

New `strata/integrations/s3_keyvalue_store.py`, `TYPE = "s3"`,
`TRANSPORTS = {"sdk"}`. The first AWS dependency this repo would ever
take — no real consumer has proven AWS usage (`/memories/repo/
v1-consumer-usage.md`: Azure-only so far), so unlike the three Azure SDKs
above, `boto3` goes in `pyproject.toml`'s `[project.optional-dependencies]`
as a new `aws` extra (`aws = ["boto3>=1.34"]`) — `registry.py`'s lazy
import (D2) means `strata validate`/every other command never pays for it
unless an `s3` integration is actually declared.

- **Bucket/endpoint** from `config.spec.endpoints.address` (the bucket
  name, or a full endpoint URL for an S3-compatible non-AWS store —
  `boto3.client("s3", endpoint_url=...)` when it looks like a URL, plain
  AWS bucket otherwise) falling back to an `AWS_S3_BUCKET`/`AWS_S3_ENDPOINT_URL`
  env var pair. Auth via boto3's own default credential chain (env vars /
  shared config / instance role) — no strata-specific wiring, same
  reasoning `AzureKeyVaultResolver`'s docstring gives for
  `DefaultAzureCredential`.
- `get(key)`: `client.get_object(Bucket=bucket, Key=key)` —
  `KeyValueEntry(value=response["Body"].read(), version=response["ETag"])`;
  `None` on a `ClientError` with code `NoSuchKey`.
- `put(key, value, if_version=None)`: unconditional
  `put_object(Bucket=bucket, Key=key, Body=value)`.
- `put(key, value, if_version=NEW_KEY_VERSION)`: `put_object(Bucket=bucket,
  Key=key, Body=value, IfNoneMatch="*")` — succeeds only if the object
  doesn't exist yet.
- `put(key, value, if_version=<a real prior etag>)`: `put_object(Bucket=bucket,
  Key=key, Body=value, IfMatch=if_version)` — succeeds only if the
  object's current ETag still matches. Relies on S3's conditional-write
  support (`IfMatch`/`IfNoneMatch` on `PutObject`) — a genuinely recent S3
  feature (2024), **to verify empirically** that the target
  bucket/region (and any non-AWS S3-compatible store a consumer might
  point this at) actually honors it before relying on it; a conflict
  surfaces as a `ClientError` with HTTP 412 / error code
  `PreconditionFailed`, caught and re-raised as `KeyValueConflictError`.
- Flag explicitly in code/docs that this backend's CAS guarantee is only
  as strong as the target store's support for conditional `PutObject` —
  weaker footing than the two Azure SDKs (which have had conditional
  writes for years), so this is the one backend worth a real integration
  test against actual S3 (or MinIO) before trusting it in production.

### Backend 5: Consul

New `strata/integrations/consul_keyvalue_store.py`, `TYPE = "consul"`,
`TRANSPORTS = {"sdk"}`. New dependency `python-consul2` (no proven real
usage yet, same reasoning as S3 — goes in `pyproject.toml` as a new
`consul` optional extra, not a hard dependency). Endpoint from
`config.spec.endpoints.address` (e.g. `https://consul.example.com:8500` —
the exact example `IntegrationEndpointsModel.address`'s own docstring
already uses) falling back to `CONSUL_HTTP_ADDR`; auth via Consul's ACL
token, read from `CONSUL_HTTP_TOKEN` (no `spec.authentication` wiring, same
"env var only" precedent as every SDK-based resolver here).

- **Version shape differs from the cloud SDKs**: Consul's KV store has no
  ETag — each key carries an integer `ModifyIndex`, so `KeyValueEntry.version`
  is `str(modify_index)` here (still a plain string at the ABC boundary;
  only this backend's internals treat it as an int).
- `get(key)`: `client.kv.get(key)` returns `(index, entry)` — `entry is
  None` means missing (-> `None`); else `KeyValueEntry(value=entry["Value"],
  version=str(entry["ModifyIndex"]))`.
- `put(key, value, if_version=None)`: `client.kv.put(key, value)` —
  unconditional, no `cas` param.
- `put(key, value, if_version=NEW_KEY_VERSION)`: `client.kv.put(key,
  value, cas=0)` — Consul's own documented convention: `cas=0` succeeds
  only if the key does not already exist, no special-casing needed on
  strata's side beyond passing `int(if_version)` straight through.
- `put(key, value, if_version=<a real prior index>)`: `client.kv.put(key,
  value, cas=int(if_version))` — succeeds only if `ModifyIndex` still
  matches.
- **Conflict signaling is boolean, not exception-based** — the real
  design wrinkle this backend (and etcd, below) introduces: Consul's
  `kv.put(cas=...)` returns `True`/`False` directly, it does not raise on
  a CAS mismatch the way the Azure SDKs/boto3 do. This backend's `put()`
  must check that return value itself and raise `KeyValueConflictError`
  on `False` — the ABC's own contract ("raises `KeyValueConflictError`")
  stays uniform for every caller; only this backend's implementation
  absorbs the translation from its SDK's boolean-return style.

### Backend 6: etcd

New `strata/integrations/etcd_keyvalue_store.py`, `TYPE = "etcd"`,
`TRANSPORTS = {"sdk"}`. New dependency `etcd3gw` (etcd's gRPC-gateway
HTTP client — avoids a grpc native-extension dependency; no proven real
usage yet, same "optional extra" treatment as Consul/S3: `etcd` extra in
`pyproject.toml`). Endpoint from `config.spec.endpoints.address` (e.g.
`https://etcd.example.com:2379`) falling back to `ETCD_ENDPOINT`; auth via
TLS client cert or `ETCD_USERNAME`/`ETCD_PASSWORD` env vars, whichever
the SDK supports most directly — detail to confirm against the real
client library when implementing.

- **Version shape**: etcd keys carry a `mod_revision` (also an integer,
  conceptually the same role as Consul's `ModifyIndex`) — same treatment,
  `KeyValueEntry.version` is `str(mod_revision)`.
- `get(key)`: etcd3gw's `client.get(key)` — empty result means missing
  (-> `None`); else `KeyValueEntry(value=..., version=str(metadata.mod_revision))`.
- `put(key, value, if_version=None)`: unconditional `client.put(key, value)`,
  no transaction needed.
- `put(key, value, if_version=NEW_KEY_VERSION)`: a `Txn` whose `compare`
  clause asserts `mod_revision(key) == 0` — etcd's own documented
  convention for "this key does not exist yet" (a nonexistent key's
  `mod_revision` is always `0`), `success` branch does the `put`,
  `failure` branch does nothing.
- `put(key, value, if_version=<a real prior revision>)`: same transaction
  shape, `compare` clause asserts `mod_revision(key) == int(if_version)`
  instead — succeeds only if still at that revision.
- **Same boolean-conflict-signaling wrinkle as Consul**: the transaction
  call returns whether its `compare` succeeded, it does not raise — this
  backend's `put()` translates a failed compare into
  `KeyValueConflictError` itself, same as Consul above.

### Backend 7: Azure Key Vault — BUILT (2026-10-12)

Extends the **existing** `AzureKeyVaultResolver`
(`azure_keyvault_resolver.py`) rather than adding a new class/config —
same reasoning as Backend 2's extension of `AzureAppConfigResolver`:
it already has the vault-URL/credential/client wiring this needs. Adds
`Capability.KEY_VALUE` to its `CAPABILITIES` frozenset alongside the
existing `SECRETS`. Motivation (distinct from the other backends): some
teams already use Key Vault as a general small-settings store rather
than App Configuration, specifically because it's cheaper for that
usage pattern — this capability's use case (small keyed records, not
necessarily secret) fits that real practice directly, not just this
feature's own deployment-outputs need.

Implemented exactly as designed below, with mypy/ruff/lint-imports clean
and 25 tests green in `test_azure_keyvault_resolver.py` (10 new, covering
`get()`/`put()` success, not-found, no-value, unreachable-store, and
both `if_version` rejection cases). One typing fact confirmed empirically
before implementing (checked the installed `azure-keyvault-secrets`
source directly): both `SecretProperties.version` and `KeyVaultSecret.value`
are typed `Optional[str]`, not `str` — `get()`/`put()` handle the `None`
case explicitly rather than assuming the SDK's own typing was just being
conservative.

- `get(key)`: `client.get_secret(key)` (already used by `resolve()`) —
  on success, `KeyValueEntry(value=secret.value.encode(),
  version=secret.properties.version)` (Key Vault's own per-update
  version id — a string, fits `KeyValueEntry.version` unchanged); `None`
  on `ResourceNotFoundError`.
- `put(key, value, if_version=None)`: `client.set_secret(key,
  value.decode())` — unconditional. Key Vault secrets are natively
  append-only/versioned (every `set_secret()` call creates a brand-new
  version regardless of what the current one is), so this is simply
  Key Vault's own normal behavior, nothing extra needed.
- `put(key, value, if_version=...)` — **either mode (`NEW_KEY_VERSION`
  or a real prior version) raises `IntegrationError`.** Unlike every
  other cloud backend here, Key Vault's data-plane API has **no
  conditional-write primitive for secrets at all** — no ETag/`If-Match`
  equivalent on `set_secret()`. A `get()`-then-`set_secret()` check
  could approximate create-only semantics, but it would have the exact
  same unavoidable race window as the file backend's own non-atomic
  update case (no atomic test-and-set primitive underneath it), so this
  backend is honest about not supporting `if_version` in any form rather
  than offering a weaker guarantee than it can actually provide — the
  lowest CAS support of any backend here, below even the file backend
  (which at least gets a real atomic create-only guarantee on local
  disk via `O_EXCL`).

### Registry

New entries in `registry.py`'s `_KNOWN`:

```python
"file": ("strata.integrations.file_keyvalue_store", "FileKeyValueStore"),
"azure-blob": ("strata.integrations.azure_blob_keyvalue_store", "AzureBlobKeyValueStore"),
"s3": ("strata.integrations.s3_keyvalue_store", "S3KeyValueStore"),
"consul": ("strata.integrations.consul_keyvalue_store", "ConsulKeyValueStore"),
"etcd": ("strata.integrations.etcd_keyvalue_store", "EtcdKeyValueStore"),
```

`azure-appconfig` and `azure-keyvault` are already registered — no
registry change needed for either, since both are existing classes
gaining a second capability, not a new one. `consul`/`etcd` also move
out of `_KNOWN_V1_TYPES` once built — they're recognized-but-unbuilt
today precisely because no v2 feature needed them yet; this design is
that need.

## Deliberately not in this step

- `DeploymentOutputStore` (the business-logic layer: JSON record shape,
  staleness/trust-window check, the sensitive-key filter, merge-on-write)
  — designed in the parent doc's Implementation Plan; not yet built.
- Wiring into `deploy_controller.py` (the pre-flight check, the actual
  read/record calls around `collect_step_outputs()`) — parent doc, not
  yet built.

## Open Questions

- **Exact SDK conflict-exception type for Backends 2 and 3 (Azure App
  Configuration, Azure Blob) — resolved:** `azure.core.exceptions.
  ResourceModifiedError` for both, independently confirmed against each
  package's own installed source (2026-10-12) — HTTP 412 Precondition
  Failed, mapped by azure-core's shared HTTP pipeline both sit on top of.
- **Whether the target S3/S3-compatible store actually honors
  conditional writes** (Backend 4) — a genuinely recent (2024) S3
  feature; flagged as the one backend worth a real integration test
  against actual S3 (or MinIO) before trusting it in production.
- **etcd's exact auth mechanism** (Backend 6) — TLS client cert vs.
  `ETCD_USERNAME`/`ETCD_PASSWORD`, whichever `etcd3gw` supports most
  directly; left to confirm against the real client library when
  implementing.
- Key naming convention for records this capability stores — settled,
  see [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md)'s
  Implementation Plan step 2 (not this capability's own concern, since
  the capability itself is key-naming-agnostic; recorded there since
  `DeploymentOutputStore` is the first real caller).

## Related

- [cross-run-step-output-delivery.md](cross-run-step-output-delivery.md) —
  the feature this capability is the foundation for.
- `src/strata/integrations/capabilities.py`'s `StoreIntegration` — the
  sibling capability this is modeled alongside, not merged into (shape
  mismatch: single-value resolve vs. versioned get/put).
- `src/strata/integrations/azure_appconfig_resolver.py` — the existing
  class Backend 2 extends in place.
