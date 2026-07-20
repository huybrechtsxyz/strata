# Pulumi Provisioner — Code-First Multi-Cloud IaC

- Status: proposed
- Date: 2026-07-20
- Priority: follows ADR-0046 (Bicep)

## Context and Problem Statement

strata supports HCL-based Terraform and Azure-native Bicep for infrastructure provisioning. A growing segment of platform engineering teams prefer **Pulumi**, which expresses infrastructure using general-purpose programming languages (Python, TypeScript, Go, C#) rather than a DSL. Pulumi is the clear #2 IaC tool after Terraform and is used at Snowflake, Atlassian, and large cloud-native organisations.

Key reasons teams choose Pulumi over Terraform:
- **Real programming languages** — loops, conditionals, abstractions, testing frameworks without HCL workarounds
- **Multi-cloud** — same engine as Terraform; supports AWS, Azure, GCP, Kubernetes, and 100+ providers
- **Pulumi Packages** — reusable infrastructure components published to npm/PyPI — richer than Terraform modules for complex abstractions
- **Native testing** — infrastructure code is testable with pytest/jest/go test without additional tooling
- **Automation API** — Pulumi exposes a full SDK for embedding deployments in Python/TypeScript code (useful for strata's AI agent integration)

## Key Differences vs Terraform

| Aspect                 | Terraform                           | Pulumi                                                   |
| ---------------------- | ----------------------------------- | -------------------------------------------------------- |
| Language               | HCL                                 | Python, TypeScript, Go, C#, Java, YAML                   |
| State management       | `.tfstate` file (backend-stored)    | Pulumi stack state (Pulumi Cloud or self-hosted backend) |
| Multi-cloud            | ✅                                   | ✅                                                        |
| Module ecosystem       | Terraform Registry                  | npm / PyPI / NuGet packages                              |
| Drift detection        | `terraform plan`                    | `pulumi preview`                                         |
| Output values          | `terraform output`                  | `pulumi stack output`                                    |
| Automation API         | Limited                             | Full SDK (`pulumi.automation`)                           |
| `strata deploy status` | Reads `.tfstate`                    | `pulumi stack ls --json`                                 |
| `strata deploy drift`  | `terraform plan -detailed-exitcode` | `pulumi preview --diff --expect-no-changes`              |

## Impact on strata Architecture

### Build phase (`strata build run`)
Pulumi programs are compiled/resolved as part of the language runtime — there is no separate "build" step equivalent to `terraform init`. strata's build phase would:
- Run `pulumi preview --non-interactive` to validate the program and produce a diff plan
- Capture the preview output as the build artifact
- Resolve stack configuration from Pulumi config files (`Pulumi.<stack>.yaml`)

### Deploy phase (`strata deploy run`)
```bash
# Deploy
pulumi up --yes --non-interactive --stack <stack-name>

# Destroy
pulumi destroy --yes --non-interactive --stack <stack-name>

# Plan (dry-run)
pulumi preview --non-interactive --stack <stack-name>
```

### State and backend
Pulumi state is stored in a **Pulumi backend** — analogous to Terraform's backend. The strata `backend:` block maps to Pulumi's backend configuration:

| Backend type           | Pulumi configuration                 |
| ---------------------- | ------------------------------------ |
| Pulumi Cloud (default) | `org: my-org`, `project: my-project` |
| Azure Blob Storage     | `url: azblob://my-container`         |
| AWS S3                 | `url: s3://my-bucket`                |
| Local (dev only)       | `url: file://./state`                |

### Stack vs workspace separation
Pulumi uses **stacks** for environment isolation (dev/staging/prod) — analogous to Terraform workspaces. strata would map one Pulumi stack per deployment stage.

### `ProvisionerType` enum addition
```python
class ProvisionerType(str, Enum):
    ...
    PULUMI = "pulumi"   # ← new
```

### Workspace YAML shape
```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: workspace
spec:
  provisioners:
    - name: infrastructure
      provisioner: pulumi
      source:
        repository: my-repo
        source_path: infra            # directory containing Pulumi.yaml
      backend:
        type: pulumi_cloud            # pulumi_cloud | azure_blob | s3 | local
        configuration:
          organization: my-org       # required for pulumi_cloud
          project: my-project        # required for pulumi_cloud; defaults to Pulumi.yaml name
          stack: production          # Pulumi stack name (maps to environment)
      configuration:
        runtime: python              # python | nodejs | go | dotnet (must match Pulumi.yaml)
```

### Status, drift, history
| strata command   | Pulumi implementation                                       |
| ---------------- | ----------------------------------------------------------- |
| `deploy status`  | `pulumi stack ls --json` + `pulumi stack --show-ids --json` |
| `deploy drift`   | `pulumi preview --diff --expect-no-changes`                 |
| `deploy history` | `pulumi stack history --json`                               |
| `deploy output`  | `pulumi stack output --json`                                |

## Considered Options

### Option A: No Pulumi support
- Operators wanting Pulumi use the `script` provisioner
- **Rejected:** `script` loses structured `deploy status`, `deploy drift`, `deploy history`

### Option B: Pulumi as a built-in provisioner (CHOSEN)
- Add `ProvisionerType.PULUMI = "pulumi"` to the enum
- Implement `PulumiBuilder` and `PulumiDeployer`
- **Chosen:** Pulumi is widely adopted (#2 IaC tool); first-class support is warranted

### Option C: Pulumi as a workspace plugin (ADR-0023)
- **Deferred:** Plugin API not yet stable. Pulumi's adoption justifies built-in support.

## Decision Outcome

Chosen: **Option B — Pulumi as a built-in provisioner type**, implemented after ADR-0046 (Bicep).

## Implementation Roadmap

### Phase 1 — Enum and model
1. Add `ProvisionerType.PULUMI = "pulumi"` to `common_models.py`
2. Add `pulumi_cloud`, `azure_blob`, `s3`, `local` backend types for Pulumi
3. Validate `configuration.runtime` against supported Pulumi runtimes
4. Schema documentation update

### Phase 2 — Build integration
1. `PulumiBuilder` — runs `pulumi preview --non-interactive`; captures diff as plan artifact
2. Wire into `_create_builder()` dispatcher

### Phase 3 — Deploy integration
1. `PulumiDeployer` — wraps `pulumi up --yes --non-interactive`
2. `PulumiDeployer.status()` — `pulumi stack ls --json`
3. `PulumiDeployer.drift()` — `pulumi preview --expect-no-changes`
4. `PulumiDeployer.history()` — `pulumi stack history --json`
5. Wire into `_create_deployer()` dispatcher

### Phase 4 — Tooling check
1. `strata tools status` — add `pulumi version` check
2. Runtime-specific checks: `python --version`, `node --version`, etc. based on `configuration.runtime`

## Consequences

### Positive
- Teams already writing Python/TypeScript can use strata without learning HCL
- Full multi-cloud support maintained
- Pulumi Automation API enables richer AI agent integration (ADR-0025)
- Testing with standard test frameworks (pytest, jest) — no separate IaC test tooling

### Negative
- Pulumi Cloud account (or self-hosted backend) required for state storage — adds operational dependency
- Multiple runtime dependencies (Python, Node.js, Go, .NET) depending on the team's language choice
- Smaller community module ecosystem than Terraform Registry
- `pulumi preview` is slower than `terraform plan` for large stacks

## More Information

- [Pulumi documentation](https://www.pulumi.com/docs/)
- [Pulumi Automation API](https://www.pulumi.com/docs/using-pulumi/automation-api/)
- [Pulumi backends](https://www.pulumi.com/docs/concepts/state/)
- Related ADRs: ADR-0023 (pluggable provisioner framework), ADR-0046 (Bicep), ADR-0025 (AI agent integration)
