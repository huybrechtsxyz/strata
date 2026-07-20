# CDK Provisioner — Cloud Development Kit (on Popular Demand)

- Status: proposed
- Date: 2026-07-20
- Priority: low — implement only on demonstrated operator demand

## Context and Problem Statement

The Cloud Development Kit (CDK) family covers two distinct tools with different deployment targets:

- **AWS CDK** — Amazon's IaC framework using TypeScript/Python/Java/C#; compiles to CloudFormation templates; AWS-only
- **CDK for Terraform (CDKTF)** — HashiCorp's variant; same programming model as AWS CDK but compiles to Terraform JSON configuration; works with any Terraform provider (Azure, GCP, on-prem)

Both allow teams to write infrastructure in a general-purpose language and generate a lower-level IaC format at build time. The key distinction from Pulumi (ADR-0047) is that CDK is a **code-to-template compiler** — the actual deployment engine is CloudFormation (AWS CDK) or Terraform (CDKTF), not CDK itself.

This ADR is recorded to document the analysis and defer implementation until there is clear operator demand. Teams that need CDK today can use the `script` provisioner as a workaround.

## Key Differences vs Terraform and Pulumi

| Aspect             | Terraform        | CDKTF                                                 | AWS CDK                                     |
| ------------------ | ---------------- | ----------------------------------------------------- | ------------------------------------------- |
| Language           | HCL              | TypeScript, Python, Go, C#                            | TypeScript, Python, Go, Java, C#            |
| Deployment engine  | Terraform CLI    | Terraform CLI (CDKTF compiles to Terraform JSON)      | CloudFormation                              |
| Multi-cloud        | ✅                | ✅ (via Terraform providers)                           | ❌ AWS-only                                  |
| State management   | `.tfstate`       | `.tfstate` (same as Terraform)                        | CloudFormation stack state                  |
| Build step         | `terraform init` | `cdktf synth` (generates Terraform JSON)              | `cdk synth` (generates CloudFormation JSON) |
| Drift detection    | `terraform plan` | `cdktf diff` → `terraform plan`                       | `cdk diff`                                  |
| strata integration | Native           | **Thin wrapper**: synth → existing Terraform deployer | New CloudFormation deployer                 |

## CDK Variants and strata Integration Strategy

### CDKTF — simplest path

CDKTF is architecturally the easiest to integrate:
1. `strata build run` calls `cdktf synth` — generates Terraform JSON in `cdktf.out/`
2. The existing `TerraformDeployer` runs against the synthesised output
3. CDKTF is essentially a **build-time code generator** that produces Terraform — the deploy layer is unchanged

This means CDKTF could be supported as a **build-only addition** without a new deployer class:
```yaml
spec:
  provisioners:
    - name: infrastructure
      provisioner: cdktf              # build: cdktf synth → deploy: terraform
      source:
        repository: my-repo
        source_path: infra
      backend:
        type: terraform_cloud         # same Terraform backend config as terraform provisioner
        configuration:
          organization: my-org
          workspace: production
```

### AWS CDK — larger investment

AWS CDK requires a new CloudFormation deployer:
- `strata build run` → `cdk synth` (generates CloudFormation templates)
- `strata deploy run` → `cdk deploy` or `aws cloudformation deploy`
- `strata deploy status` → `aws cloudformation describe-stacks`
- `strata deploy drift` → `aws cloudformation detect-stack-drift`
- AWS-only; not a priority for Azure-focused strata operator base

## `ProvisionerType` enum additions
```python
class ProvisionerType(str, Enum):
    ...
    CDKTF = "cdktf"       # CDKTF: synth → Terraform; backend = Terraform backend
    CDK   = "cdk"         # AWS CDK: synth → CloudFormation; AWS-only
```

## Why This ADR Is Deferred

1. **CDKTF is a build-step wrapper over Terraform** — most of the value is in the language choice (TypeScript/Python), which Pulumi (ADR-0047) already provides with a better native experience and no compile-to-Terraform indirection
2. **AWS CDK is AWS-only** — strata's current operator base is predominantly Azure; low demand expected
3. **CDKTF adoption is lower than Pulumi** — HashiCorp/IBM's acquisition of HashiCorp and OpenTF/OpenTofu fork uncertainty has slowed CDKTF ecosystem investment
4. **Workaround is sufficient for now** — `script` provisioner can wrap `cdktf synth && terraform apply`; teams that need CDK today are not blocked

## Implementation Trigger

This ADR moves to `accepted` when any of the following occur:
- 3+ operators request CDK support via GitHub Issues
- A strata contributor submits a design PR for the CDK deployer
- CDK becomes the default IaC toolchain for a major cloud provider's recommended Azure landing zone

Until then, teams needing CDK should use:
- `script` provisioner with `cdktf synth && terraform apply` for CDKTF
- `script` provisioner with `cdk deploy` for AWS CDK

## Consequences

### If implemented
- CDKTF: minimal new code — `CdktfBuilder` wraps `cdktf synth`, then reuses `TerraformDeployer`
- AWS CDK: full `CdkDeployer` wrapping CloudFormation APIs; moderate effort

### If not implemented
- Teams wanting CDK use `script` provisioner; `deploy status/drift/history` not available
- No impact on Terraform, Bicep, or Pulumi operators

## More Information

- [CDKTF documentation](https://developer.hashicorp.com/terraform/cdktf)
- [AWS CDK documentation](https://docs.aws.amazon.com/cdk/v2/guide/home.html)
- Related ADRs: ADR-0023 (pluggable provisioner framework), ADR-0046 (Bicep), ADR-0047 (Pulumi)
