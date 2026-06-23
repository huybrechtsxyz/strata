# Frequently Asked Questions

## What is strata?

strata is a Python CLI tool that lets you describe your entire infrastructure stack — environments, deployments, secrets, variables — in plain YAML files, then orchestrates Terraform, Helm, Ansible, and Docker Compose to bring that description to life.

The core idea: **you write config, strata runs tools.** strata never replaces Terraform, Helm, or Ansible — it sits above them as an orchestration layer, feeding each tool exactly the inputs it needs based on your YAML.

---

## How does strata work with Terraform?

strata reads your YAML workspace and deployment files, then **generates complete, ready-to-run Terraform files** into `.strata/build/`. Terraform is invoked as a subprocess and operates exactly as it normally would — with its own state backend, provider configs, and modules.

Key points:
- strata never touches `.tfstate`. All state management stays with your Terraform backend (Azure Storage, S3, GCS, Terraform Cloud).
- `strata build run` generates the `.tf` files. `strata deploy run` calls `terraform init → validate → plan → apply`.
- `strata build plan` shows a diff of what the next build would produce and runs `terraform plan` per stage — useful for drift detection in CI.
- The escape hatch is trivial: copy the generated `.tf` files out of `.strata/build/` and run Terraform directly.

---

## How does strata work with Ansible?

Ansible runs as a deployment stage inside a `strata deploy run` pipeline. strata locates the playbook via the provisioner's `source` reference, resolves any secrets it needs (SSH keys, vault passwords) from your configured secret stores, then calls `ansible-playbook` as a subprocess.

strata handles the parts that Ansible can't do natively at scale:
- Layered variable resolution across environments and tenants.
- Secret resolution from Bitwarden, Azure Key Vault, or environment variables — secrets are passed in-memory and never written to YAML.
- Sequencing Ansible stages after Terraform stages using `depends_on`.

See [config-faq.md](config-faq.md) for SSH key and secret configuration details.

---

## How does strata work with Helm and Docker Compose?

The same pattern applies. Each provisioner type (Helm, Compose) has a corresponding deployer that strata invokes as a subprocess:

- **Helm** — strata resolves values from the layering system and calls `helm upgrade --install` with the merged values file.
- **Docker Compose** — strata generates a `docker-compose.yml` from your service definitions and calls `docker compose up`.

A single deployment can chain stages across provisioner types — provision infrastructure with Terraform, then deploy applications with Helm, then verify with Ansible — all coordinated through one deployment YAML file.

---

## Why not Terragrunt?

Terragrunt solves a similar problem (DRY Terraform configuration) but with a different philosophy:

| Aspect                    | strata                                             | Terragrunt                                   |
| ------------------------- | -------------------------------------------------- | -------------------------------------------- |
| Config language           | YAML (declarative, tooling-friendly)               | HCL (Terraform-native)                       |
| Approach                  | Generate complete Terraform, then run it           | Wrap Terraform with inheritance and includes |
| Secret management         | Built-in (Key Vault, Bitwarden, env vars)          | External (sops, vault, env vars)             |
| Drift detection           | `strata build plan` (built-in)                     | Manual `terraform plan` per module           |
| Multi-stage orchestration | Deployment stages with dependency ordering         | `run-all` with dependency blocks             |
| Escape hatch              | Copy generated `.tf` files, run Terraform directly | Already Terraform — remove `terragrunt.hcl`  |
| Learning curve            | Know YAML + understand the layering model          | Know HCL + Terragrunt's inheritance model    |

**Choose strata when:**
- You want a clean separation between config (YAML) and infrastructure code (Terraform modules).
- You need integrated secret resolution from multiple backends.
- You want `build plan` as a first-class operation across environments.
- Your team prefers YAML over HCL for configuration.

**Choose Terragrunt when:**
- Your team lives in HCL and doesn't want to learn a new config format.
- You want to stay as close to raw Terraform as possible.
- You have a single state backend and simple secret management.

---

## Why not Ansible?

Ansible is a configuration management tool. strata is an infrastructure deployment orchestrator. They solve different problems and work well together.

| Aspect          | strata                                             | Ansible                                 |
| --------------- | -------------------------------------------------- | --------------------------------------- |
| Primary purpose | Generate + deploy infrastructure (Terraform, Helm) | Configure servers + deploy applications |
| When it runs    | Before infrastructure exists (or to change it)     | After infrastructure exists             |
| State           | Terraform state (remote backend)                   | Stateless (or Tower/AWX for tracking)   |
| Idempotency     | Via Terraform's state diffing                      | Via module design (task-level)          |

**They complement each other:** strata provisions the VMs and networking; Ansible configures the OS, installs packages, and deploys applications. A deployment can sequence a Terraform infrastructure stage followed by an Ansible configuration stage using `depends_on`.

---

## Why YAML and not HCL?

Three reasons:

1. **Separation of concerns.** HCL is great for describing infrastructure resources (your Terraform modules). YAML is great for describing configuration data (what to deploy, where, with what values). Mixing both in HCL blurs the line between "infrastructure code" and "deployment config."

2. **Tooling.** YAML is parseable by every language, every CI system, and every editor without special plugins. Linting, diffing, templating, and programmatic generation are trivial. HCL tooling exists but is Terraform-specific.

3. **Operator accessibility.** Not everyone who needs to change a variable value or add an environment should need to understand HCL syntax, Terraform internals, or provider schemas. YAML with documented fields is approachable.

---

## Can I use this with existing Terraform state?

**Yes.** strata generates standard Terraform files that point to YOUR state backend. It never creates, manages, or migrates state itself. Point the backend config at your existing state storage and strata will generate `.tf` files that connect to it.

See [config-faq.md](config-faq.md) for the step-by-step adoption workflow.

---

## Can I mix provisioners?

**Yes.** A deployment is a sequence of stages, each backed by a named provisioner from the workspace. You can chain Terraform → Ansible → Helm in a single deployment YAML using `depends_on`. strata orchestrates the order; each tool runs as a subprocess and behaves exactly as it normally would.

See [config-faq.md](config-faq.md) for a YAML example.

---

## Does strata support multiple clouds?

**Yes.** A workspace can reference multiple providers (AWS, Azure, Kamatera, etc.) and each topology component maps to a specific provider. The generated Terraform includes the correct provider configurations.

Practical limit: your Terraform modules need to support the target cloud. strata handles the config layering and orchestration — the actual cloud API calls happen in Terraform.

---

## What happens if I delete `.strata/`?

Nothing catastrophic:

- **`.strata/solution.json`** — workspace registry. Recreated by `strata sln init`.
- **`.strata/cli.yaml`** — your CLI preferences. Recreated by `strata config set`.
- **`.strata/build/`** — generated Terraform artifacts. Recreated by `strata build run`.

Your Terraform state lives in your remote backend (Azure Storage, S3, GCS). Your infrastructure is unaffected. Your source YAML files are in git. The `.strata/` directory is local workspace state — fully regenerable.

---

## How do I roll back a deployment?

strata doesn't have a dedicated `rollback` command because the underlying model is declarative. Revert the breaking commit in git, then rebuild and redeploy — Terraform's plan will show the resources reverting to their previous state. This is safer than imperative rollback because you can review exactly what will change before applying.

See [config-faq.md](config-faq.md) for the exact commands.

---

## Can multiple people deploy at the same time?

State locking is handled by Terraform's backend (e.g., Azure Blob lease, S3 DynamoDB lock, Terraform Cloud). strata doesn't add its own locking.

**Best practice:** Deploy through CI/CD pipelines (one pipeline per deployment), not from developer laptops. This gives you:
- Serialized deployments (pipeline queue)
- Audit trail (CI logs)
- Approval gates (environment protection rules)
