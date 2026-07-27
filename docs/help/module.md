# Module

Reusable infrastructure module packaged for sharing across workspaces.

A module (`kind: module`) encapsulates a reusable piece of infrastructure:
- **Terraform module** — a `.tf` folder with provisioning code
- **Ansible role** — a reusable configuration/deployment playbook
- **Helm chart** — a Kubernetes application template
- **Metadata** — version, dependencies, required variables, outputs

Modules live in git repos (often separate from the main workspace repo) and
are referenced by deployments and workspaces using `@repo_name/path` notation.

---

## Basic Example

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: vpc-core
  version: 1.0.0
spec:
  description: AWS VPC with subnets, NAT gateways, and route tables
  source:
    type: terraform
    path: terraform/
  inputs:
    - name: cidr_block
      type: string
      required: true
    - name: availability_zones
      type: list
      default: [a, b, c]
  outputs:
    - name: vpc_id
    - name: subnet_ids
```

---

## Using Modules

Modules are used inside workspaces or deployments:

```yaml
kind: workspace
spec:
  modules:
    - ref: @network/vpc-core.yaml
      inputs:
        cidr_block: 10.0.0.0/16
```

---

## Cross-Repository References

Modules typically live in a separate repo:

```bash
strata repo add network https://github.com/myorg/network-modules.git
```

Then reference them:

```yaml
spec:
  modules:
    - ref: @network/modules/vpc-core.yaml
```

---

## See Also

- `cross-repo` — how @repo references work
- `workspace` — infrastructure blueprint that uses modules
