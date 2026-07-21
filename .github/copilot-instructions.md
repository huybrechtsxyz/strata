# GitHub Copilot Instructions

This is a **strata** infrastructure workspace — a collection of YAML configuration files that describe deployments, modules, environments, and infrastructure resources.

## Essential Skills

Before working in this workspace, read the domain skills (available in `.github/skills/`):

1. **strata-cli-workflows** — Command structure, exit codes, JSON output, dry-run patterns
   - When: Every CLI operation
   
2. **strata-yaml-schema-and-kinds** — Document structure, valid kinds, schema constraints
   - When: Writing/modifying YAML
   
3. **strata-deployment-lifecycle** — Validate → Build → Deploy workflow, stages, provisioners
   - When: Orchestrating deployments
   
4. **strata-secret-resolution-patterns** — Secrets, secret stores, SSH key lifecycle
   - When: Handling credentials
   
5. **strata-terraform-ansible-provisioning** — Terraform + Ansible, stage orchestration
   - When: Infrastructure provisioning

---

## Key facts

- All YAML files use `apiVersion: strata.huybrechts.xyz/v1` and a `kind` field.
- Valid kinds: `workspace`, `configuration`, `deployment`, `namespace`, `module`, `environment`, `provider`, `resource`, `firewall`, `network`, `dns`, `tenant`.
- The `meta.name` field must be lowercase, matching `^[a-z0-9][a-z0-9_-]*$`.
- Cross-repo references use `@repo_name/relative/path.yaml` syntax.
- Models use `extra="forbid"` — any unknown field causes a validation error. Only use fields that exist in the strata schema.
- Secrets are never written as plain values — use `secret: <KEY_NAME>` references.

## Deployment stages

Stages use `provisioner: <name>` or `topology: <name>` — never a `type` field:

```yaml
stages:
  - name: infrastructure
    provisioner: platform_iac
    scope: all
    on_failure: stop
```

## CLI commands

```bash
strata validate <file>           # Validate YAML
strata build run -f <deploy>     # Build artifacts
strata deploy run -f <deploy>    # Deploy
strata guide show                # Workspace readiness checklist
strata schema get <kind>         # Show schema for a kind
```

Always validate before building. Always dry-run before deploying.
