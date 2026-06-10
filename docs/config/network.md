# Network Configuration

Defines named network topologies with subnets and peering references. YAML files declare address
spaces, subnet CIDRs, and network peering intent — validated by strata (including CIDR overlap
detection) and built into `networks.auto.tfvars.json` for Terraform consumption.

## When to Use

Use the `network` kind when you want to:

- Define address spaces and named subnets as code alongside your infrastructure
- Catch CIDR overlap and containment errors before Terraform ever runs
- Give resources a validated subnet reference (`spoke_app/frontend`) instead of hardcoded CIDRs
- Support per-environment CIDR flexibility via variable or secret references

> **Out of scope:** Route tables, NSG rules (use the [firewall](firewall.md) kind), provider-specific
> subnet properties (delegations, service endpoints), and peering configuration (routes, gateways).
> Strata models the *topology intent* — Terraform handles provider-specific implementation.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: network
meta:
  name: <resource_name>   # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
spec:
  references:              # Optional: declare keys used by var/secret CIDRs
    variables:
      - <var_key>          # Resolved from environment variables at build time
    secrets:
      - <secret_key>       # Injected as TF_VAR_<key> at deploy time
  networks:
    - name: <network_name> # Required: unique within spec
      description: <text>  # Optional
      address_space:
        - value: <cidr>    # one of: literal CIDR notation
          var: <key>       # one of: variable key from spec.references.variables
          secret: <key>    # one of: secret key from spec.references.secrets
      subnets:
        - name: <subnet_name>  # Required: unique within network
          description: <text>  # Optional
          cidr:
            value: <cidr>  # one of: literal CIDR
            var: <key>     # one of: variable key
            secret: <key>  # one of: secret key
      peerings:            # Optional
        - name: <peering_name> # Required: unique within network
          target: <network>    # Required: name of target network in this spec
```

## Top-level Fields

| Field             | Type   | Required | Description                                                                              |
| ----------------- | ------ | -------- | ---------------------------------------------------------------------------------------- |
| `spec.references` | object | No       | Variable and secret key declarations. Required when any CIDR uses `var:` or `secret:`.   |
| `spec.networks`   | array  | Yes      | One or more network definitions. At least one required.                                  |

## spec.references Fields

Declares the variable and secret keys that CIDRs in this file may reference. Required when
any address space or subnet CIDR uses `var:` or `secret:`.

| Field       | Type            | Required | Description                                             |
| ----------- | --------------- | -------- | ------------------------------------------------------- |
| `variables` | list of strings | No       | Keys resolved from environment variables at build time. |
| `secrets`   | list of strings | No       | Keys injected as `TF_VAR_<key>` at deploy time.         |

## Network Fields

| Field           | Type   | Required | Description                                                              |
| --------------- | ------ | -------- | ------------------------------------------------------------------------ |
| `name`          | string | Yes      | Network name — unique within `spec.networks`. `PlatformName` format.     |
| `description`   | string | No       | Human-readable description of the network's purpose.                     |
| `address_space` | array  | Yes      | One or more CIDR ranges for this network. Each entry is a `CidrSource`. |
| `subnets`       | array  | Yes      | At least one subnet required per network.                                |
| `peerings`      | array  | No       | Peering references to other networks in the same spec.                   |

## CidrSource Fields

A CIDR value with three mutually exclusive source types. Used for both `address_space` entries
and subnet `cidr` values.

| Field    | Type   | Required | Description                                                                          |
| -------- | ------ | -------- | ------------------------------------------------------------------------------------ |
| `value`  | string | one of   | Literal CIDR notation (e.g. `10.0.0.0/24`). Validated via `ipaddress.ip_network()`. |
| `var`    | string | one of   | Variable key from `spec.references.variables` — resolved at build time.              |
| `secret` | string | one of   | Secret key from `spec.references.secrets` — injected at deploy time via `TF_VAR_*`.  |

> Exactly one of `value`, `var`, or `secret` must be set per CIDR source.

## Subnet Fields

| Field         | Type       | Required | Description                                                   |
| ------------- | ---------- | -------- | ------------------------------------------------------------- |
| `name`        | string     | Yes      | Subnet name — unique within the parent network.               |
| `description` | string     | No       | Human-readable description of the subnet's purpose.           |
| `cidr`        | CidrSource | Yes      | Single CIDR for this subnet. Same value/var/secret union.     |

## Peering Fields

| Field    | Type   | Required | Description                                                                   |
| -------- | ------ | -------- | ----------------------------------------------------------------------------- |
| `name`   | string | Yes      | Peering name — unique within the parent network.                              |
| `target` | string | Yes      | Name of the target network. Must exist in `spec.networks` within this file.   |

> **v1 scope:** Peerings are lightweight `(name, target)` references only — no routing config,
> gateway settings, or direction. Strata declares the *intent* to peer; Terraform implements it.

## Examples

### Haven (simple flat network)

A home lab with a single network and three subnets — about 12 lines of spec:

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

### Enterprise (multi-network with peerings and variable CIDRs)

A hub-and-spoke topology where address spaces vary by environment. The hub uses literal CIDRs
for well-known subnets; spoke address spaces are injected from environment variables so dev
gets `10.1.0.0/16` while prod gets `10.100.0.0/16`:

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

## CIDR Overlap Detection

The core value of the `network` kind is catching CIDR misconfigurations before Terraform runs.
Strata performs three levels of overlap validation:

### Subnet-to-subnet overlap

Subnets within the same network must not overlap each other. For example, `10.0.0.0/24` and
`10.0.0.128/25` overlap — strata rejects this at validation time.

### Subnet containment

Every subnet CIDR must fit within its parent network's address space. A subnet of `10.1.0.0/24`
in a network with address space `10.0.0.0/16` fails validation — the subnet is outside the
network boundary.

### Cross-network overlap

When two networks have overlapping address spaces:

- **Peered networks** — hard error. Azure, AWS, and GCP all reject peered networks with
  overlapping CIDRs. Strata catches this before `terraform plan` wastes time.
- **Non-peered networks** — warning. Overlapping address spaces in isolated networks may be
  intentional (e.g., dev and staging reusing `10.0.0.0/16` in separate environments).

### Variable and secret CIDRs

When a CIDR uses `var:` or `secret:`, overlap checks are **deferred**. At parse time, strata
validates syntax only. At build time, after variable injection resolves CIDRs to literal values,
the service re-validates the merged result. This matches how DNS handles var-sourced record
values.

## Cross-Kind References

Resources in the workspace reference subnets using a qualified `<network_name>/<subnet_name>`
format on the `subnet` field:

```yaml
# workspace.yaml
spec:
  resources:
    - name: app_server
      file: "@infra/resources/vm-app.yaml"
      subnet: "spoke_app/frontend"
      firewalls:
        - app_firewall
```

The qualified format avoids ambiguity in multi-network setups — two networks could both have
a subnet named `default`. During validation, strata checks that every `resource.subnet`
reference resolves to a declared `<network>/<subnet>` in the loaded network definitions.

## Variable and Secret CIDRs

CIDRs support three mutually exclusive value sources. Choose based on how environment-specific
and sensitive the address space is.

| Source    | Use when                                                    | Build behaviour                                                                          |
| --------- | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `value:`  | The CIDR is the same in every environment                   | Written literally to `networks.auto.tfvars.json`                                         |
| `var:`    | The CIDR varies by environment but is not sensitive          | Resolved from `environment.yaml` at `strata build run`; written to `networks.auto.tfvars.json` |
| `secret:` | The CIDR is sensitive (e.g., enterprise topology is classified) | **Not** written to `networks.auto.tfvars.json`; injected at deploy time as `TF_VAR_<key>` |

### var: — build-time variable resolution

When a CIDR uses `var: <key>`, strata resolves `<key>` from the `spec.variables` section of
your active `environment.yaml` at `strata build run` and writes the resolved value into
`networks.auto.tfvars.json`. Declare the key in both places:

1. `spec.references.variables` in the network YAML file
2. `spec.variables` in the matching `environment.yaml`

### secret: — deploy-time injection

When a CIDR uses `secret: <key>`, the value is emitted as `null` in `networks.auto.tfvars.json`
and the key is registered in `tf_required_secrets.json`. The value is never stored on disk by
strata. Ensure it is available as `TF_VAR_<key>` when Terraform runs.

Declare the key in both places:

1. `spec.references.secrets` in the network YAML file
2. `spec.secrets` in the matching `environment.yaml`

## Linking to a Workspace

Reference one or more network definition files from your workspace YAML using `spec.networks`:

```yaml
# workspace.yaml
spec:
  networks:
    - name: hub_networking
      file: "@infra/networks/hub.yaml"
    - name: spoke_networking
      file: "@infra/networks/spokes.yaml"
```

| Field  | Type   | Required | Description                                                 |
| ------ | ------ | -------- | ----------------------------------------------------------- |
| `name` | string | Yes      | Unique name. Used as the outer key in `networks.auto.tfvars.json`. |
| `file` | string | Yes      | Path to the network YAML file. Supports `@repo-name/` refs. |

Names must be unique across all `networks` entries in the workspace.

## Build Output

Running `strata build run` generates `networks.auto.tfvars.json` in the build directory:

```
build/<deployment>/
  networks.auto.tfvars.json   ← consumed by your Terraform networking module
  firewalls.auto.tfvars.json
  dns.auto.tfvars.json
  platform.json
  ...
```

The file serialises networks, subnets, and peerings as a JSON variable file. Subnets and
peerings are keyed by name — designed for Terraform `for_each` iteration. Your Terraform
module reads it via a `variable "networks" { ... }` declaration and `*.auto.tfvars.json`
auto-load.

## Validation Rules

| Rule                                                                             | Enforcement           |
| -------------------------------------------------------------------------------- | --------------------- |
| `meta.name` must match `^[a-z][a-z0-9_]*$`                                       | Pydantic / model load |
| Exactly one of `value`, `var`, or `secret` must be set per CIDR source (V1)       | Model validator       |
| Literal `value` CIDRs must be valid CIDR notation (V2)                            | Model validator       |
| Network names must be unique within `spec.networks` (V3)                          | Model validator       |
| Subnet names must be unique within each network (V4)                              | Model validator       |
| Peering `target` must reference an existing network in `spec.networks` (V5)       | Model validator       |
| No self-peering — peering target must not equal the network's own name (V6)       | Model validator       |
| Peering names must be unique within each network (V7)                             | Model validator       |
| `var`/`secret` keys must be declared in `spec.references` (V8)                    | Model validator       |
| Subnets within a network must not overlap each other (V9)                         | Model validator       |
| Subnets must fit within their network's address space (V10)                       | Model validator       |
| Cross-network address space overlap: warning (non-peered) or error (peered) (V11) | Model validator       |
| Resource `subnet` references must resolve to a declared network/subnet (V12)      | Dynamic validation    |
| All `var` keys used in CIDRs must exist in environment variables (V13)            | Build-time            |

## Merge Behaviour

When multiple network files are referenced in a workspace, strata merges them into a single
topology before validation:

| Element                | Strategy                                                               |
| ---------------------- | ---------------------------------------------------------------------- |
| Network names          | Merge by name — last-wins for metadata and address space               |
| Subnets within network | Merge by `(network_name, subnet_name)` — last-wins replacement         |
| Peerings               | Merge by `(network_name, peering_name)` — last-wins replacement        |
| Meta (annotations)     | Shallow merge — last-wins per key                                      |
| References             | Union — all declared variables and secrets accumulated                  |

**Post-merge validation:** After merging, strata re-runs CIDR overlap detection on the
combined result. Two files may each be valid independently but create overlaps when combined.

## Template

Create a new network configuration file from the built-in template:

```bash
strata new network my-networks --path config/networks/
```

## Best Practices

- **Use literal CIDRs** for well-known subnets (gateway, firewall, bastion) that never change
  across environments. Reserve `var:` for address spaces that differ per environment.
- **One file per topology** — group hub and spoke networks together when they form a logical
  unit. Split only when different teams own different network segments.
- **Name subnets by purpose** (`frontend`, `database`, `management`), not by CIDR range.
- **Comment subnets** with their intended workload using the `description` field or YAML inline
  comments.
- **Naming:** Lowercase with underscores (`haven_network`, `contoso_networking`).
