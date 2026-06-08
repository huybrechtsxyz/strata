# Frequently Asked Questions

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

## How do I provide an SSH key for Ansible without putting it in the YAML?

Store the private key in your secret store (Bitwarden, HashiCorp Vault, Azure Key Vault, etc.) and reference it by name in `configuration.ssh_private_key_secret`. The key never appears in any YAML file — it is resolved into memory at deploy time and written to a `chmod 600` temp file that is deleted when the step completes.

**1. Store the key in Bitwarden (or any other supported store):**

Keep the full PEM content (from `-----BEGIN OPENSSH PRIVATE KEY-----` to `-----END OPENSSH PRIVATE KEY-----`) as a secure note or password field in your secret store. Note the item ID.

**2. Reference it in the workspace YAML:**

```yaml
spec:
  provisioners:
    - name: haven_init
      provisioner: ansible
      source:
        repository: haven
        source_path: ansible
      configuration:
        playbook: site.yml
        ssh_private_key_secret: haven_ssh_key   # name used to look up the key at runtime
  secrets:
    - key: haven_ssh_key
      source: bitwarden
      value: b2f90a12-3c4d-5678-abcd-ef1234567890   # Bitwarden item ID
```

**3. What happens at deploy time:**

```
strata deploy run
  └─ resolves secrets from Bitwarden into memory (ResolvedValues)
  └─ AnsibleDeployer looks up resolved_values.secrets["haven_ssh_key"]
  └─ writes PEM to /tmp/ansible_ssh_XXXXX.pem  (chmod 600)
  └─ ansible-playbook site.yml --private-key /tmp/ansible_ssh_XXXXX.pem
  └─ temp file deleted (even if the playbook fails)
```

The key is never written to disk except during the subprocess window, and never logged.

**Host key checking:** Because the runner has no prior trust with freshly provisioned VMs, set `ANSIBLE_HOST_KEY_CHECKING=false` in your CI environment (or in `ansible.cfg`). For fixed infrastructure with stable IPs, populate `~/.ssh/known_hosts` via `ssh-keyscan` and remove the env var.

---

## Why not Ansible?

Ansible is a configuration management tool. strata is an infrastructure deployment orchestrator. They solve different problems and work well together.

| Aspect          | strata                                             | Ansible                                 |
| --------------- | -------------------------------------------------- | --------------------------------------- |
| Primary purpose | Generate + deploy infrastructure (Terraform, Helm) | Configure servers + deploy applications |
| When it runs    | Before infrastructure exists (or to change it)     | After infrastructure exists             |
| State           | Terraform state (remote backend)                   | Stateless (or Tower/AWX for tracking)   |
| Idempotency     | Via Terraform's state diffing                      | Via module design (task-level)          |

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
