# Models Documentation

Pydantic v2 classes validating YAML configs with Kubernetes-inspired schema (`apiVersion`, `kind`, `meta`, `spec`).

**Models:** common (shared types/enums), auth, store (variable/secret/feature entries), firewall (rulesets), configuration, provider (cloud), resource (infra), module, namespace, workspace (topology), platform_artifact (build output), platform_template (scaffold), solution + repository + integration (project structure), deployment, environment (vars/secrets), unknown (fallback)

**Naming:** `^[a-z][a-z0-9_-]*$` (lowercase, alphanumeric, underscores, hyphens)

**Validation:** Phase 1 (static: format, fields, relationships), Phase 2 (dynamic: cross-refs, provider availability via service layer)

**Lifecycle:** clean → check → build → plan → provision → initialize → configure → output → health → protect → destroy

**Security:** Relative paths only (absolute/drive/UNC rejected)