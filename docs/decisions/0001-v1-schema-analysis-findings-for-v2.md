# Strata v1 Schema Analysis — Key Findings for v2

- Status: proposed
- Date: 2026-09-20
- Related: none yet — this ADR is the seed for follow-up ADRs as each item below is decided

## Context and Problem Statement

Before designing the v2 schema from scratch, we audited strata v1's kind schemas
(`provider`, `resource`, `network`, `dns`, `firewall`, `namespace`, `module`,
`workspace`) for internal consistency, structural gaps, and architectural issues.
This ADR is a raw findings report — nothing here has been decided yet. Each
discrepancy and architectural issue is a candidate to review and resolve
(keep as-is, fix, or drop) before or during v2 schema implementation.

## Reference Model Consistency

✓ **CONSISTENT:** All infrastructure kinds share the same reference pattern
- Provider, Resource, Network, DNS, Namespace, Module all support:
  - `spec.references.variables` — list of key names
  - `spec.references.secrets` — list of key names
  - `spec.references.features` — list of key names (v/s/f only)

**Important:** References are **just key name lists**, NOT key-value mappings.
- NO conversion/renaming happens
- Key names stay consistent from declaration through environment resolution
- Environment config maps keys to actual values and stores

## Discrepancies Worth Noting

### 1. Firewall — No References Support ❌

**Gap:** Firewall is the ONLY kind without `spec.references`
- Can't parametrize ports per environment
- Can't reference network CIDRs via `var:`/`secret:`
- Hardcoded rules only — defeats multi-environment deployments

**Impact:** Production vs staging require duplicate firewall files.

```yaml
# ❌ This doesn't work in v1
spec:
  references:
    variables: [internal_network_cidr]
  allow:
    - direction: in
      from: ${internal_network_cidr}  # NOT SUPPORTED
```

**V2 Consideration:** Add references support to firewall.

### 2. DNS — Unique `output_key:` Feature 🎯

**Only DNS has this:** Source record values from preceding deployment stage outputs

```yaml
records:
  - name: "@"
    type: A
    output_key: vm_public_ip  # ← unique to DNS
```

- Breaks the reference pattern (outputs vs vars/secrets)
- Creates hard dependency on stage execution order
- Only works within single `strata deploy run` invocation
- Terraform-specific pattern (relies on stage output injection)

**V2 Question:** Generalize stage outputs for all kinds, or accept DNS-only?

### 3. DNS vs Network — Structural Inconsistency

**DNS:** Flat union at top level

```yaml
records:
  - name: "@"
    type: A
    value: "1.2.3.4"      # one of three
    var: api_endpoint
    secret: backup_ip
```

**Network:** Nested object with value/var/secret inside

```yaml
networks:
  - name: hub
    address_space:
      - value: "10.0.0.0/16"    # nested
        var: hub_cidr
        secret: vault_cidr
```

**V2 Consideration:** Standardize on one pattern (probably DNS's flat union is cleaner).

### 4. Network — Peerings (Cross-Network References)

**Only network supports:** Native references to other networks in same file

```yaml
peerings:
  - name: to_hub
    target: hub_vnet  # references another network in spec
```

No equivalent in provider, resource, dns, firewall, namespace, module.

### 5. Resource — `subcategory` Field

**Resource-only:** Second categorization level beyond labels

```yaml
properties:
  category: compute
  subcategory: swarm-manager  # ← not in other kinds
```

Other kinds only have category in labels, not in spec.

### 6. DNS — TTL and Priority Fields

**DNS-only:** Zone-level and per-record TTL override

```yaml
zones:
  - name: example.com
    ttl: 3600           # zone-level
    records:
      - name: "@"
        ttl: 300        # per-record override
        priority: 10    # MX/SRV only
```

Network, firewall, provider, resource, namespace, module have no TTL concept.

## Major Architectural Issues

### Issue 1: Module is Vastly More Complex ⚠️

**Module features:**
- 4 environment variable source types (value, var, secret, feature)
- Service naming with collision avoidance
- Cross-module dependencies (@module/service syntax)
- Mounts (volumes, bind mounts, PersistentVolumeClaims)
- Healthchecks (http/command based)
- 7 lifecycle phases
- Properties (endpoints, checks, mounts)

**Everything else** is simple declarative lists.

**V2 Question:** Is this complexity in module justified, or does it leak IaC concerns into platform layer?

### Issue 2: Workspace Mixes Concerns 🔀

**Single workspace combines:**
- Cloud providers
- VMs and resources
- Networks and firewalls
- DNS zones
- Application namespaces with modules

Large production workspaces easily hit 1500+ lines mixing infrastructure and apps.

**V2 Consideration:** Separate into deployment specs (infrastructure) vs application manifests?

### Issue 3: Terraform Features Dominate 🔧

**Terraform-specific patterns:**
- Workspace `output:` block (format, emits, files)
- DNS `output_key:` (stage outputs)
- Workspace `backend:` configuration
- ADR-0079/0080 integration binding

Ansible/Bicep mentioned but minimal configuration options. Feels Terraform-first.

**V2 Consideration:** Generalize provisioner abstractions — treat all IaC tools equally.

### Issue 4: Firewall Lacks Parametrization 🚫

Cannot parametrize:
- Port numbers
- CIDR ranges
- Rule priority/order

Forces environment duplication in firewall files.

**V2 Fix:** Add `spec.references` to firewall.

### Issue 5: Lifecycle Hierarchy Ambiguity ❓

Three levels of lifecycle:
- **Workspace:** validate → provision → configure → health → protect → destroy
- **Namespace:** bootstrap → provision → configure → health → protect → destroy
- **Module:** service_start_before → service_start_after → deploy_provision/configure/health

**Question:** Execution order when all three are active? No documented precedence.

**V2 Consideration:** Clarify or flatten lifecycle phases.

### Issue 6: No Cross-Kind Validation 🔗

Everything is late-binding via name strings:
- Resource can't declare which firewalls apply
- Network peerings isolated within network kind
- Provisioner outputs don't type-check against consumers
- Topology components just name resources without validation

No schema enforcement for references — just string matching.

**V2 Improvement:** Schema-driven cross-kind reference validation.

### Issue 7: Source Path Inconsistency 📁

**Provisioners, modules, namespaces:** Support `@repo_name/path` cross-repo references

**Infrastructure configs (provider, resource, network, dns, firewall):** Workspace-relative files only

**Effect:** Forces infrastructure into workspace repo while apps can live elsewhere.

**V2 Consideration:** Unified cross-repo reference support.

### Issue 8: References Don't Flow Down ⛓️

Module declares `references: {secrets: [db_password]}`, but workspace.variables/secrets has actual values.

**No explicit wiring:**
- Just "the name must match somewhere"
- No type checking
- No required-key validation at build time

**V2 Fix:** Schema-driven reference validation and requirement checking.

## Summary: Top 3 V2 Priorities

1. **Add `references` to firewall** — closes the most obvious gap
2. **Standardize CidrSource pattern** — flatten nested value/var/secret to DNS's model
3. **Implement cross-kind schema validation** — detect missing references, cycles, and type mismatches at build time instead of deploy time

## Quick Reference: What Each Kind Does

| Kind | Purpose | Has References | Unique Features |
|------|---------|---|---|
| Provider | Cloud credentials | ✓ | — |
| Resource | VMs/compute | ✓ | subcategory field |
| Network | VPCs/VNets | ✓ | Peerings, nested CidrSource |
| DNS | Zones & records | ✓ | output_key, TTL, priority |
| Firewall | Security rules | ✗ | — |
| Namespace | App deployments | ✓ | Lifecycle scripts |
| Module | Services & containers | ✓ | Complex (services, healthchecks, mounts, env vars) |
| Workspace | Orchestration | (implicit) | Combines infra + apps |

## Decision Outcome

Pending — this ADR captures findings only. Each discrepancy and architectural
issue above needs a decision (keep, fix, or drop for v2) before or as its
corresponding model is implemented. Track decisions either as updates to this
ADR or as new ADRs that supersede individual sections.

## Remaining Work

- Review and decide on the 6 discrepancies (firewall references, DNS `output_key`,
  DNS/Network CidrSource inconsistency, network peerings, resource `subcategory`,
  DNS TTL/priority).
- Review and decide on the 8 major architectural issues (module complexity,
  workspace scope, Terraform-first bias, firewall parametrization, lifecycle
  hierarchy, cross-kind validation, source path consistency, reference wiring).
- Prioritize and schedule the "Top 3 V2 Priorities" against the v2 model build order.
