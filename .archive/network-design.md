# Network Kind — Architecture Design Spec

_Author: Danny (Lead Architect)_
_Date: 2026-06-10_
_Status: DESIGN — not yet implemented_
_Requested by: Vincent Huybrechts_

---

## 1. Purpose & Scope

The `network` kind models **named network topologies with subnets** — provider-agnostic
address-space and subnet definitions that strata validates structurally. The core value
proposition is:

- **CIDR overlap detection** — catch misconfigurations before Terraform ever runs
- **Named subnet references** — resources declare which subnet they belong to by name,
  validated at build time against a known registry of subnets
- **Per-environment CIDR flexibility** — the value/var/secret union lets address spaces
  differ across environments (dev gets `10.0.0.0/16`, prod gets `10.1.0.0/16`)

### In scope

| Concern                      | Status                                |
| ---------------------------- | ------------------------------------- |
| Address spaces (CIDRs)       | ✅                                     |
| Named subnets with CIDRs     | ✅                                     |
| Peering references (by name) | ✅ v1 lightweight — name + target only |

### Out of scope

| Concern                                  | Rationale                                         |
| ---------------------------------------- | ------------------------------------------------- |
| Route tables                             | Terraform territory — too provider-specific       |
| NSG rules                                | `firewall` kind handles this                      |
| Provider-specific subnet properties      | Delegations, service endpoints = Terraform config |
| Peering configuration (routes, gateways) | v2 concern — v1 captures the reference only       |

---

## 2. Example YAML

### 2.1 Haven (simple — flat home lab)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: network
meta:
  name: haven_network
  annotations:
    description: "Home lab flat network"
spec:
  networks:
    - name: home_lab
      address_space:
        - value: "10.0.0.0/16"
      subnets:
        - name: default
          cidr:
            value: "10.0.0.0/24"
        - name: services
          cidr:
            value: "10.0.1.0/24"
        - name: management
          cidr:
            value: "10.0.2.0/24"
```

~12 lines of spec — meets the "~10 lines YAML" target for haven.

### 2.2 Enterprise (multi-network with per-env CIDRs)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: network
meta:
  name: contoso_networking
  annotations:
    description: "Contoso enterprise network topology"
  labels:
    region: westeurope
spec:
  references:
    variables:
      - hub_address_space
      - spoke_app_address_space
      - spoke_data_address_space
    secrets: []
  networks:
    - name: hub_vnet
      address_space:
        - var: hub_address_space
      subnets:
        - name: gateway
          cidr:
            value: "10.0.0.0/27"
          description: "VPN/ExpressRoute gateway subnet"
        - name: firewall
          cidr:
            value: "10.0.0.32/26"
          description: "Azure Firewall subnet"
        - name: bastion
          cidr:
            value: "10.0.0.128/26"
          description: "Azure Bastion subnet"
        - name: management
          cidr:
            value: "10.0.1.0/24"

    - name: spoke_app
      address_space:
        - var: spoke_app_address_space
      subnets:
        - name: frontend
          cidr:
            value: "10.1.0.0/24"
        - name: backend
          cidr:
            value: "10.1.1.0/24"
        - name: integration
          cidr:
            value: "10.1.2.0/24"
      peerings:
        - name: to_hub
          target: hub_vnet

    - name: spoke_data
      address_space:
        - var: spoke_data_address_space
      subnets:
        - name: database
          cidr:
            value: "10.2.0.0/24"
        - name: analytics
          cidr:
            value: "10.2.1.0/24"
      peerings:
        - name: to_hub
          target: hub_vnet
```

---

## 3. Model Hierarchy

```
NetworkModel                         # Top-level document (apiVersion + kind + meta + spec)
├── apiVersion: PlatformVersion      # frozen = v1
├── kind: PlatformKind               # frozen = NETWORK
├── meta: NetworkMetaModel
│   ├── name: PlatformName
│   ├── annotations: Optional[Dict]
│   ├── labels: Optional[Dict]
│   └── tags: Optional[List]
└── spec: NetworkSpecModel
    ├── references: Optional[NetworkReferencesModel]
    │   ├── variables: VariableRefs
    │   └── secrets: SecretRefs
    └── networks: List[NetworkDefinitionModel]  (min_length=1)
        ├── name: PlatformName
        ├── description: Optional[str]
        ├── address_space: List[CidrSourceModel]  (min_length=1)
        │   ├── value: Optional[str]    ─┐
        │   ├── var: Optional[str]       ├─ exactly one set (union)
        │   └── secret: Optional[str]   ─┘
        ├── subnets: List[SubnetModel]  (min_length=1)
        │   ├── name: PlatformName
        │   ├── description: Optional[str]
        │   └── cidr: CidrSourceModel   (same union)
        └── peerings: Optional[List[PeeringReferenceModel]]
            ├── name: PlatformName
            └── target: str             (network name reference)
```

### 3.1 Field Details

| Model                    | Field           | Type                    | Required | Notes                                                  |
| ------------------------ | --------------- | ----------------------- | -------- | ------------------------------------------------------ |
| `CidrSourceModel`        | `value`         | `Optional[str]`         | union    | Literal CIDR — validated via `ipaddress.ip_network()`  |
| `CidrSourceModel`        | `var`           | `Optional[str]`         | union    | Variable key — resolved at build time from environment |
| `CidrSourceModel`        | `secret`        | `Optional[str]`         | union    | Secret key — resolved at deploy time via `TF_VAR_*`    |
| `NetworkDefinitionModel` | `name`          | `PlatformName`          | yes      | Network name — unique within spec                      |
| `NetworkDefinitionModel` | `address_space` | `List[CidrSourceModel]` | yes      | One or more CIDRs for this network's address space     |
| `NetworkDefinitionModel` | `subnets`       | `List[SubnetModel]`     | yes      | At least one subnet required per network               |
| `SubnetModel`            | `name`          | `PlatformName`          | yes      | Subnet name — unique within network                    |
| `SubnetModel`            | `cidr`          | `CidrSourceModel`       | yes      | Single CIDR for this subnet                            |
| `PeeringReferenceModel`  | `target`        | `str`                   | yes      | Name of the target network (must exist in same spec)   |

---

## 4. Validation Rules

### 4.1 Structural validation (Pydantic model validators)

| #   | Rule                                    | Scope                    | Validator type    |
| --- | --------------------------------------- | ------------------------ | ----------------- |
| V1  | Exactly one of value/var/secret set     | `CidrSourceModel`        | `model_validator` |
| V2  | `value` is valid CIDR when literal      | `CidrSourceModel`        | `model_validator` |
| V3  | Unique network names within spec        | `NetworkSpecModel`       | `model_validator` |
| V4  | Unique subnet names within each network | `NetworkDefinitionModel` | `model_validator` |
| V5  | Peering target exists in spec.networks  | `NetworkSpecModel`       | `model_validator` |
| V6  | No self-peering (target ≠ own name)     | `NetworkDefinitionModel` | `model_validator` |
| V7  | Unique peering names within network     | `NetworkDefinitionModel` | `model_validator` |
| V8  | References declared for used var/secret | `NetworkSpecModel`       | `model_validator` |

### 4.2 CIDR overlap detection (the core value)

| #   | Rule                                                  | Scope                    | Notes                                                                                                                                                                                                                                  |
| --- | ----------------------------------------------------- | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| V9  | Subnets within a network must not overlap each other  | `NetworkDefinitionModel` | Only when all CIDRs are literals                                                                                                                                                                                                       |
| V10 | Subnets must fit within their network's address space | `NetworkDefinitionModel` | Only when both are literals                                                                                                                                                                                                            |
| V11 | Cross-network address space overlap = **warning**     | `NetworkSpecModel`       | Warning, not error — peered networks may intentionally share space, and overlapping address spaces in non-peered networks may be valid (e.g., isolated environments). Hard error only if both networks have a mutual peering declared. |

**CIDR validation implementation:**
```python
import ipaddress

def cidrs_overlap(a: str, b: str) -> bool:
    """Check if two CIDR blocks overlap."""
    net_a = ipaddress.ip_network(a, strict=False)
    net_b = ipaddress.ip_network(b, strict=False)
    return net_a.overlaps(net_b)

def cidr_contained(subnet: str, supernet: str) -> bool:
    """Check if subnet fits within supernet."""
    sub = ipaddress.ip_network(subnet, strict=False)
    sup = ipaddress.ip_network(supernet, strict=False)
    return sub.subnet_of(sup)
```

**When var/secret sources are used:** CIDR overlap checks are deferred — they run only when
all CIDRs resolve to literals. At parse time, the model validates syntax only. At build time,
after variable injection, the builder or service can re-validate with resolved values.

### 4.3 Cross-kind validation (dynamic phase)

| #   | Rule                                                         | Where                                |
| --- | ------------------------------------------------------------ | ------------------------------------ |
| V12 | Resource subnet references resolve to a declared subnet name | `NetworkService._validate_dynamic()` |
| V13 | All var keys used in CIDRs exist in environment variables    | Build-time                           |

---

## 5. Value/Var/Secret Union — `CidrSourceModel`

Follows the exact pattern from `DnsRecordModel`:

```python
class CidrSourceModel(PlatformBaseModel):
    """CIDR value with value/var/secret union — exactly one must be set."""

    value: Optional[str] = Field(None, description="Literal CIDR notation, e.g. '10.0.0.0/24'")
    var: Optional[str] = Field(None, description="Variable key — resolved at build time")
    secret: Optional[str] = Field(None, description="Secret key — resolved at deploy time via TF_VAR_*")

    @model_validator(mode="after")
    def validate_exactly_one_source(self) -> "CidrSourceModel":
        sources = [f for f in (self.value, self.var, self.secret) if f is not None]
        if len(sources) != 1:
            raise ValueError("Exactly one of value, var, or secret must be set for CIDR source.")
        return self

    @model_validator(mode="after")
    def validate_cidr_format(self) -> "CidrSourceModel":
        if self.value is not None:
            try:
                ipaddress.ip_network(self.value, strict=False)
            except ValueError as e:
                raise ValueError(f"Invalid CIDR: {self.value}") from e
        return self
```

**Where it applies:**
- `NetworkDefinitionModel.address_space` — list of `CidrSourceModel` (networks can have multiple address ranges)
- `SubnetModel.cidr` — single `CidrSourceModel`

**Why secret for CIDRs?** Uncommon but valid — some enterprises treat network topology as
sensitive data. The union pattern supports it for consistency without mandating it.

---

## 6. Cross-Kind References — How Resources Reference Subnets

### 6.1 Resource model changes

Resources currently declare dependencies via `ResourceDependencyModel` with `category: "networking"`.
For subnet references, add a new optional field to `WorkspaceResourceModel`:

```yaml
# In workspace.yaml — resource definition
resources:
  - name: app_server
    file: "@infra/resources/vm-app.yaml"
    subnet: "spoke_app/frontend"        # <network_name>/<subnet_name>
    firewalls:
      - app_firewall
```

The `subnet` field uses a **qualified name** format: `<network_name>/<subnet_name>`.

**Alternative considered and rejected:** Using `configuration` dict for subnet references.
Rejected because it's unvalidated — strata's value is catching misconfigurations before Terraform.

### 6.2 Reference resolution

```
workspace.yaml                  network.yaml
┌───────────────────┐           ┌──────────────────────┐
│ resources:        │           │ spec.networks:       │
│   - name: app_vm  │──subnet──▶│   - name: spoke_app  │
│     subnet:       │  ref      │     subnets:         │
│     "spoke_app/   │           │       - name: frontend│
│      frontend"    │           │         cidr: ...     │
└───────────────────┘           └──────────────────────┘
```

**Validation:** During `_validate_dynamic`, the workspace service checks that every
`resource.subnet` reference resolves to a valid `<network>/<subnet>` in the loaded
network definitions. This is the same pattern as firewall reference validation
(`validate_firewall_references` on `WorkspaceSpecModel`).

### 6.3 Future: topology-level network binding

In v2, topologies could reference a network definition:
```yaml
topology:
  - name: azure_primary
    provider: azure
    network: hub_vnet        # binds topology to network
```
This is deferred — v1 keeps network references at the resource level only.

---

## 7. Touchpoint List

Every file that needs modification for the `network` kind:

| #   | File                                             | Change                                                                        |
| --- | ------------------------------------------------ | ----------------------------------------------------------------------------- |
| 1   | `src/strata/models/common_models.py`             | Add `NETWORK = "network"` to `PlatformKind` enum                              |
| 2   | `src/strata/models/network_model.py`             | **NEW** — all models from §3                                                  |
| 3   | `src/strata/models/workspace_model.py`           | Add `WorkspaceNetworkModel(name, file)` class                                 |
| 4   | `src/strata/models/workspace_model.py`           | Add `networks: Optional[List[WorkspaceNetworkModel]]` to `WorkspaceSpecModel` |
| 5   | `src/strata/models/workspace_model.py`           | Add `validate_unique_networks` validator                                      |
| 6   | `src/strata/models/workspace_model.py`           | Add `subnet: Optional[str]` to `WorkspaceResourceModel`                       |
| 7   | `src/strata/models/platform_artifact_model.py`   | Add `PlatformNetworkModel` (flattened) with `from_network_model()`            |
| 8   | `src/strata/models/platform_artifact_model.py`   | Add `networks: Optional[List[PlatformNetworkModel]]` to `PlatformSpecModel`   |
| 9   | `src/strata/services/network_service.py`         | **NEW** — `NetworkService(BaseService)` with `merge_networks()`               |
| 10  | `src/strata/validators/platform_validator.py`    | Add `PlatformKind.NETWORK: NetworkService` to `_KIND_TO_SERVICE`              |
| 11  | `src/strata/builders/terraform_builder.py`       | Add `_build_network_vars()` method                                            |
| 12  | `src/strata/builders/terraform_builder.py`       | Write `networks.auto.tfvars.json` in `_save_terraform_vars()`                 |
| 13  | `tests/strata/models/test_models_network.py`     | **NEW** — model validation tests                                              |
| 14  | `tests/strata/services/test_services_network.py` | **NEW** — service + merge tests                                               |
| 15  | `tests/data/network/`                            | **NEW** — test YAML fixtures (standard, overlapping, multi-net, var-refs)     |
| 16  | `docs/config/network.md`                         | **NEW** — reference documentation                                             |
| 17  | `src/strata/models/__init__.py`                  | Export `NetworkModel` (if `__init__` re-exports)                              |

**17 touchpoints** — comparable to DNS (15). The two extras are the `WorkspaceResourceModel.subnet`
field addition (#6) and cross-kind reference validation that DNS didn't need.

---

## 8. Merge Strategy

### 8.1 How multiple network files combine

Same pattern as DNS zones. Multiple network YAML files can be referenced in a workspace:

```yaml
# workspace.yaml
spec:
  networks:
    - name: hub_networking
      file: "@infra/networks/hub.yaml"
    - name: spoke_networking
      file: "@infra/networks/spokes.yaml"
```

**Merge rules:**

| Element                  | Strategy                                                       |
| ------------------------ | -------------------------------------------------------------- |
| Network names            | Merge by name — last-wins for metadata/address_space           |
| Subnets within network   | Merge by `(network_name, subnet_name)` — last-wins replacement |
| Peerings                 | Merge by `(network_name, peering_name)` — last-wins            |
| Meta (annotations, etc.) | Shallow merge — last-wins per key                              |
| References               | Union — all declared variables/secrets accumulated             |

**Post-merge validation:** After merge, re-run CIDR overlap detection on the merged result.
Two files may each be valid independently but create overlaps when combined.

### 8.2 `NetworkService.merge_networks()` signature

```python
@staticmethod
def merge_networks(models: List[NetworkModel]) -> NetworkModel:
    """Merge multiple NetworkModel instances into one.
    
    Network merge by name (last-wins for network-level fields).
    Subnet merge by (network_name, subnet_name) — replacement, not dedup.
    Peering merge by (network_name, peering_name) — replacement.
    """
```

Follows `DnsService.merge_dns()` pattern exactly.

---

## 9. tfvars Output Shape

### 9.1 What the Terraform builder emits

File: `networks.auto.tfvars.json`

```json
{
  "networks": {
    "hub_networking": {
      "description": "Contoso hub network",
      "labels": { "region": "westeurope" },
      "tags": [],
      "networks": {
        "hub_vnet": {
          "address_space": ["10.0.0.0/16"],
          "subnets": {
            "gateway": {
              "cidr": "10.0.0.0/27",
              "description": "VPN/ExpressRoute gateway subnet"
            },
            "firewall": {
              "cidr": "10.0.0.32/26",
              "description": "Azure Firewall subnet"
            },
            "bastion": {
              "cidr": "10.0.0.128/26",
              "description": "Azure Bastion subnet"
            },
            "management": {
              "cidr": "10.0.1.0/24",
              "description": null
            }
          },
          "peerings": {}
        },
        "spoke_app": {
          "address_space": ["10.1.0.0/16"],
          "subnets": {
            "frontend": {
              "cidr": "10.1.0.0/24",
              "description": null
            },
            "backend": {
              "cidr": "10.1.1.0/24",
              "description": null
            }
          },
          "peerings": {
            "to_hub": {
              "target": "hub_vnet"
            }
          }
        }
      }
    }
  }
}
```

### 9.2 Design notes on tfvars shape

- **Outer key** is the workspace network attachment name (from `workspace.spec.networks[].name`)
- **Inner key** is the network definition name (from `spec.networks[].name`)
- **Subnets** keyed by name — Terraform `for_each` friendly
- **Peerings** keyed by name — lightweight, target-only in v1
- **address_space** is a list — networks can have multiple address ranges
- **CIDRs are always resolved** to literal strings in tfvars — var/secret sources are
  resolved before emission (var at build time, secret emitted as `null` with a
  corresponding entry in `tf_required_secrets.json`)
- **`description: null`** included (not omitted) — uniform schema for Terraform
  `object({ cidr = string, description = string })` type constraint

### 9.3 Secret CIDRs

If a CIDR uses `secret` source, the builder emits `null` in the tfvars and registers
the key in `tf_required_secrets.json`. Same pattern as DNS secret records.

---

## 10. Architecture Decisions

### AD-NET-1: CidrSourceModel as a reusable union type

**Decision:** Create `CidrSourceModel` as a dedicated model for the value/var/secret union
around CIDR values, rather than inlining the three fields on every model that needs a CIDR.

**Rationale:** Address spaces use `List[CidrSourceModel]` (multiple ranges), subnets use a
single `CidrSourceModel`. A dedicated model avoids duplication and makes the union validation
DRY. Follows the same pattern principle as `DnsRecordModel` but extracted because CIDRs
appear in two structural positions.

### AD-NET-2: Subnets required per network (min_length=1)

**Decision:** Every network must declare at least one subnet.

**Rationale:** A network without subnets has no operational value in strata's model — resources
reference subnets, not networks directly. An empty network would pass validation but be
unreferenceable, which is misleading. If someone only needs an address space declaration without
subnets, they should use raw Terraform — strata's value is in the subnet registry.

### AD-NET-3: Peering as lightweight reference, not configuration

**Decision:** Peerings in v1 are `(name, target)` pairs only — no routing config, no gateway
settings, no direction.

**Rationale:** Peering configuration is deeply provider-specific (Azure VNet peering ≠ AWS VPC
peering ≠ GCP VPC peering). Strata's value is declaring the *intent* — "these two networks
peer" — so cross-network CIDR overlap becomes a hard error for peered networks. The actual
peering implementation is Terraform's job. This keeps the model ~3 lines per peering and
avoids a provider-abstraction rabbit hole.

### AD-NET-4: Qualified subnet references (`network/subnet` format)

**Decision:** Resources reference subnets using `<network_name>/<subnet_name>` string format
on `WorkspaceResourceModel.subnet`.

**Rationale:** A bare subnet name would be ambiguous in multi-network setups (two networks
could both have a subnet named `default`). The qualified format is explicit, grep-friendly,
and trivially splittable for validation. Alternatives considered:
- Nested object `{network: ..., subnet: ...}` — over-engineering for a single reference
- Dot notation `network.subnet` — conflicts with PlatformName regex (dots not allowed)
- Separate fields `network` + `subnet` — coupling that doesn't add clarity

### AD-NET-5: CIDR overlap is a warning for non-peered, error for peered

**Decision:** Cross-network address space overlap produces a warning unless both networks
declare a mutual peering, in which case it's a hard error.

**Rationale:** Non-peered networks may legitimately overlap — e.g., dev and staging using
the same `10.0.0.0/16` range in isolated environments. But peered networks with overlapping
CIDRs will fail at the provider level (Azure/AWS both reject overlapping peered VNets).
Strata catches this before `terraform plan` wastes time.

### AD-NET-6: CIDR validation deferred for var/secret sources

**Decision:** CIDR overlap detection runs only when all involved CIDRs resolve to literal
values. Variable/secret sources bypass CIDR validation at parse time.

**Rationale:** Models must load without a real filesystem or environment context (per
copilot-instructions.md: "Do not call `Path.exists()` inside model validators"). The same
principle applies to CIDR resolution — variables aren't available until build time. The
service layer re-validates after variable injection. This matches how DNS handles
var-sourced record values.

### AD-NET-7: Merge strategy follows DNS zone merge pattern

**Decision:** Network merge by name (last-wins), subnet merge by `(network_name, subnet_name)`
(replacement), post-merge CIDR re-validation.

**Rationale:** Consistent with `DnsService.merge_dns()` which merges zones by name and records
by `(name, type)`. Replacement (not dedup) is correct because two files may intentionally
override a subnet's CIDR for different deployment contexts. Post-merge validation catches
conflicts that arise from combining independently-valid files.

### AD-NET-8: Separate tfvars file (`networks.auto.tfvars.json`)

**Decision:** Network data gets its own tfvars file, not merged into an existing one.

**Rationale:** Follows the established pattern: `firewalls.auto.tfvars.json`,
`dns.auto.tfvars.json`, `namespaces.auto.tfvars.json` — each kind gets its own file.
Keeps Terraform variable declarations modular and makes `terraform plan` diffs readable.

### AD-NET-9: No provider field on NetworkSpecModel (unlike DNS)

**Decision:** The network kind does NOT have a `spec.provider` field.

**Rationale:** DNS has `spec.provider` because DNS zone management is multi-provider in
the same workspace (INWX for external, Route53 for internal — simultaneously). Networks
are bound to a single provider through the workspace topology. The provider is already
declared on `WorkspaceTopologyModel.provider`. Adding it to the network model would create
a redundant, potentially contradictory source of truth.

### AD-NET-10: `networks` (plural) as workspace spec field name

**Decision:** The workspace field is `spec.networks` (not `spec.network` or `spec.network_topologies`).

**Rationale:** Follows the pattern: `spec.firewalls`, `spec.dns_zones`, `spec.namespaces`.
Plural because it's a list of references. `_topologies` suffix rejected because `topology`
already has a specific meaning in the workspace model (`WorkspaceTopologyModel`).

---

## 11. File Layout

```
src/strata/models/network_model.py       # CidrSourceModel, SubnetModel, NetworkDefinitionModel,
                                           # PeeringReferenceModel, NetworkReferencesModel,
                                           # NetworkSpecModel, NetworkMetaModel, NetworkModel

src/strata/services/network_service.py    # NetworkService(BaseService) + merge_networks()

tests/data/network/
  network-haven.yaml                       # Simple haven example
  network-enterprise.yaml                  # Multi-network enterprise example  
  network-overlapping-subnets.yaml         # Invalid: overlapping subnets (test expects error)
  network-peered-overlap.yaml              # Invalid: peered networks with overlapping CIDRs
  network-var-refs.yaml                    # CIDRs using var/secret sources
  network-duplicate-names.yaml             # Invalid: duplicate network/subnet names

tests/strata/models/test_models_network.py
tests/strata/services/test_services_network.py
docs/config/network.md
```

---

## 12. Open Questions (for implementation phase)

| #   | Question                                                               | Default if not decided                                        |
| --- | ---------------------------------------------------------------------- | ------------------------------------------------------------- |
| Q1  | Should `ResourceDependencyModel` gain a `subnet` field too?            | No — keep it on `WorkspaceResourceModel` only                 |
| Q2  | Should peering be bidirectional by default (declare once, imply both)? | No — explicit in both networks. Less magic.                   |
| Q3  | Should we validate subnet CIDRs against IPv4 only, or support IPv6?    | Support both — `ipaddress.ip_network()` handles both natively |

---

## 13. Relationship to Existing Kinds

```
┌─────────────┐     references      ┌──────────────┐
│  workspace  │────subnet ref──────▶│   network    │
│  (resource  │                     │  (subnets,   │
│   entries)  │                     │   CIDRs)     │
└──────┬──────┘                     └──────────────┘
       │                                    │
       │ firewalls ref                      │ CIDR values
       ▼                                    ▼
┌─────────────┐                     ┌──────────────┐
│  firewall   │                     │ environment  │
│  (NSG rules │                     │ (var values  │
│   with CIDR │                     │  per env)    │
│   from/to)  │                     └──────────────┘
└─────────────┘
```

The `network` kind is orthogonal to `firewall` — firewalls reference CIDRs in their `from`/`to`
fields, but those are inline values, not subnet references. A future enhancement could let
firewall rules reference subnets by name (`from: subnet:spoke_app/frontend`), but that's out
of scope for this design.
