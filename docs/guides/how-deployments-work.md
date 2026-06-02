# How Deployments Work

> A plain-language walkthrough of the strata deployment model — from a single server to a
> multi-stage production pipeline.

---

## The five things you need to know

| Thing           | What it is                                                                                                                                                                                                                                                                        |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Workspace**   | Declares *what infrastructure tools exist* and how to use them.                                                                                                                                                                                                                   |
| **Provisioner** | One entry in the workspace — a named IaC tool (Terraform, Ansible, Helm, …) with its source code location.                                                                                                                                                                        |
| **Topology**    | An optional named group in the workspace that says "these servers were created by this provisioner." When a stage uses `topology:`, strata derives the Ansible inventory from that provisioner's `stage_outputs` at runtime — so no static inventory file is needed or consulted. |
| **Deployment**  | Declares *what to do* — which environment to target, and a list of stages to run in order.                                                                                                                                                                                        |
| **Stage**       | One step in the deployment. Points to a provisioner (or a topology) and runs a deployer (Terraform, Ansible, …).                                                                                                                                                                  |

Stages run **in order**. Each stage can read the **outputs of every stage that ran before it**.

---

## Simple example — one server, one Ansible playbook

You want to:
1. Create a server with Terraform.
2. Configure it with Ansible.

### Workspace

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: workspace
meta:
  name: my_workspace
spec:
  provisioners:
    # Tool 1: Terraform — knows how to create the server
    - name: my_iac
      provisioner: terraform
      source:
        repository: my_infra
        source_path: terraform

    # Tool 2: Ansible — knows how to configure the server
    - name: my_ansible
      provisioner: ansible
      source:
        repository: my_infra
        source_path: ansible
      properties:
        playbook: site.yml
        ssh_private_key_secret: ssh_private_key

  topology:
    # "my_server" is a name for the concept "one server managed by my_iac".
    - name: my_server
      provisioner: my_iac           # which provisioner created it
```

### Deployment

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: my_deployment
spec:
  workspace:
    name: my_workspace
    file: workspace.yaml

  environments:
    - environments/env-prd.yaml

  stages:
    # Stage 1 — create the server
    - name: infrastructure
      provisioner: my_iac        # direct: "use this provisioner by name"
      secrets:
        - hetzner_api_token      # only this secret is available

    # Stage 2 — configure the server
    - name: configure
      provisioner: my_ansible    # direct: "use this provisioner by name"
      secrets:
        - ssh_private_key        # only SSH key, not the API token
      # OR — using topology (see below):
      # topology: my_server
```

### Terraform outputs.tf (simple)

This is the file inside your Terraform source that produces the output:

```hcl
# terraform/outputs.tf
output "server_ip" {
  value = hcloud_server.main.ipv4_address
}
```

That single declaration is all that's needed. After `terraform apply` strata runs
`terraform output -json`, reads `server_ip`, and stores it for the next stage.

### What happens at runtime

```
┌─────────────────────────────────────────────────────────┐
│  Stage: infrastructure   (Terraform deployer)           │
│                                                         │
│  Reads:   environment variables (HETZNER_TOKEN, etc.)   │
│  Does:    terraform init → plan → apply                 │
│  Outputs: { "server_ip": "10.0.1.42" }   ← from outputs.tf│
│           → stored in stage_outputs["server_ip"]        │
└────────────────────┬────────────────────────────────────┘
                     │  stage_outputs flows down
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Stage: configure   (Ansible deployer)                  │
│                                                         │
│  Reads:   stage_outputs["server_ip"] = "10.0.1.42"      │
│           (when using topology:, this becomes the       │
│            inline Ansible inventory automatically)      │
│  Does:    ansible-galaxy install → ansible-playbook     │
│  Outputs: (none — Ansible produces no structured output)│
└─────────────────────────────────────────────────────────┘
```

### `provisioner:` vs `topology:` on a stage

Both point the stage to the right deployer. The difference is what Ansible does with the server IP.

| `provisioner: my_ansible`                                                                    | `topology: my_server`                                                                                                                          |
| -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Direct link to the Ansible IaC entry.                                                        | Indirect: strata first looks up `my_server`, finds it uses `my_iac`, then finds the Ansible IaC entry.                                         |
| Ansible uses a **static inventory file** from the playbook directory (e.g. `inventory.yml`). | Ansible uses a **dynamic inventory**: the IP is read from `stage_outputs["server_ip"]` at runtime and passed inline. No inventory file needed. |
| Use this when the host list is known ahead of time.                                          | Use this when the servers were just created by a previous stage and their IPs are only known at runtime.                                       |

> **Why not combine them?** A static inventory file in the playbook directory is never consulted when using `topology:`. This is intentional — if a stage is skipped in a partial re-run, a stale inventory file would silently point Ansible at servers from a previous run. The split makes the host source unambiguous: either you own a static file, or strata derives the hosts from fresh Terraform output.

---

## Complex example — multi-stage production pipeline

Infrastructure, configuration, and application deployment across five stages.

### What we're building

```
[ Terraform: network ]  →  [ Terraform: servers ]  →  [ Ansible: configure ]
                                                              │
                                                              ▼
                                                    [ Helm: deploy app ]
                                                              │
                                                              ▼
                                                    [ Ansible: smoke test ]
```

### Workspace

```yaml
spec:
  provisioners:
    - name: network_iac
      provisioner: terraform
      source:
        repository: infra
        source_path: terraform/network

    - name: server_iac
      provisioner: terraform
      source:
        repository: infra
        source_path: terraform/servers

    - name: configure_ansible
      provisioner: ansible
      source:
        repository: infra
        source_path: ansible/configure
      properties:
        playbook: site.yml
        ssh_private_key_secret: ssh_private_key

    - name: app_helm
      provisioner: helm
      source:
        repository: infra
        source_path: helm

    - name: test_ansible
      provisioner: ansible
      source:
        repository: infra
        source_path: ansible/smoketest

  topology:
    # The "app_servers" topology: servers created by server_iac.
    - name: app_servers
      provisioner: server_iac
```

### Deployment

```yaml
spec:
  stages:
    - name: network
      provisioner: network_iac
      secrets:
        - cloud_api_key

    - name: servers
      provisioner: server_iac
      secrets:
        - cloud_api_key

    - name: configure
      topology: app_servers       # ← no static inventory; IPs come from stage 'servers'
      secrets:
        - ssh_private_key

    - name: deploy
      provisioner: app_helm
      # no secrets needed — Helm values come from STRATA_CONTEXT

    - name: smoketest
      topology: app_servers       # ← same topology, same dynamic inventory
      secrets:
        - ssh_private_key
```

### Terraform outputs.tf (complex)

```hcl
# terraform/network/outputs.tf
output "vpc_id" {
  value = aws_vpc.main.id
}
output "subnet_id" {
  value = aws_subnet.app.id
}
```

```
# terraform/servers/outputs.tf
output "server_ips" {
  value = [for s in aws_instance.app : s.private_ip]  # list of IPs
}
```

The `servers` stage produces a **list** — strata joins it with commas into the
Ansible inline inventory format: `"10.0.1.10,10.0.1.11,10.0.1.12,"`.

### Data flow through all five stages

```
┌──────────────────────────────────────────────────────────────┐
│  Stage 1: network   (Terraform)                              │
│                                                              │
│  Reads:   env vars — cloud credentials, region              │
│  Does:    creates VPC, subnets, firewall rules               │
│  Outputs: { "vpc_id": "vpc-abc123",       ← from outputs.tf  │
│             "subnet_id": "sub-def456" }                      │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 2: servers   (Terraform)                              │
│                                                              │
│  Inputs:  env vars + stage_outputs from stage 1:            │
│           TF_VAR_vpc_id    = "vpc-abc123"                    │
│           TF_VAR_subnet_id = "sub-def456"                    │
│  Does:    creates 3 VMs inside the VPC                       │
│  Outputs: { "server_ips": ["10.0.1.10","10.0.1.11",         │
│                             "10.0.1.12"] }                   │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 3: configure   (Ansible, via topology: app_servers)   │
│                                                              │
│  Inputs:  stage_outputs["server_ips"]                        │
│           → _dynamic_inventory = "10.0.1.10,10.0.1.11,      │
│                                   10.0.1.12,"                │
│  Does:    runs site.yml against all three servers            │
│  Outputs: (none)                                             │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 4: deploy   (Helm)                                    │
│                                                              │
│  Inputs:  env vars + all previous stage_outputs             │
│           (Helm values can reference server IPs, VPC ID, …) │
│  Does:    helm upgrade --install my-app ./chart             │
│  Outputs: (none by default)                                  │
└────────────────────┬─────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 5: smoketest   (Ansible, via topology: app_servers)   │
│                                                              │
│  Inputs:  same dynamic inventory as stage 3                  │
│           (built again from stage_outputs["server_ips"])     │
│  Does:    runs smoketest.yml — hits each server's /health    │
│  Outputs: (none)                                             │
└──────────────────────────────────────────────────────────────┘
```

---

## How outputs travel between stages

Terraform outputs are collected after every `apply` and split into two buckets:

| Bucket        | Key in code                                  | Injected into subprocesses?                                                    | Example                         |
| ------------- | -------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------- |
| Non-sensitive | `stage_outputs` (STRATA_CONTEXT)             | Yes — as `TF_VAR_<key>` for Terraform stages, bare env var for Ansible/Compose | `server_ip`, `vpc_id`           |
| Sensitive     | `stage_outputs_sensitive` (STRATA_SENSITIVE) | Only if the stage declares the key in `secrets:`                               | passwords, tokens, private keys |

Every stage that runs after a Terraform stage automatically has its non-sensitive outputs available (via STRATA_CONTEXT). Sensitive outputs require the downstream stage to declare the key in its `secrets:` allowlist.

### STRATA_CONTEXT seed

Before the first stage runs, STRATA_CONTEXT is seeded with the resolved **environment variables** and **feature flags**. After each stage, its non-sensitive outputs are added. Every subsequent stage receives the full accumulated context.

### STRATA_SENSITIVE seed and scoping

Before the first stage runs, STRATA_SENSITIVE is seeded with resolved **secrets**. Access is default-deny: a stage only receives secrets it declares in `secrets:`.

```yaml
stages:
  - name: provision
    provisioner: my_tf
    secrets:
      - hetzner_api_token    # only this secret visible
  - name: configure
    provisioner: my_ansible
    secrets:
      - ssh_private_key      # only SSH key visible, not the API token
  - name: verify
    provisioner: script_check
    # no secrets → no sensitive values at all
```

---

## Topology — when to use it

Use `topology:` on a stage (instead of `provisioner:`) when **both** of these are true:

1. The stage needs to talk to servers that were created by a *previous* stage.
2. The IPs of those servers are only known at runtime (Terraform output).

The topology definition in the workspace is the bridge: it declares which provisioner created the servers *and* which Terraform output key holds their IPs. The Ansible deployer reads that key from `stage_outputs` and builds the inventory string automatically.

If the servers are static and known ahead of time (fixed IPs in a repo inventory file), use `provisioner:` directly and keep the inventory file in the playbook directory.
