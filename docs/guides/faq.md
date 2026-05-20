# Frequently Asked Questions

---

## Why not Terragrunt?

Terragrunt solves a similar problem (DRY Terraform configuration) but with a different philosophy:

| Aspect | strata | Terragrunt |
|--------|--------|-----------|
| Config language | YAML (declarative, tooling-friendly) | HCL (Terraform-native) |
| Approach | Generate complete Terraform, then run it | Wrap Terraform with inheritance and includes |
| Secret management | Built-in (Key Vault, Bitwarden, env vars) | External (sops, vault, env vars) |
| Drift detection | `strata diff` (built-in) | Manual `terraform plan` per module |
| Multi-stage orchestration | Deployment stages with dependency ordering | `run-all` with dependency blocks |
| Escape hatch | Copy generated `.tf` files, run Terraform directly | Already Terraform — remove `terragrunt.hcl` |
| Learning curve | Know YAML + understand the layering model | Know HCL + Terragrunt's inheritance model |

**Choose strata when:**
- You want a clean separation between config (YAML) and infrastructure code (Terraform modules).
- You need integrated secret resolution from multiple backends.
- You want `diff` and `plan` as first-class operations across environments.
- Your team prefers YAML over HCL for configuration.

**Choose Terragrunt when:**
- Your team lives in HCL and doesn't want to learn a new config format.
- You want to stay as close to raw Terraform as possible.
- You have a single state backend and simple secret management.

---

## Why not Ansible?

Ansible is a configuration management tool. strata is an infrastructure deployment orchestrator. They solve different problems and work well together.

| Aspect | strata | Ansible |
|--------|--------|---------|
| Primary purpose | Generate + deploy infrastructure (Terraform, Helm) | Configure servers + deploy applications |
| When it runs | Before infrastructure exists (or to change it) | After infrastructure exists |
| State | Terraform state (remote backend) | Stateless (or Tower/AWX for tracking) |
| Idempotency | Via Terraform's state diffing | Via module design (task-level) |

**They complement each other:** strata provisions the VMs and networking; Ansible configures the OS, installs packages, and deploys applications. Some teams use strata's `lifecycle.configure` phase to call Ansible after infrastructure is provisioned.

---

## Can I use this with existing Terraform state?

**Yes.** strata generates standard Terraform files that point to YOUR state backend. It never creates, manages, or migrates state itself.

### Adopting strata with existing infrastructure:

1. **Create your strata workspace YAML** describing the infrastructure you already have.
2. **Point the backend config** to your existing state backend (same storage account, same key).
3. **Run `strata build run`** to generate Terraform files.
4. **Run `terraform plan`** (or `strata build plan`) in the generated output.
   - If the plan shows no changes → your YAML accurately describes reality. Done.
   - If the plan shows changes → adjust your YAML until the plan is clean.

Your Terraform state stays exactly where it is. strata just generates the `.tf` files that Terraform reads — it never touches `.tfstate`.

### What about `terraform import`?

If you have infrastructure that was created outside Terraform (manually, or via another tool), you'll still need `terraform import` to bring it under state management. strata doesn't change this workflow — after import, your state reflects reality, and strata generates the config that matches.

---

## Why YAML and not HCL?

Three reasons:

1. **Separation of concerns.** HCL is great for describing infrastructure resources (your Terraform modules). YAML is great for describing configuration data (what to deploy, where, with what values). Mixing both in HCL blurs the line between "infrastructure code" and "deployment config."

2. **Tooling.** YAML is parseable by every language, every CI system, and every editor without special plugins. Linting, diffing, templating, and programmatic generation are trivial. HCL tooling exists but is Terraform-specific.

3. **Operator accessibility.** Not everyone who needs to change a variable value or add an environment should need to understand HCL syntax, Terraform internals, or provider schemas. YAML with documented fields is approachable.

---

## Can I mix provisioners?

**Yes.** A deployment can have multiple stages, each using a different provisioner:

```yaml
stages:
  - name: infrastructure
    type: infrastructure      # uses Terraform
    scope: all
  - name: configuration
    type: configuration       # could use Ansible, scripts, or Helm
    scope: all
    depends_on: [infrastructure]
```

strata orchestrates the stages in order. Each stage calls its provisioner via subprocess — it doesn't care whether that's `terraform apply`, `ansible-playbook`, or `helm upgrade`.

---

## What happens if I delete `.strata/`?

Nothing catastrophic:

- **`.strata/solution.json`** — workspace registry. Recreated by `strata sln init`.
- **`.strata/cli.yaml`** — your CLI preferences. Recreated by `strata config set`.
- **`.strata/build/`** — generated Terraform artifacts. Recreated by `strata build run`.

Your Terraform state lives in your remote backend (Azure Storage, S3, GCS). Your infrastructure is unaffected. Your source YAML files are in git. The `.strata/` directory is local workspace state — fully regenerable.

---

## How do I roll back a deployment?

strata doesn't have a dedicated `rollback` command, because the underlying model is declarative:

1. **Revert the config change in git:**
   ```bash
   git revert <commit-that-broke-things>
   ```
2. **Rebuild and redeploy:**
   ```bash
   strata build run --file deploy/deploy-prd.yaml
   strata deploy run --file deploy/deploy-prd.yaml
   ```

Terraform's plan will show the resources reverting to their previous state. This is safer than imperative rollback because you can review exactly what will change before applying.

---

## Does strata support multiple clouds?

**Yes.** A workspace can reference multiple providers (AWS, Azure, Kamatera, etc.) and each topology component maps to a specific provider. The generated Terraform includes the correct provider configurations.

Practical limit: your Terraform modules need to support the target cloud. strata handles the config layering and orchestration — the actual cloud API calls happen in Terraform.

---

## Can multiple people deploy at the same time?

State locking is handled by Terraform's backend (e.g., Azure Blob lease, S3 DynamoDB lock, Terraform Cloud). strata doesn't add its own locking.

**Best practice:** Deploy through CI/CD pipelines (one pipeline per deployment), not from developer laptops. This gives you:
- Serialized deployments (pipeline queue)
- Audit trail (CI logs)
- Approval gates (environment protection rules)
