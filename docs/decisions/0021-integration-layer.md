# Integration Layer - Capabilities as Contracts, Transport as Configuration

- Status: accepted
- Date: 2026-09-23
- See also: [docs/design/audit-sink-dispatch.md](../design/audit-sink-dispatch.md)
  for the audit sink capability/class added within this ADR's structure.
- Related: [ADR-0011](0011-topology-and-provisioning-decoupling.md)
  (`ProvisionerModel.tool`'s open-string vocabulary is reused here; this ADR
  **removes** that model's `.version` field and adds `.integration` - see
  D4), [ADR-0019](0019-version-pinning.md) (same evidence method, and its
  finding that `pins.tools` has zero real usage is what settles D4),
  [ADR-0020](0020-v1-consumer-feature-priority.md) (the evidence this ADR
  acts on: `build run` renders Terraform *and* Helm; haven's real workloads
  need Terraform, Helm and Compose), [ADR-0022](0022-strata-build-run.md)
  (the first consumer of everything built here)

## Context and Problem Statement

Earlier this session, `strata values get` was built with three store
resolvers (`InfisicalResolver`, `AzureKeyVaultResolver`,
`AzureAppConfigResolver`) - each independently env-var-configured, no shared
base class, cached only for the lifetime of one command
(`value_controller.py`'s ad hoc `_Resolvers`). That was the right scope for
*that* task, but `strata build` is next, and it needs external dependencies
of a different shape: not "resolve a key from a remote store" but "run
`terraform plan` and read its output" - starting with Terraform, then
Compose and Helm for `haven`'s real Forge/Hearth workloads (per ADR-0020).
Without a shared shape, each would reinvent subprocess handling,
availability checking, and version parsing from scratch.

Two requirements make this more than "add a base class for CLI tools":

1. **The same tool can be reached two ways.** `consul kv get my/key` and
   `GET /v1/kv/my/key` do the same thing. One workspace may have the CLI
   installed and configured; another may only have the service API. The
   integration has to handle whichever connection method is configured -
   so CLI-vs-API cannot be a property of the class.
2. **"What version do we expect" is already modelled three times.** See
   below.

v1 already solved this, at real scale: 36 built-in integration types
(`IntegrationFactory._BUILTIN_CLASS_MAP`), a thread-safe singleton
`BaseIntegration` (availability/version/`run_command`/sibling-auth-reuse),
an `IntegrationRegistry` (name → instance), an `IntegrationService`
(loads from `Configuration.spec.integrations[]`, capability-scoped
required-integration preflight), and `capabilities.py` - 16 `Protocol`
classes (`ISecretStore`, `IInfrastructureTool`, `IContainerTool`, ...) plus
a capability-name registry mapping strings to those Protocols.

Explicit direction for this ADR: reuse that *functionality*, leaner, and
shaped so registering the rest of v1's ~36 types later (git, ansible,
bicep, opentofu, vault, consul, etcd, bitwarden, flagsmith, cost/CVE/SIEM/
identity tools - "we will import all existing integrations, just not from
the start") is an incremental addition, not a redesign. Terraform ships
first, Compose and Helm next.

## What v2 already has that v1 didn't start with

`IntegrationModel` (`integration_model.py`) already made the leanest of
v1's decisions on its own: `capabilities` is a plain `frozenset[str]`
checked against a small closed vocabulary
(`VALID_INTEGRATION_CAPABILITIES`), not 16 `Protocol` classes. This design
extends that choice rather than reopening it - the runtime layer should use
the same string vocabulary the model already committed to, not invent a
second one.

`ProvisionerModel` (ADR-0011) already carries `version: str | None` - "Tool
version this provisioner expects... preflight verifies what is present and
fails on a mismatch." This is v1's `ensure_available()`/`validate_version()`
check, already modeled, on the document that actually names a tool.

**And that is a problem, because v1 modelled the same fact three times.**
`IntegrationModel.validation` carries `min_version`/`max_version` for the
same binary; `WorkspaceIacModel.version` carries a pinned tool version set
by `strata versions`; and `WorkspaceIacModel.integration` binds a
provisioner to an integration *"for auth/endpoints/tool-version
validation"*. Three places, one fact, nothing arbitrating - the identical
failure v1 hit with remote checkout locations (recorded in both
`config/remotes.yaml` and `.strata/solution.json`, which drifted and
produced "has not been fetched yet" against an already-fetched repo; see
ADR-0015 and `layout.py`'s module docstring: *"The bug was not the value -
it was that two places owned it"*). v2 has inherited exactly one of the
three so far. D4 picks the owner before the others can be ported back in.

## Decision

### D1 - Capability is the class contract; transport is runtime configuration

Consul makes the point directly: `consul kv get my/key` and `GET
/v1/kv/my/key` are the *same capability* (resolve a value) over two
different *transports*. Two earlier drafts of this ADR both got this wrong
- first by making "CLI tool" and "store" alternate base classes, then by
making transport a *mixin*. Both encode transport in the class hierarchy,
and that cannot express the real requirement:

> one workspace wants consul with the CLI configured, another workspace has
> the consul service API configured

That is the *same integration type*, the *same capability*, chosen
differently per solution - so it is configuration data, not a type. A
mixin would force two classes (`ConsulCliStore`, `ConsulApiStore`) for one
tool, and the registry would have to pick between them by type name, which
is exactly the information it does not have.

So: **the base class owns transport; capability ABCs own the contract.**

```python
# strata/integrations/base.py
class Integration(ABC):
    """Identity, configuration, and every transport an integration may use."""

    TYPE: ClassVar[str]                     # "terraform", "consul", "infisical"
    CAPABILITIES: ClassVar[frozenset[str]]  # core, plus x-* extensions (D9)
    TRANSPORTS: ClassVar[frozenset[str]]    # OPEN strings this class supports:
                                            # cli / http / sdk / socket / grpc / ...
    COMMAND: ClassVar[str | None] = None    # default executable; spec.command wins

    def __init__(self, config: IntegrationModel | None = None) -> None: ...

    @property
    def transport(self) -> str:
        """The configured transport: `spec.transport` when set - validated
        against *this class's own* `TRANSPORTS`, never a global enum; the
        sole supported one when only one; otherwise `cli` if the command is
        on PATH, else the first remaining entry (v1's
        `InfisicalIntegration.prefer_cli` default, which it already applied
        per-call)."""

    # --- cli transport -------------------------------------------------
    def is_available(self) -> bool: ...
    def run(self, *args: str, cwd: Path | None = None,
            env: Mapping[str, str] | None = None,
            timeout: int = 300) -> CommandResult: ...

    # --- api transport -------------------------------------------------
    def request(self, method: str, path: str, **kw: Any) -> HttpResult: ...

    # --- shared --------------------------------------------------------
    def get_version(self) -> str | None: ...
    def ensure_version(self, expected: str | None) -> None: ...
```

Capability ABCs then declare *only* method contracts - nothing about how
the work travels:

```python
# strata/integrations/capabilities.py
class StoreIntegration(Integration):
    """Capability: variables / secrets / features. Resolves a key to a value."""
    @abstractmethod
    def resolve(self, key: str) -> str: ...


class InfraIntegration(Integration):
    """Capability: infrastructure / container. Plans and applies change."""
    @abstractmethod
    def plan(self, path: Path, **kw: Any) -> CommandResult: ...
    @abstractmethod
    def deploy(self, path: Path, **kw: Any) -> CommandResult: ...
    @abstractmethod
    def destroy(self, path: Path, **kw: Any) -> CommandResult: ...
```

A concrete class implements one capability and dispatches internally on
`self.transport`:

```python
class TerraformIntegration(InfraIntegration):
    TYPE = "terraform"
    CAPABILITIES = frozenset({"infrastructure"})
    TRANSPORTS = frozenset({"cli"})          # no network form
    COMMAND = "terraform"

    def plan(self, path, **kw):   return self.run("plan", cwd=path)
    def deploy(self, path, **kw): return self.run("apply", "-auto-approve", cwd=path)


class ConsulIntegration(StoreIntegration):
    TYPE = "consul"
    CAPABILITIES = frozenset({"variables", "secrets"})
    TRANSPORTS = frozenset({"cli", "http"})  # the real dual case
    COMMAND = "consul"

    def resolve(self, key: str) -> str:
        if self.transport == "cli":
            return self.run("kv", "get", key).stdout.strip()
        return self.request("GET", f"/v1/kv/{key}").json()[0]["Value"]
```

One class per *tool*, regardless of how many transports it speaks -
matching v1, where `InfisicalIntegration` was a single class that tried
CLI then fell back to REST. The difference is that the choice is now
declared rather than guessed on every call.

Capability → contract is a fixed, small mapping (no 16-Protocol registry):

| capability string                  | ABC                | contract                          |
| ---------------------------------- | ------------------ | --------------------------------- |
| `variables`, `secrets`, `features` | `StoreIntegration` | `resolve()`                       |
| `infrastructure`, `container`      | `InfraIntegration` | `plan()`, `deploy()`, `destroy()` |
| `sources`                          | *(none yet)*       | remote fetching is not built      |

A class declaring a capability whose ABC it does not implement is a
programming error, caught by a test that walks `_KNOWN` and asserts the
pairing - not something a document author can trigger.

**`TRANSPORTS` is an open vocabulary, not a `cli | api` enum.** A review of
all ~36 v1 integrations found the two-value version is simply false:
`syslog_siem_integration.py:94` opens a raw UDP socket
(`socket.socket(AF_INET, SOCK_DGRAM)`); OTel and etcd speak gRPC;
`lock_local` uses the filesystem; and v2's *own* `azure_keyvault_resolver.py`
and `azure_appconfig_resolver.py` use the Azure SDK's client objects, not
HTTP calls strata makes itself. A closed enum would have had to be reopened
by the third real integration. So `transport` is an open string - the same
treatment `ProvisionerModel.tool` and `Module.spec.type` already receive,
for the same reason - and a value is checked against the declaring class's
`TRANSPORTS`, which is the only scope that can actually know what is
supported. Conventional values: `cli`, `http`, `sdk`, `socket`, `grpc`.

### D2 - One lean, lazily-loaded registry, not a factory + separate registry + service

```python
# strata/integrations/registry.py
_KNOWN: dict[str, tuple[str, str]] = {
    "terraform": ("strata.integrations.terraform", "TerraformIntegration"),
    "infisical": ("strata.integrations.infisical", "InfisicalIntegration"),
    "azure-keyvault": ("strata.integrations.azure_keyvault", "AzureKeyVaultIntegration"),
    "azure-appconfig": ("strata.integrations.azure_appconfig", "AzureAppConfigIntegration"),
    # compose, helm next; then ansible/git/bicep/opentofu/vault/consul/etcd/
    # bitwarden/flagsmith/cost/cve/siem/identity as each gets a real v2
    # consumer. Every addition is one dict entry + one small file - this
    # dict's shape does not change.
}

#: v1 types not yet ported. Only used to make "not built yet" (a real,
#: known type) read differently from "not real" (a typo) in the error.
_KNOWN_V1_TYPES = frozenset({"ansible", "git", "bicep", "opentofu", "vault", ...})

def get(integration_type: str, config: IntegrationModel | None = None) -> Integration:
    """Import and instantiate lazily - so `strata validate` never pays for
    azure-identity, hvac, boto3, google-cloud-*, etc. it doesn't use.

    `config` is the `Integration` document configuring this instance, when
    the solution declares one (D1's transport selection reads it). Omitted
    for a tool that needs nothing but PATH."""
```

This collapses v1's `IntegrationFactory` (type → class) +
`IntegrationRegistry` (name → instance, process-wide singleton) +
`IntegrationService` (loads from Configuration, capability preflight) into
one function. The lazy import is kept - genuinely useful, not bloat, since
it's the only thing standing between "add a Vault integration" and "every
`strata` invocation now hard-depends on `hvac`."

Finding the `IntegrationModel` to pass is the *caller's* job, not the
registry's: `strata.integrations` sits below `strata.services` in the
import-linter contract and cannot reach the document index. A controller
looks the document up by name and hands it down - the same direction
`value_controller.py` already passes data into resolvers.

### D3 - No process-wide singleton; a command-scoped cache keyed by declaration

v1's `BaseIntegration.__new__` thread-safety and `IntegrationRegistry`
singleton solve "authenticate once, reuse across a long-running deploy, and
let a sibling integration reuse another's session" - real problems, but
ones that need `.reset()` calls sprinkled through v1's own test suite to
avoid cross-test contamination. `strata build`/`strata deploy` are each one
short-lived process; the already-proven pattern from `values get`
(`value_controller.py`'s `_Resolvers`, constructed once, reused across the
requested keys, discarded when the command exits) is reused here for
integration instances instead of inventing global state. Revisit only if a real
multi-integration auth-reuse need appears (v1's `set_sibling_resolver`) —
none exists yet.

The cache key is the **declaration name**, not the type. Once transport is
configuration (D1), one solution can legitimately declare two Consuls -
one CLI, one API - and keying by type would collapse them into whichever
was built first. This is the same bug v1 fixed in
`_get_instance_key_static` when it moved from the literal `"default"` to
`config.name`; starting from the fixed behaviour costs nothing.

**This applies to a caller that resolves a *named* document (e.g. Phase
5's `ProvisionerModel.integration` binding) - not to Phase 4's store
resolution, which has no name to key by at all.** `VariableStoreModel`/
`SecretStoreModel`/`FeatureStoreModel` only carry a `store` **type**
(`store: infisical`), never a reference to a specific declaration - so
`value_controller.py`'s cache is keyed by type there, on purpose. Checked
against v1: its own store-to-integration lookup
(`ValueController._get_integration_by_type`) is *also* type-based - it
loops every named registered integration and returns the first one whose
type matches, so a v1 solution with two named Infisical integrations
already got an arbitrary ("first in iteration order") one for store
resolution specifically. v2's single cached instance per type is a more
deterministic version of that same real behaviour, not a weaker one.

**Considered and rejected: closing this gap by requiring `type` to be
unique across a solution's Integration documents.** It would make type-keyed
lookup unambiguous everywhere, but it does so by outlawing the two-Consuls
case two paragraphs up - the exact scenario this decision exists to
support. Document *names* are already guaranteed unique per kind
(`DocumentIndex.add()`, every kind, `integration` included) - that was
never in question. The type-ambiguity is a real but narrow limitation of
one call path (a store's schema has nothing to name), left as-is rather
than traded for a solution-wide constraint with a worse cost.

### D4 - The `Integration` owns the tool version; `ProvisionerModel.version` is dropped

**The integration is what resolves to a binary, so it owns that binary's
version.** D1 puts `COMMAND`, `transport` and `endpoints` on the
`Integration`; "which `terraform` am I invoking" is answered there and
nowhere else. `ensure_version()` is an `Integration` method - having its
expectation arrive from a foreign document would mean the object that
knows what it runs is told by something else what it should be.

**v1 agrees, in the place that matters.** `WorkspaceIacModel.integration`
binds a provisioner to an integration explicitly *"for auth/endpoints/
**tool-version validation**"* (ADR-0079/0080). v1 already routed tool-version
validation through the integration binding.

v1's `WorkspaceIacModel.version` is a different thing that merely looks the
same: *"Pinned tool version for this provisioner. Set by `strata versions`
when a `type:tool` pin targets this provisioner's name."* It is an
**output** of version-pinning machinery - and ADR-0019's census of every
repository on disk found **zero** uses of `pins.tools`. The field is fed by
a mechanism nobody uses. v2's own example solution declares no provisioner
`version:` either.

**What the first draft of this ADR got wrong.** It argued the expectation
varies per workspace (one pins 1.7, another 1.9), so the use site must own
it. But `ensure_version()` inspects what is on `PATH`, and there is exactly
one `terraform` on `PATH` per execution. Two workspaces asserting different
versions of one shared binary is a *contradiction*, not a variation - the
per-workspace field cannot be satisfied even in principle.

**And the one fact per-workspace ownership would buy already has a home.**
"This workspace's code requires >= 1.7" is a property of the code, and
every tool already expresses it natively - `terraform { required_version }`,
a chart's `kubeVersion`. Restating it in strata duplicates a constraint the
tool itself enforces at the point where it can actually act on it.

So the version lives on the integration, and the provisioner names the
integration rather than restating anything (v1's field, kept):

```yaml
kind: integration
meta: { name: terraform }
spec:
  type: terraform
  capabilities: [infrastructure]
  version: "~=1.9"           # what this solution's toolchain provides (PEP 440, checked via `packaging` - Phase 3)
```

```yaml
# workspace
provisioners:
  - name: infra
    tool: terraform
    integration: terraform    # optional - auto-binds when exactly one candidate
```

Auto-bind follows v1's rule verbatim (ADR-0079/0080): when `integration` is
unset, bind to the sole registered integration compatible with this
provisioner's `tool`, and **error rather than guess** when more than one
candidate exists.

`version` is a single constraint string, replacing v1's `validation.min_version`/
`max_version` pair: one field expressing an exact version or a range reads
better than two fields that are usually half-empty, and `ensure_version()`
parses it. v1's `validation.command` is still not ported - D1's `COMMAND`
covers it.

### D5 - `TransportResult` is the contract; `run_command`/`http_request` are the two shipped helpers

Transport is a runtime choice (D1), so a capability method has to be able
to return the same shape whichever way the work travelled. What must be
uniform is therefore the **result**, not the set of protocols: strata will
never ship every protocol, and an earlier draft of this ADR claiming "the
only module that talks to the outside world" was wrong the moment syslog
(UDP) or etcd (gRPC) is ported.

Two helpers are shipped because two cases are overwhelmingly common and
were both duplicated in v1: the subprocess half existed
(`strata.utils.system.run_command`), while every REST-speaking integration
hand-rolled `urllib` inline (`infisical.py`, `azure_keyvault.py`,
`azure_appconfig.py` each building their own `Request`, headers and error
handling) - three copies of the same retry-less, timeout-by-hand code, and
v2 already repeated it once in `infisical_resolver.py`.

```python
# strata/utils/transport.py - the shared result contract, plus two helpers
class TransportResult(Protocol):
    @property
    def is_successful(self) -> bool: ...
    @property
    def payload(self) -> str: ...


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def is_successful(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def payload(self) -> str:
        return self.stdout


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: str
    headers: Mapping[str, str]
    timed_out: bool = False

    @property
    def is_successful(self) -> bool:
        return 200 <= self.status < 300 and not self.timed_out

    @property
    def payload(self) -> str:
        return self.body

    def json(self) -> Any: ...


def run_command(
    args: list[str], *, cwd: Path | None = None,
    env: Mapping[str, str] | None = None, timeout: int = 60,
) -> CommandResult: ...


def http_request(
    method: str, url: str, *, headers: Mapping[str, str] | None = None,
    body: bytes | None = None, timeout: int = 30,
) -> HttpResult: ...
```

Neither raises on a failed call - both report it, so an integration
decides. Both normalise timeouts into the result rather than an exception,
so `if not result.is_successful` is the single check either way. The
shared `TransportResult` protocol is what lets a capability method stay
transport-agnostic (D1's `ConsulIntegration.resolve`).

**A protocol strata does not ship is implemented by the integration that
needs it, in its own module.** There is deliberately no transport registry
and nothing to register: an integration speaking syslog opens a socket in
its own class and returns something satisfying `TransportResult`. The only
thing its author has to know is that protocol - `is_successful` and
`payload`, timeouts folded into the result rather than raised. A syslog
sink is roughly:

```python
class SyslogSink(AuditIntegration):          # capability ABC, D9
    TRANSPORTS = frozenset({"socket"})

    def emit(self, event: str) -> TransportResult:
        try:
            with socket.socket(AF_INET, SOCK_DGRAM) as sock:
                sock.sendto(event.encode(), (self.host, self.port))
        except OSError as exc:
            return SocketResult(ok=False, detail=str(exc))
        return SocketResult(ok=True, detail="")
```

Promoting a protocol into `transport.py` is then a later, evidence-driven
step - done when a second integration needs the same one, not in
anticipation. That is the same rule D1 applies to capability ABCs and
ADR-0019/ADR-0020 apply to features generally.

The two shipped helpers are still where cross-cutting concerns for *those*
protocols belong - retries, proxy handling, TLS verification policy,
request logging/redaction - rather than in every integration that makes an
HTTP call.

### D6 - Model changes: `capabilities` gains `container`; `transport`, `endpoints` and `version` are added

D1 makes the `Integration` document load-bearing for the first time, which
needs five changes to `IntegrationSpecModel`:

```yaml
kind: integration
meta:
  name: consul-prod
spec:
  type: consul
  capabilities: [variables, secrets]
  transport: http                # NEW - open string, checked per-class (D1)
  version: ">= 1.17"             # NEW - replaces v1's validation.min/max (D4)
  command: consul                # NEW - optional override of the class COMMAND
  endpoints:                     # UN-DEFERRED - needed by networked transports
    address: https://consul.example.com:8500
  authentication:
    method: oauth2               # unchanged, already modelled
```

1. **`capabilities` gains `"container"`** (Compose/Helm), distinct from
   `"infrastructure"` (Terraform/Ansible/Bicep) - the one real distinction
   v1's `IContainerTool` vs `IInfrastructureTool` Protocols captured that
   the current five-entry vocabulary doesn't. Both map to the same
   `InfraIntegration` ABC (D1's table): the label says what *kind* of thing
   is provisioned; the contract (`plan`/`deploy`/`destroy`) is identical,
   so a second ABC would be ceremony.
2. **`transport` is added** - an open string (D1), optional. This is the
   field the two-workspace requirement needs, and it has no v1 equivalent
   (v1 hard-coded `prefer_cli=True` in Python). Validation is delegated to
   the resolved class's `TRANSPORTS`, so the model itself enumerates
   nothing.
3. **`endpoints` is un-deferred** - a networked transport is unusable
   without an address. Its deferral reason ("no remote consumer yet") is
   now spent. Validation: required when the chosen transport is networked,
   rejected when the class declares `TRANSPORTS = {"cli"}` only.
4. **`version` is added** (D4) - a single constraint string, collapsing
   v1's `validation.min_version`/`max_version` pair.
5. **`command` is added** - an optional override of the class's `COMMAND`.
   D4 dropped v1's `validation.command` on the grounds that `COMMAND`
   covers it; that holds for a built-in like `terraform`, but not for a
   generic wrapper (v1's `customsecret`/`customapi` types, D10) whose
   binary is only known to the document. This restores v1's
   `_get_command_from_config()` fallback: `spec.command`, else the class
   `COMMAND`, else `spec.type`.

Correspondingly, in `provisioning_model.py`: **`ProvisionerModel.version`
is removed** and **`ProvisionerModel.integration` is added** (D4) - an
optional `References(PlatformKind.INTEGRATION)` field, so
`validate_references` checks it for free.

Still not ported: `validation.command` (D1's `COMMAND` covers it) and the
`customsecret`/`customvariable` allowlist. A sixth field, `lifecycle`, is
added by D11 for its own separate reason.

### D7 - Existing store resolvers are retrofitted, not rewritten

`InfisicalResolver`/`AzureKeyVaultResolver`/`AzureAppConfigResolver` already
have the right method (`resolve(key) -> str`); they become
`StoreIntegration` subclasses with `TYPE`/`CAPABILITIES`/`TRANSPORTS`
declared, and `value_controller.py`'s ad hoc `_Resolvers` is replaced by
`registry.get(...)`.

Their transports differ, which is itself the evidence for D1's open
vocabulary:

| resolver                 | `TRANSPORTS` | what changes                                                 |
| ------------------------ | ------------ | ------------------------------------------------------------ |
| `InfisicalResolver`      | `{"http"}`   | hand-rolled `urllib` moves to `http_request` (D5)            |
| `AzureKeyVaultResolver`  | `{"sdk"}`    | keeps `SecretClient`; **no** transport change                |
| `AzureAppConfigResolver` | `{"sdk"}`    | keeps `AzureAppConfigurationClient`; **no** transport change |

An earlier draft asserted all three moved to `http_request`. That was
simply wrong: only Infisical calls `urllib`: the two Azure resolvers use
the SDK's own client objects, which carry their own auth chain
(`DefaultAzureCredential`) and HTTP stack. Routing them through
`http_request` would mean reimplementing Azure's credential chain, which is
the opposite of leaner.

Internal reorganization only - `resolve_values()`'s external behavior, and
every test in `test_value_controller.py`/`test_commands_values.py`, is
unaffected.

### D8 - `Provider` and `Integration` stay separate kinds; credentials compose rather than duplicate

Raised as a thought exercise while writing this ADR: *why have both, when a
Provider is arguably just a specialization of an Integration?* The overlap
is real and larger than it first looks - four of roughly seven fields are
the same:

| field                                            | `ProviderModel`         | `IntegrationModel` |
| ------------------------------------------------ | ----------------------- | ------------------ |
| `type`                                           | yes (`properties.type`) | yes                |
| `authentication`                                 | yes                     | yes                |
| `configuration` (untyped passthrough)            | yes                     | yes                |
| `custom`                                         | yes                     | yes                |
| `properties.region`, `default_tags`, `lifecycle` | yes                     | no                 |
| `capabilities`, `required`, `enabled`            | no                      | yes                |

**Decision: keep both.** The distinguishing test is *does it end up in the
artifact, or does it produce the artifact?* A Provider renders into output
- a `provider "azurerm" {}` block, `default_tags` stamped onto every
resource. An Integration never appears in rendered output at all; it is the
machinery that renders and talks. Four further asymmetries follow the same
line:

1. **Graph membership.** `Workspace.spec.providers` and
   `Resource.spec.properties.provider_type` mean resources declare "I live
   here." Nothing in the infrastructure graph declares "I use the terraform
   integration" - an integration is selected at execution time via
   `ProvisionerModel.tool` → `registry.get()` (D2).
2. **Type registry.** `ProviderConfig` governs a provider type: valid
   regions, geographies, allowed resource types, per-resource schema
   constraints (ADR-0014). There is no equivalent question for
   "terraform" - no registry of valid terraform regions - so an
   `IntegrationConfig` could not be given content.
3. **Residency policy.** `Tenant.spec.geographies` validates against
   `ProviderConfig.spec.regions[].geography`. Real compliance machinery,
   entirely Provider-side; an Integration has no geography.
4. **One system, two roles, simultaneously.** Azure is `provider:
   azure-main` (where resources are created) *and* `azure-keyvault` (where
   secrets are read from) - v1 additionally had an `azure_cli` integration
   for CLI auth. Merging would not remove that distinction, only relocate
   it into `capabilities` and force one document to be both a deployment
   target and a secret source.

The concrete cost of merging is also plain: `region` would move from a
typed field cross-checked against `ProviderConfig` into untyped
`configuration: {}`, losing that Phase 2 check, the Tenant geography check,
and `default_tags` semantics - typed policy-bearing fields traded for a
dict.

**But the overlap on `authentication` is not incidental - it is a missing
relationship.** Talking to a cloud (quota checks, managed identity
resolution, `az login` session state) is integration work; v1 had
`azure_cli`/`aws_cli`/`gcloud_cli` integrations for exactly that. The right
fix is composition, not merger:

```yaml
kind: provider
meta: { name: azure-main }
spec:
  properties: { type: azure, region: westeurope }
  integration: azure-cli      # instead of an inline `authentication:` block
  default_tags: { managed-by: strata }
```

Provider keeps what makes it a Provider (registry-validated type, region,
tags); the "how do I authenticate and reach this cloud" half delegates to
an Integration document, which after D1/D6 already owns transport, auth and
capabilities. That removes the one genuinely duplicated concern instead of
collapsing two distinct concepts.

This is the same shape D4 adopts for `ProvisionerModel.integration`, which
is in turn v1's own `WorkspaceIacModel.integration` field - so "a document
that needs to reach an external system names an Integration" becomes one
consistent pattern across Provisioner and Provider, not a one-off.

Not built in this pass: nothing consumes provider credentials yet (no
build/deploy layer), so adding `provider.spec.integration` now would be
inert - the same "wait for the consumer" rule D6 had to spend for
`endpoints`. Recorded here so the duplication is a known, decided-about
state rather than an oversight.

### D9 - Core capabilities are closed and dispatchable; `x-` extensions are open and inert

v1 shipped 16 capability Protocols *plus* a second mechanism whose only job
was to let a user declare an integration strata had no class for: nine
generic wrapper types (`customsecret`, `customvariable`, `customkeyvalue`,
`customfeature`, `customrepository`, `custominfrastructure`,
`customcontainer`, `customapi`, `customaudit`) and a
`CUSTOM_TYPE_CAPABILITY_MAP` binding each to a capability.

v2's `VALID_INTEGRATION_CAPABILITIES` is closed on purpose -
`integration_model.py`: *"a capability is an abstract contract the codebase
itself dispatches on, not an arbitrary tool name"*. That reasoning is right
and stays. Taken alone, though, it makes a user-written integration
undeclarable, which is why v1 needed the second mechanism at all.

**Two tiers instead of two mechanisms:**

- **Core** - closed set, each with exactly one ABC, dispatched on by
  strata. Today `variables`/`secrets`/`features` -> `StoreIntegration`,
  `infrastructure`/`container` -> `InfraIntegration`, `sources` -> no ABC
  yet.
- **Extension** - any `x-`-prefixed string (`x-ticketing`, `x-pagerduty`).
  Accepted, never dispatched on, carried for the integration's own use and
  for display by a future `strata tools status`.

An unprefixed unknown capability remains an **error** - that is the typo
case, and a closed core is exactly what makes it catchable. `x-` is the
author stating "I know strata does not know this one." Same convention as
HTTP `X-` headers and Kubernetes annotations, and the same distinction
ADR-0019 drew between `stale_pin` (probably a mistake) and
`pin_not_applicable` (deliberate but inapplicable).

**One ABC per core capability, added when its consumer is.** Named
`<Capability>Integration`, in `capabilities.py`. Two exist today; `sources`
is declarable but not dispatchable until `SourceIntegration`
(`fetch`/`checkout`) is built. Known future ones, each waiting on a real
consumer: audit sinks (`emit`), identity (`login`/`token`), cost
(`estimate`), security scan (`scan`), diagram render (`render`), and
**locking** (`acquire`/`release`) - `DeploymentLockingModel` already exists
in v2 and v1 ships six lock backends (`lock_azurerm`, `lock_consul`,
`lock_gcs`, `lock_s3`, `lock_tfc`, `lock_local`), so locking is scheduled
rather than speculative.

The pairing test (D1) tolerates a core capability with no ABC yet; it fails
only when an ABC exists and a class declaring that capability does not
implement it.

### D10 - Custom integrations register through entry points

`_KNOWN` (D2) holds built-ins. A user-written integration is not in it, and
v1 had `IntegrationFactory.register_type()` for exactly that. v2 also
already *promises* this extensibility one layer up: `ProvisionerModel.tool`
keeps an unrecognized tool schema-valid specifically because "v1's real
`DeployerFactory` supports user-registered provisioner plugins" - a promise
with no loading mechanism behind it.

Mechanism: Python entry points, group `strata.integrations`.

```toml
# a third-party package's pyproject.toml
[project.entry-points."strata.integrations"]
servicenow = "strata_servicenow:ServiceNowIntegration"
```

`registry.get()` consults built-ins first, then entry points resolved at
lookup time. `pip install strata-servicenow` is the entire installation
step - no configuration edit, no plugin path, nothing registered at
runtime. A name collision with a built-in is an error, never a silent
override.

What a custom integration author must know, and nothing beyond it:

1. Subclass the ABC for the capability provided (D9) - or no ABC at all,
   declaring only `x-` capabilities, when strata is never meant to dispatch
   to it.
2. Declare `TYPE`, `CAPABILITIES`, `TRANSPORTS`.
3. Implement the capability's methods - using `run_command`/`http_request`
   when the protocol is one of the two shipped, or their own code returning
   a `TransportResult` when it is not (D5).

This deliberately does **not** port v1's nine `custom*` wrapper types. They
existed to make a class-less integration declarable from YAML alone; entry
points solve that by making a class cheap to supply instead, without a
parallel type vocabulary and its capability map to keep in sync. If
evidence later shows real demand for a no-code wrapper, the answer is a
*single* generic class configured entirely from the document (`spec.type`,
`spec.command`, `spec.endpoints`) - one class, not nine types.

### D11 - `Integration` gains `lifecycle`, restricted to Python scripts for now

`IntegrationSpecModel` is the only kind that carries external-system
configuration but has no hook mechanism. Six kinds already have
`lifecycle: CommonLifecycleModel` (deployment, module, namespace, provider,
resource, workspace) and `DeploymentStageModel` has a bare
`scripts: ScriptsModel`; v1's `IntegrationModel.lifecycle` existed too and
v2 deferred it with the reason *"no hook-execution machinery"*. D1 makes
the document load-bearing, so the gap is now worth closing.

**`lifecycle`, not a bare `scripts`.** The bare form is used exactly once,
on `DeploymentStageModel` - correctly, because a stage *is* already a
phase, so there is nothing to key by. An Integration is not a phase: a
hook has to say *when* (before connecting, after resolving, on teardown),
which is what the phase-keyed open map exists for. Following the six
majority kinds also means no new shape to learn.

**Scripts are referenced, never discovered.** `ScriptPathModel.file` is an
explicit relative path, already validated twice at Phase 1 -
`validate_relative_path()` (must not escape the solution) and an extension
allowlist. Globbing a directory and executing what is found would make a
dropped file sufficient to make strata run it; explicit declaration keeps
the decision in a reviewable diff. Nothing in v2 discovers scripts, and
nothing should.

**Restricted to `.py` for now.** `SCRIPT_EXTENSIONS` allows seven
extensions (`.sh`, `.bash`, `.py`, `.ps1`, `.js`, `.mjs`, `.go`), but
nothing anywhere dispatches a script to an interpreter: v1's only executor
hardcodes `run_command(["python", str(script_path)], ...)` in
`terraform_builder.py`. A `.ps1` hook would therefore pass schema
validation and then fail at run time - the worst combination, since the
document looked correct. Integration hooks accept `.py` only until
interpreter dispatch exists, which turns a runtime failure into an
authoring-time error:

```python
# common_models.py - the allowlist becomes a parameter
def validate_script_file(value: str, allowed: frozenset[str] = SCRIPT_EXTENSIONS) -> str: ...

# integration_model.py
_INTEGRATION_SCRIPT_EXTENSIONS = frozenset({".py"})
```

Widening this is a one-line change once an extension -> interpreter map
exists (`.ps1` -> `pwsh -File`, `.sh` -> `bash`, `.py` -> `sys.executable`).
That map belongs beside `run_command` in `transport.py` (D5), since it is
a property of how a subprocess is launched, not of any one kind.

**Still deferred: `transport: script`.** A script satisfying a capability
contract directly - `spec.command: ./scripts/fetch-secret.py` implementing
`StoreIntegration.resolve()` - would be the no-code custom integration
D10 declines to build, and is a plausible replacement for v1's nine
`custom*` wrapper types. It needs its own contract first (how a value is
returned: stdout? JSON on stdout? what exit codes mean), which is
`TransportResult` re-expressed as a process convention. Not decided here;
recorded so the option is a known one rather than a rediscovery.

## Consequences

- Good: the same integration type serves a CLI-configured workspace and an
  API-configured one without a second class, because transport is data.
- Good: one class per tool, one capability ABC per contract - adding
  Compose/Helm next, and the other ~30 v1 types over time, is one dict
  entry + one small file each time, matching the explicit "not from the
  start" direction.
- Good: the shared `TransportResult` contract (D5) lets an integration
  speak a protocol strata never shipped without asking permission - no
  transport registry, nothing to register, just a result shape to satisfy.
  The two shipped helpers still centralise retries/proxy/TLS/redaction for
  the two common protocols.
- Good: a user can ship an integration as an installable package (D10)
  without editing strata, which is what `ProvisionerModel.tool`'s
  open-string docstring has been promising with nothing behind it.
- Good: lazy import keeps optional SDKs (hvac, boto3, google-cloud-*, ...)
  out of the default install until something actually registers that type.
- Good: no global singleton to `.reset()` between tests - a real wart in
  v1's own suite, avoided by construction.
- Good: exactly one document owns the expected tool version (D4), so the
  v1/ADR-0015 "two places owned it" failure cannot recur here.
- Bad: five model fields change (D6) rather than none, and `endpoints`
  comes out of deferral earlier than the "wait for a consumer" rule would
  normally allow - justified only because D1's requirement *is* that
  consumer.
- Good: `Integration` hooks fail at authoring time rather than run time
  (D11) - accepting only the extension something can actually execute is
  narrower than `SCRIPT_EXTENSIONS`, but honest about what exists.
- Bad: `transport` and `capabilities`' `x-` tier are both open vocabularies
  (D1, D9), so a wrong value surfaces at resolution rather than at schema
  validation. Accepted for the same reason `ProvisionerModel.tool` is open:
  a closed list cannot enumerate what a plugin author will need, and this
  review proved a `cli | api` enum wrong against v1's own integrations.
- Bad: an integration implementing an unshipped protocol carries code the
  project does not review or test centrally (D5). Mitigated by the
  `TransportResult` contract being small, and by promoting a protocol into
  `transport.py` once a second integration needs it.
- Bad: no capability-based "find me anything that can do X" query yet
  (v1's `get_integrations_with_capability`) - only direct `type` lookup.
  Revisit if/when something needs "any available secrets store" without
  caring which (e.g. a future required-integration preflight, or a `strata
  tools status` command).
- Bad: `ProvisionerModel.version` is removed (D4) - a field ADR-0011
  introduced. Justified by its v1 origin (an output of the unused
  `pins.tools` mechanism) and zero usage in v2's example solution, but it
  is still a reversal of an earlier decision, and ADR-0011 should be
  annotated to point here.
- Bad: `transport: auto` resolution (D1) means behaviour can differ between
  a developer laptop with the CLI installed and a runner without it.
  Mitigated by letting a solution pin `transport` explicitly; v1 had the
  same ambiguity with no way to pin it at all.
- Neutral: `authentication` remains modelled on both `ProviderModel` and
  `IntegrationModel` (D8). Known duplication, deliberately left until a
  consumer exists, rather than an unnoticed one.

