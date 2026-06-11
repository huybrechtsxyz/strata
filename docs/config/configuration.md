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

| Field         | Type     | Default | Description                                              |
| ------------- | -------- | ------- | -------------------------------------------------------- |
| `pattern`     | `str`    | —       | Regex the field value must fully match                   |
| `required`    | `bool`   | `true`  | Whether the field must be present in resource config     |
| `description` | `str`    | `null`  | Human-readable description of the field                  |

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

## Notes

- Built-in default in `src/STRATA_platform/data/configuration.yaml` always loads first
- Use numeric prefixes (00-, 10-, 20-) to control merge order
- Set `additional_regions: false` to restrict regions
- Regex patterns validate resource configurations
- See workspace.md, environment.md, deployment.md for usage