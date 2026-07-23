# Configuration

Platform-wide **validation schemas and defaults** for providers, resources, and topologies.

## Purpose

Define **validation rules** for providers/resources/topologies, establish **platform defaults**, support **multiple layered configs** with merge order (built-in → custom 1...N, later overrides).

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: <name> # ^[a-z][a-z0-9_]*$
spec:
  configuration: {} # Platform defaults
  properties: {} # Custom properties
  providers: [] # Provider definitions (regions, resources)
  topologies: [] # Topology schemas (components, rules)
```

## Providers

Define allowed regions and resource validation patterns:

```yaml
providers:
  - name: <provider>
    additional_regions: false # Restrict to listed regions
    regions: [eu-fr, us-ny]
    additional_resources: false # Restrict to defined resources
    resources:
      - name: virtualmachine
        category: compute
        configuration: # Regex validation
          cpu_cores: "^[1-9][0-9]?$" # 1-99
          ram_mb: "^(512|1024|2048|4096)$"
```

## Topologies

Define cluster component rules:

```yaml
topologies:
  - type: docker-swarm
    components:
      - role: manager
        is_control: true
        min_count: 1
        max_count: 7
      - role: worker
        min_count: 1
        max_count: 0 # unlimited
```

## Layering — Artifact Path Hierarchies

Define how deployment artifacts are organized into a hierarchical path structure. Use layering when different deployment files need to be placed into different directories during the build process.

### Layering Schemes

Two formats are supported:

**Single-scheme layering** (`spec.layering` — deprecated, for backward compatibility):

```yaml
spec:
  layering:
    - name: zone
      required: true
    - name: customer
      required: true
    - name: environment
      required: false
      default: dev
```

This creates a single layer scheme applied to all deployments. The artifact path is constructed by combining values from each layer in order, separated by `/`. For example, `zone: europe`, `customer: contoso`, `environment: prd` produces artifact path `europe/contoso/prd`.

**Multi-scheme layering** (`spec.layerings` — recommended):

```yaml
spec:
  layerings:
    - name: zone-tenant
      scope: zones/**
      layers:
        - name: zone
          required: true
        - name: customer
          required: true
        - name: environment
          required: false
          default: dev

    - name: landscape-ring
      scope: landscape/**
      layers:
        - name: landscape
          required: true
        - name: ring
          required: true
```

This declares multiple layer schemes. Each scheme has:
- **`name`**: Identifier for the scheme
- **`scope`**: Glob pattern matching deployment file paths (e.g., `zones/**`, `landscape/**`, `**`)
- **`layers`**: Ordered list of layer definitions

During deployment validation, the deployment file's path is matched against schemes in order. The first matching scope wins, and that scheme's layers are used to resolve the artifact path.

### Layer Definition

Each layer entry has:

| Field      | Type   | Default | Description                                                       |
| ---------- | ------ | ------- | ----------------------------------------------------------------- |
| `name`     | `str`  | —       | Layer name (e.g., `zone`, `customer`, `ring`, `landscape`)        |
| `required` | `bool` | `true`  | Whether the deployment must provide a value for this layer        |
| `default`  | `str`  | `null`  | Default value if no value is provided (only if `required: false`) |

**Important:** Layer names are arbitrary and must be unique within a scheme. The layer named `environment` no longer has special meaning — any valid layer can be the last layer in the hierarchy.

### Artifact Path Resolution

When a deployment references a configuration file with layering:

1. **Scope matching**: The deployment file's relative path (from workspace root) is matched against all scheme scopes in order using glob pattern matching
2. **First match wins**: The first scheme whose scope matches the file path is selected
3. **Value resolution**: Layer values are extracted from the deployment's `properties` field or use defaults
4. **Path construction**: The artifact path is built as `layer1_value/layer2_value/.../layerN_value`

**Example matching:**

```
Deployment files:
├── zones/europe/contoso/prd/deploy.yaml    ← matches scope: zones/**
├── landscape/platform/ring2/deploy.yaml    ← matches scope: landscape/**
└── shared/base.yaml                        ← no match; layering not applied
```

### Migration from `layering` to `layerings`

If you're using the deprecated single-scheme `layering` format, migrate as follows:

**Before:**

```yaml
spec:
  layering:
    - name: zone
      required: true
    - name: customer
      required: true
    - name: environment
      default: dev
```

**After:**

```yaml
spec:
  layerings:
    - name: default
      scope: "**"  # Matches all deployment files
      layers:
        - name: zone
          required: true
        - name: customer
          required: true
        - name: environment
          default: dev
```

Wrap your existing `layering` list in a single `ScopedLayeringModel` with `scope: "**"` (catch-all). The `"**"` scope matches all files, so all deployments use that scheme — equivalent to the old single-scheme behavior.

### Mutual Exclusion

A configuration cannot have both `spec.layering` and `spec.layerings` defined. The system enforces this with a validation error:

```yaml
# ✗ INVALID — both formats present
spec:
  layering: [...]
  layerings: [...]
```

Choose one format and remove the other.

## Example

```yaml
meta:
  name: cloud_validation
spec:
  providers:
    - name: kamatera
      additional_regions: false
      regions: [eu-fr, us-ny]
      resources:
        - name: virtualmachine
          category: compute
          configuration:
            cpu_cores: "^[1-9][0-9]?$" # 1-99
            ram_mb: "^(512|1024|2048|4096)$"
  topologies:
    - type: docker-swarm
      components:
        - role: manager
          is_control: true
          min_count: 1
          max_count: 7
        - role: worker
          min_count: 1
  layerings:
    # Multi-tenant deployments with region isolation
    - name: regional-tenant
      scope: zones/**
      layers:
        - name: zone
          required: true
        - name: customer
          required: true
        - name: environment
          required: false
          default: dev

    # Ring-based deployments (canary → production)
    - name: ring-promotion
      scope: landscape/**
      layers:
        - name: landscape
          required: true
        - name: ring
          required: true
```

## Configuration Schema Fields

The `configuration` dict on a resource (and `properties` at the spec level) maps field names to validation rules. Each entry is either a **shorthand regex string** or a **structured `ConfigurationSchemaField`**:

```yaml
resources:
  - name: virtualmachine
    configuration:
      # Shorthand: just the pattern string (required=true, no description)
      cpu_cores: "^[1-9][0-9]?$"

      # Structured: pattern + optional flags
      enable_backup:
        pattern: "^(true|false)$"
        required: false
        description: "Whether automated backups are enabled"
```

| Field         | Type   | Default | Description                                          |
| ------------- | ------ | ------- | ---------------------------------------------------- |
| `pattern`     | `str`  | —       | Regex the field value must fully match               |
| `required`    | `bool` | `true`  | Whether the field must be present in resource config |
| `description` | `str`  | `null`  | Human-readable description of the field              |

> **Boolean fields must use a pattern — there is no native `type: boolean` in the schema.**
> The configuration schema is regex-only; all values are validated as strings.
> Use `"^(true|false)$"` as the pattern and pass `"true"` or `"false"` as the value.
>
> ```yaml
> # Schema (in configuration YAML)
> enable_backup:
>   pattern: "^(true|false)$"
>   required: false
>
> # Usage (in deployment/resource YAML)
> configuration:
>   enable_backup: "true"   # string, not a YAML boolean
> ```

---

## Merge Behavior

Multiple configs merge: built-in → 00-_.yaml → 10-_.yaml → 99-\*.yaml  
**Properties:** Last wins (override)  
**Providers/Topologies:** Additive (extend list)

## Validation

- Valid regex patterns in resource configuration
- min_count ≤ max_count for topology components
- Unique provider/topology names after merge
- Defined regions/resources when additional\_\* = false

## Path Convention Policy

Declare directory structure conventions in `spec.paths`. A `path_convention` policy type
enforces these at validation time.

### Declaring conventions

```yaml
spec:
  paths:
    - name: zone-deployment-tree
      scope: "zones/**"                              # files in this subtree are candidates
      pattern: "zones/{zone}/customers/{tenant}/{env}" # {segment} captures one path part
      validate:
        zone: spec.zones[*].name                     # captured value must be a declared zone
        tenant: "customers/{tenant}/tenant.yaml"     # file at this path must exist
        env: spec.environments[*].name               # captured value must be a declared environment

    - name: tenant-registry
      scope: "customers/**"
      pattern: "customers/{tenant}"
      validate:
        tenant: "customers/{tenant}/tenant.yaml"

    - name: landscape-registry
      scope: "landscape/**"
      pattern: "landscape/{landscape}"
      validate:
        landscape: "landscape/{landscape}/landscape.yaml"
```

### Enforcement policy

```yaml
spec:
  policies:
    # Enforce all conventions
    - name: enforce-paths
      type: path_convention
      phase: validate
      enforcement: deny

    # Enforce specific conventions only
    - name: advisory-landscape
      type: path_convention
      phase: validate
      enforcement: warn
      configuration:
        conventions: [landscape-registry]
```

### Inline convention (deploy-repo mode)

For repositories without a configuration model, declare the convention inline on the policy:

```yaml
policies:
  - name: deploy-layout
    type: path_convention
    phase: validate
    enforcement: deny
    configuration:
      scope: "deploy/**"
      pattern: "deploy/{landscape}/{ring}"
      validate:
        landscape: "deploy/{landscape}/landscape.yaml"
```

### Validation rule types

| Rule syntax                      | Meaning                                                                |
| -------------------------------- | ---------------------------------------------------------------------- |
| `spec.zones[*].name`             | Captured value must appear in a field of the loaded ConfigurationModel |
| `customers/{tenant}/tenant.yaml` | File at this path (relative to workspace root) must exist on disk      |

`spec.*` rules require `--deep` validation (configuration service must be available). File
existence rules work in both surface and deep mode.

Files that match the scope but not the pattern (e.g., shallower depth) are skipped — not a
violation. Files that match no scope are not checked by that convention.

## Checkov IaC Security Policy

Run [Checkov](https://www.checkov.io) against Terraform build artifacts during the `build` phase.
Requires Checkov to be installed (`pip install checkov`). Gracefully skipped when not available.

### Declaring the integration

```yaml
spec:
  integrations:
    - name: checkov
      type: checkov
      capabilities: [iac_security]
      required: false
      validation:
        command: checkov --version
        min_version: "2.0.0"
```

### Enabling the policy

```yaml
spec:
  policies:
    - name: terraform_security_baseline
      type: checkov
      phase: build
      enforcement: deny
      description: "Block builds with HIGH or CRITICAL Checkov findings"
      configuration:
        framework: terraform          # default: terraform
        severity_gate: high           # critical|high|medium|low (default: high)
        skip_checks:                  # CKV IDs to suppress (false positives, accepted risks)
          - CKV_AWS_1
          - CKV_AWS_20
        include_checks: []            # if set, run ONLY these checks (empty = run all)
        custom_checks_dir: ".strata/checkov/custom/"  # optional: custom rule directory
        timeout: 120                  # subprocess timeout in seconds (default: 120)
```

### How it works

1. After Terraform artifacts are generated by `strata build run`, the policy engine finds the
   `terraform/` subdirectory under `build_path` (falling back to `build_path` itself if no
   subdirectory exists).
2. Invokes: `checkov --directory <terraform_dir> --framework terraform --output json --compact`
3. Parses Checkov JSON output into `CheckovFinding` records with severity, resource, and file path.
4. Applies `severity_gate` — findings at or above the gate level become policy violations.

### Severity gate

| `severity_gate`  | Fails on                    |
| ---------------- | --------------------------- |
| `critical`       | CRITICAL only               |
| `high` (default) | HIGH, CRITICAL              |
| `medium`         | MEDIUM, HIGH, CRITICAL      |
| `low`            | LOW, MEDIUM, HIGH, CRITICAL |

### Graceful degradation

- Checkov not installed → policy skips (passes), warning logged
- No `.tf` files found in build path → policy skips
- Checkov subprocess fails → policy skips (non-fatal, never blocks build)

## Secret Stores

Secrets in `spec.secrets` are resolved at build time by the `strata build` command. The `store` field controls which backend is used. The following stores are supported:

| `store` value    | Resolver type | Integration required? | Notes                                      |
| ---------------- | ------------- | --------------------- | ------------------------------------------ |
| `constant`       | Built-in      | No                    | Literal value — avoid for real secrets     |
| `environment`    | Built-in      | No                    | Reads a named env var from the local shell |
| `github`         | Built-in      | No                    | Reads a GitHub Actions injected env var    |
| `azure-keyvault` | Integration   | Yes                   | Azure Key Vault secret                     |
| `bitwarden`      | Integration   | Yes                   | Bitwarden Secrets Manager item             |
| `vault`          | Integration   | Yes                   | HashiCorp Vault / OpenBao secret           |
| `infisical`      | Integration   | Yes                   | Infisical secret                           |

### `github` — GitHub Actions secrets

GitHub Actions secrets are injected into the runner's environment as plain environment variables before each job step executes. The `github` store type reads from those environment variables.

```yaml
spec:
  secrets:
    - key: db_password
      store: github
      value: DB_PASSWORD          # GitHub secret name (env var injected by Actions)
      description: "Database password from GitHub Secrets"
```

**How it works:** The `value` field is the environment variable name. GitHub Actions maps your repository secret `DB_PASSWORD` to the env var `DB_PASSWORD` when you reference it in the workflow's `env:` block. The resolver calls `os.environ.get("DB_PASSWORD")` at build time.

**Uppercase normalization:** GitHub uppercases all secret names at storage time. The resolver automatically uppercases `value` before the lookup — so `value: db_password` and `value: DB_PASSWORD` are equivalent.

**Local development:** Running `strata build` locally with `store: github` secrets emits a warning because `GITHUB_ACTIONS` is not set. Set the env vars manually for local testing:

```powershell
$env:DB_PASSWORD = "local-test-value"
```

**`version` field:** Not supported for `store: github`. GitHub Secrets are not versioned. Specifying `version` raises a validation error.

**Production policy:** If your configuration defines `security.allowed_secret_stores`, add `"github"` explicitly:

```yaml
spec:
  security:
    allowed_secret_stores:
      - github
      - azure-keyvault
```

---

## Deployment Artifacts

The `spec.deployment` section controls where deployment artifacts are persisted.

### Manifest Storage

Controls where the deployment manifest (full audit record) is written after every deploy run.
When absent, manifests are not written.

```yaml
spec:
  deployment:
    manifest:
      type: local                     # local | gitops
      path: ".strata/deployments"     # base path; appended: /{deployment}/{version}/{timestamp}.json
```

For GitOps storage:

```yaml
spec:
  deployment:
    manifest:
      type: gitops
      repository: state-repo          # must match a name in spec.remotes
      branch: manifests
      path: deployments
      tag: true                       # create git tag {deployment}/{version} after write
```

| Field        | Type   | Default               | Description                                   |
| ------------ | ------ | --------------------- | --------------------------------------------- |
| `type`       | `enum` | —                     | `local` or `gitops`                           |
| `path`       | `str`  | `.strata/deployments` | Base path for manifest files                  |
| `repository` | `str`  | `null`                | Repository name (required when `type=gitops`) |
| `branch`     | `str`  | `null`                | Target branch (required when `type=gitops`)   |
| `tag`        | `bool` | `true`                | Create a git tag after writing (gitops only)  |

### Terraform Output Artifact Storage

Controls whether Terraform output values are written to a durable artifact file after a successful
deploy. When absent, outputs are available to downstream stages within the same run but not persisted.

```yaml
spec:
  deployment:
    outputs:
      enabled: true                   # set to false to disable
      path: ".strata/outputs"         # base path; appended: /{deployment}/{version}/{stage}.json
      sensitive: redact               # redact | omit
```

Written to: `{work_path}/{path}/{deployment_name}/{version}/{stage_name}.json`

Artifact structure:

```json
{
  "deployment": "prod_deploy",
  "version": "2.0.0",
  "stage": "network",
  "written_at": "2026-06-15T10:00:00Z",
  "outputs": {
    "server_ip": "10.0.0.5",
    "db_password": "(sensitive)"
  }
}
```

| Field       | Type   | Default           | Description                                                                         |
| ----------- | ------ | ----------------- | ----------------------------------------------------------------------------------- |
| `enabled`   | `bool` | `true`            | Write the artifact. Set `false` to disable without removing config                  |
| `path`      | `str`  | `.strata/outputs` | Base path for output artifacts                                                      |
| `sensitive` | `enum` | `redact`          | `redact` — keep key, replace value with `"(sensitive)"`. `omit` — drop key entirely |

> Sensitive output handling applies to any Terraform output declared `sensitive = true`.
> Non-sensitive outputs are always stored as-is.
> Write failures are non-fatal and logged as warnings.

---

## Integrations

`spec.integrations` declares external service integrations used by the platform — primarily
SIEM sinks for audit event forwarding. Each integration is referenced by name from
`spec.audit.sinks[].integration` in environment YAML.

```yaml
spec:
  integrations:
    - name: splunk_hec           # Referenced by audit sinks
      type: splunk
      enabled: true
      endpoints:
        address: "https://splunk.internal:8088"
      authentication:
        method: api_key
        api_key:
          api_key: "${SPLUNK_HEC_TOKEN}"    # Resolved from env at deploy time
      properties:
        index: strata
        source: strata-deploy
        sourcetype: _json
        channel: "guid-for-indexer-ack"    # optional HEC channel
```

### Supported SIEM types

#### `splunk` — Splunk HTTP Event Collector (HEC)

Forwards events via the HEC endpoint (`POST /services/collector`).

| Property     | Default  | Description                                        |
| ------------ | -------- | -------------------------------------------------- |
| `index`      | `main`   | Splunk index                                       |
| `source`     | `strata` | Event source label                                 |
| `sourcetype` | `_json`  | Sourcetype (use `_json` for structured data)       |
| `channel`    | —        | HEC channel GUID (enables indexer acknowledgement) |

```yaml
- name: splunk_hec
  type: splunk
  endpoints:
    address: "https://splunk.corp.example:8088"
  authentication:
    method: api_key
    api_key:
      api_key: "${SPLUNK_HEC_TOKEN}"
  properties:
    index: platform
    sourcetype: _json
```

#### `elk` — ELK / Logstash

Forwards events via TCP (Logstash JSON codec) or HTTP (Elasticsearch Bulk API).

| Property        | Default        | Description                                |
| --------------- | -------------- | ------------------------------------------ |
| `protocol`      | `tcp`          | `tcp` (Logstash) or `http` (Elasticsearch) |
| `index_pattern` | `strata-audit` | Elasticsearch index prefix                 |

```yaml
# TCP (Logstash JSON input)
- name: elk_logstash
  type: elk
  endpoints:
    address: "logstash.internal:5044"
  properties:
    protocol: tcp

# HTTP (Elasticsearch Bulk API)
- name: elk_es
  type: elk
  endpoints:
    address: "https://es.internal:9200"
  authentication:
    method: basic
    basic:
      username: strata
      password: "${ES_PASSWORD}"
  properties:
    protocol: http
    index_pattern: strata-prod
```

#### `otel` — OpenTelemetry (OTLP/HTTP)

Forwards events as OTLP Log Records to any OpenTelemetry-compatible backend
(Grafana, Datadog, Splunk OTel Collector, etc.).

| Property              | Default | Description                        |
| --------------------- | ------- | ---------------------------------- |
| `protocol`            | `http`  | `http` (OTLP/HTTP JSON)            |
| `resource_attributes` | `{}`    | Extra OTel resource attributes map |

```yaml
- name: otel_collector
  type: otel
  endpoints:
    address: "https://otel.internal:4318"
  properties:
    resource_attributes:
      service.name: strata
      deployment.environment: production
```

#### `sentinel` — Azure Sentinel (DCR Logs Ingestion API)

Forwards events via the Azure Monitor Logs Ingestion API (DCR-based).
Uses `DefaultAzureCredential` — managed identity, service principal, or Azure CLI.

| Property                  | Required | Description                                       |
| ------------------------- | -------- | ------------------------------------------------- |
| `data_collection_rule_id` | Yes      | Immutable DCR ID (e.g. `dcr-abc123`)              |
| `stream_name`             | Yes      | Custom stream name (e.g. `Custom-DeployAudit_CL`) |

```yaml
- name: azure_sentinel
  type: sentinel
  endpoints:
    address: "https://my-dce.westeurope-1.ingest.monitor.azure.com"
  properties:
    data_collection_rule_id: dcr-0abc1234567890def
    stream_name: Custom-StrataDeployAudit_CL
```

### Integration field reference

| Field               | Type   | Required | Description                                                 |
| ------------------- | ------ | -------- | ----------------------------------------------------------- |
| `name`              | string | Yes      | Unique name referenced by `audit.sinks[].integration`       |
| `type`              | enum   | Yes      | `splunk`, `elk`, `otel`, `sentinel`                         |
| `enabled`           | bool   | No       | Defaults to `true`. Set `false` to disable without removing |
| `endpoints.address` | string | Yes      | Target URL or `host:port`                                   |
| `authentication`    | object | No       | `method`: `api_key`, `basic`, `bearer`                      |
| `properties`        | map    | No       | Type-specific configuration (see per-type tables)           |

---

## Notes

- Built-in default in `src/STRATA_platform/data/configuration.yaml` always loads first
- Use numeric prefixes (00-, 10-, 20-) to control merge order
- Set `additional_regions: false` to restrict regions
- Regex patterns validate resource configurations
- See workspace.md, environment.md, deployment.md for usage