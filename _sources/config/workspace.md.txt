# Workspace Configuration

Top-level configuration files defining complete infrastructure deployments. YAML files orchestrate providers, resources, topology, namespaces, and lifecycle management for platform environments.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: workspace
meta:
  name: <workspace_name> # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
spec:
  lifecycle: {} # Optional: workflow phases
  properties: # Tool-specific configs
    terraform:
      organization: <org>
      cloudspace: <workspace>
  custom: {} # Custom metadata
  providers: [] # Cloud providers
  provisioners: [] # IaC tools
  topology: [] # Infrastructure clusters
  namespaces: [] # Application deployments
  variables: [] # Non-sensitive data
  secrets: [] # Sensitive credentials
```

## Lifecycle Phases

| Phase                | Description                  | When Executed        |
| -------------------- | ---------------------------- | -------------------- |
| `clean`              | Clean build artifacts        | Before operations    |
| `check` / `validate` | Validate configuration       | Before planning      |
| `build`              | Build artifacts              | Before provisioning  |
| `plan`               | Preview changes (dry-run)    | Before applying      |
| `provision`          | Create/update infrastructure | During deployment    |
| `initialize`         | Initialize resources         | After provisioning   |
| `configure`          | Configure infrastructure     | After initialization |
| `output`             | Export resource metadata     | After configuration  |
| `health`             | Health checks                | After deployment     |
| `protect`            | Backup operations            | Before destruction   |
| `destroy`            | Teardown infrastructure      | During teardown      |

**Lifecycle structure:**

```yaml
lifecycle:
  <phase_name>:
    scripts:
      - file: <script_path>
        description: <description>
```

## Providers

```yaml
providers:
  - name: <provider_name> # Unique name
    file: <provider_path> # Path to provider config
```

## Provisioners

```yaml
provisioners:
  - name: <provisioner_name>
    provisioner: terraform | ansible   # IaC tool
    source:
      repository: <repository_name>    # optional — omit for single-repo workspaces
      source_path: <path>              # path within the repo (or workspace root when repository is absent)
      target_path: <path>              # optional: path in the build output
    backend:                           # Terraform only — state storage
      type: terraform_cloud | azurerm | s3 | ...
      configuration: {}
    configuration:                     # tool-specific options (see below)
      playbook: site.yml               # Ansible: main playbook (default: site.yml)
      inventory: inventory/hosts.yml   # Ansible: inventory file (auto-discovered if omitted)
      ssh_private_key_secret: <name>   # Ansible: secret key name in resolved_values.secrets
      extra_vars:                      # Ansible: extra -e variables
        key: value
    output:                            # Terraform only — controls build output files
      format: strata | custom | script | none   # default: strata
      emits: []                        # categories to emit (omit = format defaults)
      files: []                        # custom file definitions (see Build Output Profile)
```

### Build Output Profile (Terraform only)

The `output` block on a Terraform provisioner controls what `.auto.tfvars.json` files
`strata build run` produces. When absent, `format: strata` is assumed (current behaviour).

**`output.format` modes:**

| Mode     | Behaviour                                                                                            |
| -------- | ---------------------------------------------------------------------------------------------------- |
| `strata` | Default. Emit all built-in files (`workspace.auto.tfvars.json`, `providers.auto.tfvars.json`, etc.). |
| `custom` | Emit only what `emits[]` and `files[]` specify.                                                      |
| `script` | One user-provided Python/shell script owns all output files.                                         |
| `none`   | Copy source files only — no tfvars output generated.                                                 |

**`output.emits[]` categories:**

| Category     | File produced                                          | Tier           |
| ------------ | ------------------------------------------------------ | -------------- |
| `features`   | `flags.auto.tfvars.json` — flat `{key: bool}` map      | Build + Deploy |
| `variables`  | `variables.auto.tfvars.json` — flat `{key: value}` map | Build + Deploy |
| `properties` | `properties.auto.tfvars.json` — all merged properties  | Build          |
| `custom`     | `custom.auto.tfvars.json` — all merged custom fields   | Build          |
| `workspace`  | `workspace.auto.tfvars.json`                           | Build          |
| `providers`  | `providers.auto.tfvars.json`                           | Build          |
| `topologies` | `topologies.auto.tfvars.json`                          | Build          |
| `modules`    | `modules.auto.tfvars.json`                             | Build          |
| `namespaces` | `namespaces.auto.tfvars.json`                          | Build          |
| `firewalls`  | `firewalls.auto.tfvars.json`                           | Build          |
| `dns`        | `dns.auto.tfvars.json`                                 | Build          |
| `networks`   | `networks.auto.tfvars.json`                            | Build          |
| `resources`  | `resx_<type>.auto.tfvars.json`                         | Build          |
| `tenant`     | `tenant.auto.tfvars.json`                              | Build          |

`features` and `variables` are two-tier: `constant`/`environment` store entries are
written at build time; integration-backed entries (Flagsmith, Azure App Config, Vault)
are written by the deployer immediately before `terraform init`. Secrets are **never**
written to any file — they are always injected as `TF_VAR_*` environment variables.

**`output.files[]` — custom file definitions:**

Each entry produces one `.auto.tfvars.json` file. Two modes, mutually exclusive:

*Source mode* — pass through a key from the merged properties/custom dict:

```yaml
files:
  - name: aks_config.auto.tfvars.json
    variable: aks_config        # wraps output as { "aks_config": { ... } }
    type: object                # object (default) | map | list | flat
    source: properties          # properties | custom
    key: aks_config             # key to extract from the merged dict

  # Multiple variables in one file
  - name: platform.auto.tfvars.json
    sources:
      - variable: aks_config
        source: properties
        key: aks_config
      - variable: dns_config
        source: properties
        key: dns_config
```

*Script mode* — run a Python/shell script that reads `platform.json` and writes the file:

```yaml
files:
  - name: environment_info.auto.tfvars.json
    type: script
    script: "@my_repo/scripts/build_env_info.py"   # supports @repo_name/path
```

The script receives these environment variables: `STRATA_PLATFORM_PATH`,
`STRATA_BUILD_PATH`, `STRATA_OUTPUT_PATH`, `STRATA_OUTPUT_FILE`,
`STRATA_WORKSPACE_PATH`, `STRATA_DRY_RUN`.

**`format: script`** — one script produces all output files:

```yaml
output:
  format: script
  script: scripts/build_tfvars.py   # script receives STRATA_PROVISIONER too
```

**Single-repo form** — when IaC lives inside the workspace itself, omit `repository` and
use `source_path` alone. strata resolves the path relative to the workspace root:

```yaml
provisioners:
  - name: platform_iac
    provisioner: terraform
    source:
      source_path: terraform   # resolved as <workspace_root>/terraform
```

**Multi-repo form** — when IaC lives in a separate registered repository:

```yaml
provisioners:
  - name: platform_iac
    provisioner: terraform
    source:
      repository: platform-iac   # must match a repo registered via `strata repo add`
      source_path: deploy/terraform
```

### Provisioner types

| Type        | Tool             | Deployer            | Stage types that activate it         |
| ----------- | ---------------- | ------------------- | ------------------------------------ |
| `terraform` | Terraform CLI    | `TerraformDeployer` | `infrastructure`, `terraform`        |
| `ansible`   | ansible-playbook | `AnsibleDeployer`   | `configure`, `initialize`, `ansible` |

## Topology

Defines infrastructure clusters and components:

```yaml
topology:
  - name: <topology_name>
    provider: <provider_name>   # Must reference defined provider
    provisioner: terraform
    type: <topology_type>       # docker-swarm, kubernetes, etc.
    volumes: []                 # Optional: shared volumes
      - name: <volume_name>
        type: <volume_type>     # local, replicated, distributed
    components: []              # VMs, nodes
      - name: <component_name>  # Unique across workspace
        role: <role>            # manager, worker, control-plane, etc.
        resource:
          file: <resource_path>
          count: <count>        # 1-100
        module:                 # Optional
          name: <module_name>
          file: <module_path>
        labels: {}
        tags: []
```

**Component roles by topology:**

- **docker-swarm:** `manager` (1, 3, 5, 7 - odd for quorum), `worker` (1+)
- **kubernetes:** `control-plane`/`master` (1, 3, 5 - odd), `worker` (1+)
- **custom:** Any role name

## Namespaces

```yaml
namespaces:
  - name: <namespace_name>
    file: <namespace_path>
```

## Variables & Secrets

**Variables** (non-sensitive):

```yaml
variables:
  - key: WORKSPACE # UPPER_SNAKE_CASE
    source: constant # constant, env, file, computed
    value: platform
```

**Secrets** (sensitive):

```yaml
secrets:
  - key: TERRAFORM_API_TOKEN
    source: bitwarden # bitwarden, vault, env, file
    value: <secret_id> # ID or reference
```

## Examples

**Minimal:**

```yaml
meta:
  name: minimal
spec:
  providers:
    - name: local
      file: config/providers/local.yaml
  provisioners:
    - name: infra
      provisioner: terraform
      source:
        type: local
        repository: /
        reference: /
        source_path: deploy/terraform
        deploy_path: terraform
  topology:
    - name: cluster
      provider: local
      provisioner: terraform
      type: docker-swarm
      components:
        - name: node
          role: manager
          resource:
            file: config/resources/vm-basic.yaml
            count: 1
```

**Single Topology (Terraform only):**

```yaml
meta:
  name: platform
  labels:
    version: "1.0.0"
spec:
  properties:
    terraform:
      organization: myorg
      cloudspace: platform-workspace
  providers:
    - name: kamatera_europe
      file: config/providers/kamatera-eu-fr.yaml
  provisioners:
    - name: platform_infrastructure
      provisioner: terraform
      source:
        type: local
        repository: /
        reference: /
        source_path: deploy/terraform
        deploy_path: terraform
  topology:
    - name: platform_swarm
      provider: kamatera_europe
      provisioner: terraform
      type: docker-swarm
      volumes:
        - name: config
          type: replicated
        - name: data
          type: local
      components:
        - name: manager
          role: manager
          resource:
            file: config/resources/vm-manager.yaml
            count: 1
        - name: worker
          role: worker
          resource:
            file: config/resources/vm-worker.yaml
            count: 3
  namespaces:
    - name: infra
      file: config/namespaces/platform-infra.yaml
  variables:
    - key: WORKSPACE
      source: constant
      value: platform
  secrets:
    - key: TERRAFORM_API_TOKEN
      source: bitwarden
      value: d47e736b-2db8-47d5-b46b-b2c8016ece73
```

**Terraform + Ansible (infrastructure then configuration):**

```yaml
meta:
  name: haven
  labels:
    version: "1.0.0"
spec:
  providers:
    - name: kamatera_europe
      file: config/providers/kamatera-eu-fr.yaml
  provisioners:
    - name: haven_infrastructure
      provisioner: terraform
      source:
        repository: haven
        source_path: terraform
      backend:
        type: terraform_cloud
        configuration:
          organization: myorg
          workspace: haven-prd
    - name: haven_init
      provisioner: ansible
      source:
        repository: haven
        source_path: ansible
      configuration:
        playbook: site.yml
        inventory: inventory/hosts.yml
        ssh_private_key_secret: haven_ssh_key   # resolved from the secret store at deploy time
        extra_vars:
          env: production
  topology:
    - name: haven_swarm
      provider: kamatera_europe
      provisioner: terraform
      type: docker-swarm
      components:
        - name: manager
          role: manager
          resource:
            file: config/resources/vm-manager.yaml
            count: 1
        - name: worker
          role: worker
          resource:
            file: config/resources/vm-worker.yaml
            count: 3
  secrets:
    - key: haven_ssh_key
      source: bitwarden
      value: b2f90a12-3c4d-5678-abcd-ef1234567890   # Bitwarden item ID holding the PEM key
    - key: TERRAFORM_API_TOKEN
      source: bitwarden
      value: d47e736b-2db8-47d5-b46b-b2c8016ece73
```

```yaml
meta:
  name: multi_region
  labels:
    version: "2.0.0"
spec:
  properties:
    terraform:
      organization: myorg
      cloudspace: multi-region
  custom:
    owner: "DevOps Team"
    project: "Global Platform"
  providers:
    - name: azure_westeurope
      file: config/providers/azure-westeurope.yaml
    - name: azure_eastus
      file: config/providers/azure-eastus.yaml
  provisioners:
    - name: infra
      provisioner: terraform
      source:
        type: local
        repository: /
        reference: /
        source_path: deploy/terraform
        deploy_path: terraform
  topology:
    - name: europe_cluster
      provider: azure_westeurope
      provisioner: terraform
      type: kubernetes
      components:
        - name: eu_control_plane
          role: control-plane
          resource:
            file: config/resources/vm-k8s-master.yaml
            count: 3
        - name: eu_worker
          role: worker
          resource:
            file: config/resources/vm-k8s-worker.yaml
            count: 5
    - name: us_cluster
      provider: azure_eastus
      provisioner: terraform
      type: kubernetes
      components:
        - name: us_control_plane
          role: control-plane
          resource:
            file: config/resources/vm-k8s-master.yaml
            count: 3
        - name: us_worker
          role: worker
          resource:
            file: config/resources/vm-k8s-worker.yaml
            count: 5
  namespaces:
    - name: infrastructure
      file: config/namespaces/infra.yaml
    - name: applications
      file: config/namespaces/apps.yaml
```

**Custom build output — wrapping an existing Terraform repo:**

```yaml
meta:
  name: aks_core
spec:
  properties:
    git_repo_name: iac_aks_core
    kubernetes_version: "1.31"
  providers:
    - name: azure_westeurope
      file: "@iac_aks_core/stack/provider.yaml"
  provisioners:
    - name: terraform
      provisioner: terraform
      source:
        repository: iac_aks_core
        source_path: .
      backend:
        type: azurerm
        configuration:
          resource_group_name: ${var:TF_RG}
          storage_account_name: ${var:TF_SA}
          container_name: tfstate
          key: ${var:TF_STATE_KEY}
      output:
        format: custom
        emits:
          - features        # → flags.auto.tfvars.json  (enable_* booleans)
          - variables       # → variables.auto.tfvars.json
        files:
          # Script produces environment_info.auto.tfvars.json from platform.json
          - name: environment_info.auto.tfvars.json
            type: script
            script: "@iac_aks_core/scripts/build_env_info.py"
          # Pass through aks_config/dns_config from environment properties
          - name: aks.auto.tfvars.json
            variable: aks_config
            type: object
            source: properties
            key: aks_config
          - name: dns.auto.tfvars.json
            variable: dns_config
            type: object
            source: properties
            key: dns_config
  topology:
    - name: aks_platform
      provider: azure_westeurope
      provisioner: terraform
      type: kubernetes
      components:
        - resource: aks_cluster
  resources:
    - name: aks_cluster
      file: "@iac_aks_core/stack/aks.yaml"
```

**Full-control script output:**

```yaml
provisioners:
  - name: terraform
    provisioner: terraform
    source:
      repository: my_tf_repo
      source_path: .
    output:
      format: script
      script: scripts/build_tfvars.py   # writes all .auto.tfvars.json files itself
```

## Execution Flow

1. Load configuration → Parse and validate workspace YAML
2. Register providers → Load provider configurations
3. Lifecycle: clean → Clean build artifacts
4. Lifecycle: check → Validate configuration
5. Lifecycle: build → Build deployment artifacts
6. Lifecycle: plan → Preview infrastructure changes
7. Provision infrastructure → Execute provisioners for each topology
8. Lifecycle: provision → Post-provisioning hooks
9. Lifecycle: initialize → Initialize resources
10. Lifecycle: configure → Configure infrastructure
11. Deploy namespaces → Deploy application namespaces
12. Lifecycle: output → Export resource metadata
13. Lifecycle: health → Verify deployment health

## Best Practices

- **Naming:** Descriptive names (`platform`, `production`, `development`)
- **Versions:** Track versions through labels
- **Provider organization:** Group by region or purpose
- **Component names:** Unique across entire workspace
- **Secret management:** Always use secret managers (Bitwarden, Vault)
- **Variable naming:** UPPER_SNAKE_CASE for keys
- **Idempotent scripts:** Lifecycle scripts safe to run multiple times
- **Manager/control-plane counts:** Odd numbers (1, 3, 5, 7) for quorum
- **Documentation:** Clear annotations for purpose

## Multi-Topology

When using multiple topologies:

- **Shared backend:** All topologies share Terraform backend (atomic operations)
- **Execution order:** Processes in defined order
- **Component names:** Must be unique across entire workspace
- **Provider references:** Each topology can use different providers

## Validation

Platform validates:

- Valid workspace name (lowercase, alphanumeric, underscores)
- Required fields (providers, provisioners, topology)
- Unique names (providers, provisioners, topologies, components, namespaces, variables, secrets)
- Provider/provisioner references exist
- Component counts within limits (1-100)
- File references exist

## Troubleshooting

**Validation failed:** Check required fields, verify file references, ensure unique names, validate naming patterns  
**Provider not found:** Verify provider name matches definition, check file path, ensure valid configuration  
**Component conflicts:** Check for duplicate names, verify uniqueness across workspace  
**Topology validation failed:** Use odd numbers for manager/control-plane, verify provider/provisioner references  
**Variable/Secret conflicts:** Ensure unique keys, verify valid sources  
**Lifecycle script failed:** Check executable permissions, verify path, review logs, ensure dependencies installed

## Advanced

**Cost tracking:** Use custom properties (`owner`, `costcenter`, `project`, `budget_monthly`)  
**Environment separation:** Separate workspace files (`production.yaml`, `staging.yaml`, `development.yaml`)  
**Multi-region:** Multiple topologies with different providers  
**Disaster recovery:** Protection lifecycle phase with backup scripts