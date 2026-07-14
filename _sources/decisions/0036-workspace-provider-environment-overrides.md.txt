# Workspace, Provider, and Environment-level Provider Overrides

- Status: completed
- Date: 2026-07-14

## Context and Problem Statement

Today, a strata workspace defines providers (cloud platforms, regions, credentials) that are used
during deployment. However, different environments (dev, staging, production) often need to deploy
to **different cloud providers, regions, or authentication contexts**. For example:

- **Dev environment** → AWS us-east-1 with ephemeral credentials
- **Staging environment** → Azure eastus with service principal auth
- **Production environment** → AWS eu-west-1 with assumed role + MFA

Currently, there is no mechanism for an environment to override or influence provider configuration.
The provider is fixed in the workspace, and all environments must use the same provider setup.

**The gaps:**

1. **No provider-per-environment** — Cannot specify different cloud platforms across environments
2. **No region override** — Cannot pin a different region per environment
3. **No auth override** — Cannot use different credentials (role assumption, service principal, etc.) per environment
4. **No provider switching** — Cannot route multi-cloud deployments (some resources to AWS, some to Azure)
5. **Unclear provider scope** — Is a provider global to the workspace, or per-namespace/topology?

This creates constraints:
- Multi-cloud and multi-region strategies cannot be expressed in strata
- Dev/prod parity breaks when environments have fundamentally different infrastructure targets
- Cross-region failover and disaster recovery patterns are unsupported

## Related Work

- **ADR 0024 — Environment Composition (Flat Merge Fix)**: Describes how environment overrides merge with
  workspace config (resources, modules). This ADR extends that pattern to providers.
- **ADR 0023 — Pluggable Provisioner Framework**: Defines provisioner types (Terraform, Ansible, etc.).
  Providers are provisioner-specific (Terraform provider, Helm provider, etc.).
- **ADR 0030 — Command Lifecycle Explicitness and Thin Overrides**: Establishes the override philosophy
  (thin overrides, clear lineage, no deep merging).

## Design Overview

### Terminology Clarification

| Term            | Definition                                                                      | Scope                | Mutable                    |
| --------------- | ------------------------------------------------------------------------------- | -------------------- | -------------------------- |
| **Workspace**   | Root configuration file defining provisioners, providers, topologies, resources | Global               | Once (at definition)       |
| **Provider**    | Cloud/infrastructure platform config (cloud, region, auth, API endpoint)        | Per-provisioner type | Fixed in workspace (today) |
| **Environment** | Deployment context (dev, staging, prod, canary)                                 | One per deployment   | Thin overrides only        |
| **Override**    | Environment-level delta from workspace baseline                                 | Per-environment      | At deploy time             |
| **Topology**    | Cluster/infrastructure grouping (single provisioner, namespace set)             | Per-provisioner      | References providers       |

### Current Architecture

Providers are **separate files** referenced from the workspace. Each provider defines cloud platform,
region, and authentication independently.

```yaml
# workspace.yaml
spec:
  providers:
    - name: aws-primary
      file: providers/aws-primary.yaml     # ← Separate file reference
  
  provisioners:
    - name: terraform
      provisioner: terraform
      # ... provisioner config
  
  topology:
    - name: main
      provisioner: terraform               # ← References provisioner name
      provider: aws-primary                # ← References provider name (fixed at workspace definition)
      # ... topology config

---

# providers/aws-primary.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: Provider
meta:
  name: aws-primary
spec:
  properties:
    type: aws                              # Cloud platform
    region: us-east-1                      # Region (fixed)
    engine: azurerm                        # Optional IaC engine
    version: "~>5.0"                       # Optional version constraint
  authentication:
    role_arn: arn:aws:iam::123456789:role/terraform
  references:
    secrets: [aws_role_arn]               # Secret references

---

# environments/prod.yaml
spec:
  overrides: {}  # ← No way to override provider region, auth, etc.
```

**Problem:** A topology's provider is fixed at workspace definition time. If production must deploy to
`eu-west-1` instead of `us-east-1`, or use different authentication, there is no way to express this
without creating an entirely separate provider file and topology in the workspace.

### Proposed: Provider Override Pattern

Environments gain a new override section: `spec.overrides.providers`. This follows the same pattern as
resource and module overrides (ADR 0024).

**Key principle:** Provider **names** are stable workspace identifiers. Environments can override which **file**
is used for each provider name, but cannot change the name itself. This keeps provider references clean and
unambiguous throughout the workspace.

```yaml
# workspace.yaml (defines provider names and default files)
spec:
  providers:
    - name: primary                        # Stable name throughout workspace
      file: providers/aws-primary.yaml    # Default file
  
  provisioners:
    - name: terraform
      provisioner: terraform
  
  topology:
    - name: main
      provisioner: terraform
      provider: primary                    # Always references by name, not file

---

# providers/aws-primary.yaml (base — dev/default)
apiVersion: strata.huybrechts.xyz/v1
kind: Provider
meta:
  name: primary
spec:
  properties:
    type: aws
    region: us-east-1                      # Base: US region
  authentication:
    role_arn: arn:aws:iam::123456789:role/terraform

---

# providers/aws-primary-prod.yaml (production variant)
apiVersion: strata.huybrechts.xyz/v1
kind: Provider
meta:
  name: primary
spec:
  properties:
    type: aws
    region: eu-west-1                      # Prod: EU region
  authentication:
    role_arn: arn:aws:iam::999888777:role/terraform-prod

---

# environments/prod.yaml
spec:
  overrides:
    providers:
      - name: primary                      # Same name, different file
        file: providers/aws-primary-prod.yaml  # ← Override: prod uses prod provider file
```

**Resolution semantics (ADR 0030 — thin overrides):**
- Provider **names** are immutable references throughout the workspace
- Workspace defines base file for each provider name
- Environment override can point to a different file for the same provider name
- File is resolved at deploy time: `resolved_file = environment.overrides.providers[name].file or workspace.providers[name].file`
- Provider spec is loaded from the resolved file (no field-level merging)

### Resolution Points

**When are provider overrides resolved?**

1. **At `strata build` time** — Provider config is baked into the build artifacts (Terraform `.tf.json`, manifests, etc.)
2. **At `strata deploy` time** — Provider config is used to determine credentials, regions, API endpoints
3. **At validation** — Provider schema validation must occur after merging (base + overrides)

**Flow:**

```
strata deploy run -f {deployment_file} [{optional} --environment {env_name}]
  ↓
Load workspace.yaml → extract providers[].name and providers[].file
  ↓
For each topology:
  Get provider name from topology.provider
  ↓
  Resolve provider file:
    if environment.overrides.providers[name].file exists → use that
    else → use workspace.providers[name].file
  ↓
  Load provider spec from resolved file
  ↓
  Validate provider spec against provider schema
  ↓
Build artifacts (provisioner-specific) with resolved provider specs
  ↓
Deploy using resolved provider config (credentials, regions, etc.)
```

**Key difference:** No spec-level merging. File selection happens at environment resolution time.

## Considered Options

### Option A: No environment provider overrides (status quo)

**Pros:**
- Simplicity — no new override mechanism
- Clear separation — workspace defines infrastructure, environment is just "run location"

**Cons:**
- Multi-cloud/multi-region deployments impossible
- Dev/prod parity breaks for region/auth changes
- Workaround: duplicate workspace files per environment (code smell)

### Option B: Thin provider overrides (proposed)

**Pros:**
- Extends existing ADR 0024 override pattern (consistency)
- Environments control provider without redefining entire workspace
- Supports multi-cloud and multi-region strategies
- Audit trail: override file shows what changed from base

**Cons:**
- More complex validation (must validate after merging)
- Potential for invalid merges (e.g., conflicting provider types)
- Requires careful schema design

### Option C: Provider templates with placeholders

Use templating (Jinja2 — ADR 0017) to inject environment-specific values:

```yaml
# workspace.yaml
spec:
  providers:
    - name: aws-primary
      provisioner: terraform
      configuration:
        cloud: aws
        region: "{{ var.provider_region | default('us-east-1') }}"
        auth:
          role-arn: "{{ var.terraform_role_arn }}"
```

Then environments pass variables:

```yaml
# environments/prod.yaml
spec:
  variables:
    - name: provider_region
      value: eu-west-1
    - name: terraform_role_arn
      value: arn:aws:iam::999888777:role/terraform-prod
```

**Pros:**
- Leverages existing templating system

**Cons:**
- Less explicit — provider config scattered across workspace + environment files
- Harder to audit (must resolve templates to see actual config)
- Mixes provider config (infrastructure concern) with variables (application concern)

### Option D: Separate provider environment files

Each environment has its own provider overrides file (similar to GitOps pattern):

```
environments/
  dev/
    providers.yaml    # ← Explicit provider overrides
    deployment.yaml
  prod/
    providers.yaml
    deployment.yaml
```

**Pros:**
- Explicitly locates provider overrides
- Clear file structure

**Cons:**
- More files to manage
- Duplicates provider names across files
- Harder to see inheritance (base vs. override)

## Decision Outcome

**Option B: Thin provider overrides** — Extend the existing environment override pattern (ADR 0024)
to support provider configuration overrides.

**Why:**
1. **Consistency** — Aligns with resource and module overrides (ADR 0024)
2. **Simplicity** — Thin merge semantics, no deep nesting
3. **Expressiveness** — Supports multi-cloud and multi-region patterns
4. **Auditability** — Override file clearly shows deltas

## Detailed Design

### Provider Override Model

**New model:** `EnvironmentProviderOverrideModel` (parallel to `EnvironmentResourceOverrideModel`)

The override model is **minimal**: it only specifies which file to use for a provider name.

```python
# models/environment_model.py

class EnvironmentProviderOverrideModel(PlatformBaseModel):
    """Override provider file selection at environment level."""
    
    name: PlatformName  # Must reference a provider name in workspace (immutable)
    
    # File override (required — if you override, you must specify the file)
    file: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] = Field(
        ...,  # Required
        description="Path to the provider configuration file for this environment. "
                   "Allows different environments to use different provider files (e.g., dev vs prod configurations)."
    )
    
    description: Optional[str] = None
    
    # Audit trail
    reason: Optional[str] = Field(
        None,
        description="Why this override exists (e.g., 'prod uses different region and credentials')"
    )


class EnvironmentOverridesModel(PlatformBaseModel):
    """Extended from ADR 0024 to include provider overrides."""
    
    resources: Optional[List[EnvironmentResourceOverrideModel]]
    modules: Optional[List[EnvironmentModuleOverrideModel]]
    providers: Optional[List[EnvironmentProviderOverrideModel]]  # ← NEW
```

### File Resolution Algorithm

**Location:** `core/resolution/provider_resolver.py`

```python
def resolve_provider_file(workspace_providers, environment_overrides, provider_name) -> str:
    """
    Resolve which provider file to use for a given provider name.
    
    Semantics (ADR 0030 — thin overrides):
    - Provider names are immutable references
    - Environment can override the file path for a provider name
    - If no override exists, use workspace default
    - If provider name doesn't exist in workspace → validation error
    
    Args:
        workspace_providers: List of WorkspaceProviderModel from workspace.yaml
        environment_overrides: List of EnvironmentProviderOverrideModel from environment.yaml
        provider_name: Name of the provider to resolve
    
    Returns:
        str: Resolved file path
    
    Raises:
        ProviderValidationError: If provider name not found in workspace
    """
    
    # Find workspace default
    workspace_entry = next(
        (p for p in workspace_providers if p.name == provider_name),
        None
    )
    if not workspace_entry:
        raise ProviderValidationError(f"Provider '{provider_name}' not found in workspace")
    
    # Check for environment override
    if environment_overrides:
        override = next(
            (p for p in environment_overrides if p.name == provider_name),
            None
        )
        if override and override.file:
            return override.file  # Use environment file
    
    # Fall back to workspace default
    return workspace_entry.file


def load_provider_spec(file_path, expected_provider_name: str = None) -> ProviderModel:
    """
    Load and validate provider spec from file.
    
    Args:
        file_path: Path to provider YAML file
        expected_provider_name: If provided, validate that provider meta.name matches.
                               Catches misconfigured provider files early.
    
    Returns:
        ProviderModel: Loaded and validated provider
    
    Raises:
        FileNotFoundError: If file doesn't exist
        ProviderValidationError: If provider spec is invalid or meta.name doesn't match expected name
    """
    with open(file_path, 'r') as f:
        data = yaml.safe_load(f)
    
    provider = ProviderModel.model_validate(data)
    provider.spec.validate()
    
    # Validate provider meta.name matches expected name (prevents misconfiguration)
    if expected_provider_name and provider.meta.name != expected_provider_name:
        raise ProviderValidationError(
            f"Provider file '{file_path}' has meta.name '{provider.meta.name}' "
            f"but workspace references provider '{expected_provider_name}'. "
            f"Ensure provider file meta.name matches the provider name in workspace.spec.providers."
        )
    
    return provider
```

### Validation

**When:** At file resolution time (before build/deploy)

**Checks:**
1. **Provider name exists** — Environment override references a provider name in workspace
2. **Provider file path is valid** — Resolved file path is specified
3. **Provider file exists** — File can be opened and read
4. **Provider spec is valid YAML** — File parses as valid YAML
5. **Provider spec matches schema** — ProviderModel.model_validate() succeeds
6. **Meta name matches** — Provider file's `meta.name` matches the provider name from workspace
7. **Required fields present** — Provider spec has required fields (type, region, etc.)

**Example validation errors:**

```
# Error 1: Provider name not in workspace
Error: Provider 'unknown_provider' referenced in environments/prod.yaml overrides
  does not exist in workspace.spec.providers

# Error 2: Meta name mismatch
Error: Provider file 'providers/aws-prod.yaml' has meta.name 'aws-prod'
  but workspace references provider 'primary'
  Ensure provider file meta.name matches the provider name in workspace.spec.providers
  
  Fix: Either rename meta.name in the provider file OR update workspace to use 'aws-prod'

# Error 3: Missing required field
Error: Invalid provider spec in 'providers/aws-prod.yaml'
  Provider 'primary' has missing required field: 'region'
  
  Fix: Add 'region' to spec.properties in the provider file
```

### Configuration Resolution Points

**Build time** — Resolved provider specs are loaded and embedded in build artifacts:

```python
# builders/terraform_builder.py (example)

def build_terraform(workspace, environment, build_path):
    # For each topology's provider, resolve file path then load spec
    resolved_providers = {}
    for topology in workspace.topology:
        provider_name = topology.provider
        
        # Step 1: Resolve which file to use
        provider_file = resolve_provider_file(
            workspace.spec.providers,
            environment.spec.overrides.providers if environment else [],
            provider_name
        )
        
        # Step 2: Load provider spec from resolved file
        # Pass expected_provider_name for validation (prevents misconfigured files)
        provider_model = load_provider_spec(provider_file, expected_provider_name=provider_name)
        resolved_providers[provider_name] = provider_model.spec
    
    # Build Terraform config with resolved providers
    terraform_config = {
        "terraform": {
            "required_providers": { ... },
            "provider": {}  # ← Populated from resolved provider specs
        },
        "resource": { ... }
    }
    
    for provider_name, spec in resolved_providers.items():
        terraform_config["terraform"]["provider"][provider_name] = {
            "type": spec.properties.type,
            "region": spec.properties.region,
            # ... auth fields from spec.authentication
        }
    
    return terraform_config
```

**Deploy time** — Provider credentials are resolved from secrets and injected:

```python
# deployers/terraform_deployer.py (example)

def deploy(workspace, environment):
    # Same file resolution as build time
    resolved_providers = {}
    for topology in workspace.topology:
        provider_name = topology.provider
        provider_file = resolve_provider_file(
            workspace.spec.providers,
            environment.spec.overrides.providers if environment else [],
            provider_name
        )
        provider_model = load_provider_spec(provider_file)
        resolved_providers[provider_name] = provider_model.spec
    
    for provider_name, spec in resolved_providers.items():
        # Resolve auth secrets (role ARN, API keys, etc.)
        if spec.authentication:
            auth_resolved = resolve_authentication(
                spec.authentication,
                environment.secrets  # Secret store
            )
        
        # Set environment variables / CLI flags for provisioner
        os.environ[f"TF_VAR_{provider_name}_region"] = spec.properties.region
        os.environ[f"AWS_ROLE_ARN"] = auth_resolved.role_arn
        # ... etc
```

## Scope and Limitations

### What can be overridden?

Environments can override the **provider file** for each provider name. The file contains everything:
- Cloud platform (AWS, Azure, GCP)
- Region / zone
- Authentication (role assumption, service principal, API keys, etc.)
- API endpoint / base URL
- Engine type and version
- Additional provider-specific settings

### What cannot be overridden?

- **Provider name** — Cannot rename a provider at environment level. Names are stable workspace identifiers.
- **Topology provider references** — Topologies always reference providers by name. Cannot route to a different provider.
- **Provisioner type** — A provider remains bound to its provisioner type (Terraform, Helm, etc.).

**Philosophy:** Environments choose **different files**, not different settings. This keeps overrides simple and auditable.

### Multi-cloud example (why this matters)

```yaml
# workspace.yaml (workspace defines provider names + default files)
spec:
  providers:
    - name: primary                       # Stable name for AWS
      file: providers/aws-default.yaml   # Dev/default config
    - name: backup                        # Stable name for Azure
      file: providers/azure-default.yaml # Dev/default config
  
  provisioners:
    - name: terraform
      provisioner: terraform
  
  topology:
    - name: main
      provisioner: terraform
      provider: primary                   # Always references 'primary' by name
    - name: failover
      provisioner: terraform
      provider: backup                    # Always references 'backup' by name

---

# providers/aws-default.yaml (dev/base)
apiVersion: strata.huybrechts.xyz/v1
kind: Provider
meta:
  name: primary
spec:
  properties:
    type: aws
    region: us-east-1
  authentication:
    role_arn: arn:aws:iam::123456789:role/terraform

---

# providers/aws-prod.yaml (prod variant — different region, different role)
apiVersion: strata.huybrechts.xyz/v1
kind: Provider
meta:
  name: primary
spec:
  properties:
    type: aws
    region: eu-west-1                    # Different region for GDPR compliance
  authentication:
    role_arn: arn:aws:iam::999888777:role/terraform-prod  # Different role

---

# providers/azure-default.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: Provider
meta:
  name: backup
spec:
  properties:
    type: azure
    region: eastus
  authentication:
    service_principal_id: ${secret:azure_sp_id}

---

# providers/azure-prod.yaml (prod variant — different region)
apiVersion: strata.huybrechts.xyz/v1
kind: Provider
meta:
  name: backup
spec:
  properties:
    type: azure
    region: northeurope                  # Different region for GDPR compliance
  authentication:
    service_principal_id: ${secret:azure_sp_id_prod}

---

# environments/prod.yaml
spec:
  overrides:
    providers:
      - name: primary
        file: providers/aws-prod.yaml     # Use prod AWS config
      - name: backup
        file: providers/azure-prod.yaml   # Use prod Azure config
```

**Benefits:**
- Provider **names** (`primary`, `backup`) stay consistent throughout workspace
- Topologies reference names, never files
- Each environment can use completely different provider files without code duplication
- Enables **geo-distributed deployments** without changing workspace structure

## Implementation Steps

1. **Add model** — `EnvironmentProviderOverrideModel` to `models/environment_model.py` (file override only)
2. **Add file resolution** — `resolve_provider_file()` in `core/resolution/provider_resolver.py`
3. **Add file loader** — `load_provider_spec()` in `core/resolution/provider_resolver.py`
4. **Update builders** — Terraform, Helm, Ansible builders use `resolve_provider_file()` and `load_provider_spec()`
5. **Update deployers** — Same file resolution pattern used at deploy time
6. **Add validation** — Ensure environment overrides reference valid provider names from workspace
7. **CLI updates** — No new flags needed (`strata deploy --environment` already exists)
8. **Tests** — Unit tests for file resolution, multi-region E2E tests, provider file loading tests
9. **Docs** — Update provider configuration guide with override examples

## Open Questions

1. **Provider inheritance hierarchy** — Can an environment override a provider that an earlier environment already overrode? (Answer: Yes, each environment sees workspace as baseline.)
2. **Multi-region deployments** — If a stage references multiple topologies with different providers, are all resolved providers used? (Answer: Yes, each topology's provider is resolved independently.)
3. **Cross-provisioner routing** — Can a resource specify which provider to use? (Future ADR: resource-provider affinity.)
4. **Credential rotation** — If a secret (role ARN, API key) is rotated, how does strata refresh it? (Future ADR: credential lifecycle.)
5. **Provider health checks** — Should strata validate provider connectivity before deploy? (Future ADR: pre-deploy validation.)

## Next Steps

- [x] Implement `EnvironmentProviderOverrideModel`
- [x] Implement `resolve_provider_file()` with full test coverage
- [x] Implement `load_provider_spec()` with validation
- [x] Update Terraform builder to use resolved provider file
- [x] Add CLI test: `strata deploy --environment prod` with provider file overrides
- [x] Document in provider configuration guide
- [x] Add to ADR decision log

## References

- [ADR 0024 — Environment Composition (Flat Merge Fix)](0024-environment-composition-flat-merge-fix.md)
- [ADR 0030 — Command Lifecycle Explicitness and Thin Overrides](0030-command-lifecycle-explicitness-and-thin-overrides.md)
- [ADR 0023 — Pluggable Provisioner Framework](0023-pluggable-provisioner-framework.md)
