# Multi-Repository Setup

> **Audience:** DevOps teams managing infrastructure-as-code across multiple repositories.
> You have separate repos for Terraform modules, Ansible playbooks, Helm charts, and deployment
> configs. You want to understand how to register, pin versions, and synchronize them in CI/CD.

Multi-repo is a core strata capability. Instead of one monorepo, you register multiple repositories
and reference them with `@repo_name/path` syntax. This guide covers the practical workflow.

---

## Why Multi-Repo?

**Single repo problems:**
- Terraform modules, Ansible playbooks, and deployment configs all change at different cadences
- Large monorepos are slow to clone and build
- Different teams own different repos (infrastructure, platform, config)
- Versioning is implicit (what commit do we use?)

**Multi-repo solution:**
- Each repo has its own change cadence, versioning, and ownership
- Deployment configs explicitly reference which version to use per repo
- Repositories are cloned once, reused across multiple deployments
- Version pinning is environment-specific (dev uses main, prd uses tagged releases)

---

## Repository Registration

### Registering a repository

Register a repository once per workspace. Two types: **remote git** and **local**.

#### Remote git repository

```bash
strata repo add infra https://github.com/org/xyz-infrastructure.git --branch main --clone
```

| Option     | Default | Purpose                                                        |
| ---------- | ------- | -------------------------------------------------------------- |
| `--branch` | `main`  | Default branch to track                                        |
| `--clone`  | false   | Clone immediately after registering (otherwise: register only) |

**Auto-detection:** Remote URLs are detected by prefix (`https://`, `git@`, etc.) and registered
as type `gitops`. The repository is cloned to `.strata/repos/infra/` by default.

#### Local repository (already on disk)

```bash
strata repo add platform C:/repos/xyz-platform
```

Or with explicit type:

```bash
strata repo add platform C:/repos/xyz-platform --type local
```

**When to use:** Shared repos already cloned to the developer machine, or monorepo split across
multiple local folders.

### Listing registered repositories

```bash
strata repo list
strata repo list --name infra    # single repo
```

Output:

```
Repositories registered in workspace: my-platform

  ✅ infra     (git)    https://github.com/org/xyz-infrastructure.git
     Status:  cloned at C:\repos\xyz-config\.strata\repos\infra
     Branch:  main
     Behind:  0

  ✅ platform (git)    https://github.com/org/xyz-platform-modules.git
     Status:  cloned at C:\repos\xyz-config\.strata\repos\platform
     Branch:  develop
     Behind:  5 commits

  ✅ shared   (local)  C:\repos\xyz-shared
     Status:  on disk
```

### Syncing repositories

Clone all registered remote repos (or update existing clones):

```bash
strata repo sync
strata repo sync --name infra                  # single repo
strata repo sync --name infra --force          # hard-reset if dirty
```

`--force` hard-resets the working tree if there are uncommitted changes. Use with caution.

### Removing a repository

```bash
strata repo remove infra
strata repo remove infra --purge               # also delete the clone
```

---

## Repository Status and Version Tracking

### Check repository status

```bash
strata repo status
strata repo status --name infra
strata repo status --output json
```

Output:

```
Repository Status
─────────────────

  ✅ infra  [main → origin/main]
     Tags:
       Release: v2.3.0 (7 days ago, abc1234)
       Quality: tested (2 days ago, def5678)
     Ahead: 0, Behind: 2

  ✅ platform  [develop → origin/develop]
     Tags:
       Release: v1.5.0 (21 days ago, ghi9abc)
       Quality: tested-prd (1 day ago, jkl2def)
     Ahead: 3, Behind: 0
```

Key columns:
- **[branch → remote/branch]** — your local branch and what it's tracking
- **Release tag** — latest semver tag (used for pinning versions)
- **Quality tag** — optional deployment gate tag (e.g., "tested", "approved")
- **Ahead/Behind** — commit count vs. tracking remote

---

## Referencing Repositories in YAML

### The `@repo_name` syntax

Reference files in registered repos using `@repo_name/path` notation:

```yaml
# deployments/deploy-prd.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment

spec:
  workspace:
    name: platform
    source:
      repository: platform              # reference the repo by name
      source_path: workspace.yaml       # relative path within the repo

  provisioners:
    - name: terraform
      provisioner: terraform
      source:
        repository: infra               # different repo for Terraform
        source_path: terraform/prod

  environment:
    - @infra/environments/prd.yaml      # environment file from infra repo
    - @shared/common/variables.yaml     # shared variables from another repo
```

### Resolution at build time

When you run `strata build run -f deployments/deploy-prd.yaml`, strata:

1. Looks up `infra` in `solution.spec.repositories`
2. Finds the local clone path: `.strata/repos/infra/`
3. Resolves `terraform/prod` relative to that path
4. Generates build artifacts with the resolved files

### Benefits

- **Decoupled updates:** Update the Terraform repo without touching deployment configs
- **Version pinning:** Specify which version each env uses (see below)
- **Re-use:** Share common files across multiple deployments
- **Security:** Separate access controls per repository (git permissions)

---

## Version Pinning and Promotion

### Pin a repository to a specific version

Use git tags to mark stable versions. In your environment config, pin the tag:

```yaml
# environments/env-prd.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment

spec:
  repositories:
    - name: infra
      version: v2.3.0                   # pin to tagged release
    - name: platform
      version: tested-20260620          # pin to quality-gate tag
    - name: shared
      version: main                     # or use branch name (less stable)
```

At build time, strata checks out the specified version (tag or branch) before resolving file paths.

### Promotion workflow

**Development:** Use branches (most recent code)
```yaml
# env-dev.yaml
repositories:
  - name: infra
    version: develop
  - name: platform
    version: develop
```

**Staging:** Use quality-gate tags (tested code)
```yaml
# env-stg.yaml
repositories:
  - name: infra
    version: tested
  - name: platform
    version: approved-for-staging
```

**Production:** Use release tags (versioned, stable)
```yaml
# env-prd.yaml
repositories:
  - name: infra
    version: v2.3.0
  - name: platform
    version: v1.5.0
```

### Tagging strategy

Use this pattern:

| Tag Pattern                  | Meaning                           | When to create                                  |
| ---------------------------- | --------------------------------- | ----------------------------------------------- |
| `vX.Y.Z`                     | Release tag (semantic versioning) | When merging to main, after approval            |
| `tested` / `tested-YYYYMMDD` | Quality gate tag                  | After CI passes (tests, linting, security scan) |
| `approved-for-prod`          | Promotion tag                     | Manual step before deploying to production      |
| `main` or `develop`          | Branch (implicit version)         | Always points to latest code                    |

Example:
```bash
# In your IaC repo CI/CD pipeline:

# After tests pass:
git tag -a tested-$(date +%Y%m%d) -m "Passed automated tests"
git push origin tested-$(date +%Y%m%d)

# After manual approval:
git tag -a v2.3.0 -m "Release v2.3.0"
git push origin v2.3.0

# Or just update the moving tag:
git tag -d tested && git tag -a tested -m "Latest tested build"
git push -f origin tested
```

---

## CI/CD Integration

### Syncing repositories in your pipeline

Before deploying, sync all registered repos to the latest version:

```bash
# GitHub Actions
- name: Sync repositories
  run: strata repo sync

- name: Deploy
  run: strata deploy run -f deployments/deploy-prd.yaml --force
```

```bash
# Azure Pipelines
- script: strata repo sync
  displayName: Sync repositories

- script: strata deploy run -f deployments/deploy-prd.yaml --force
  displayName: Deploy
```

### Checking out a specific repository version

If your deployment pins a version, strata checks it out automatically. But you can also force it
in CI/CD:

```bash
# Explicit checkout (useful for debugging or manual promotion)
cd .strata/repos/infra
git checkout v2.3.0                    # checkout the pinned version

cd - && strata deploy run -f deployments/deploy-prd.yaml
```

### Example: Multi-stage promotion pipeline

```yaml
# azure-pipelines.yml
trigger:
  - main

stages:
  - stage: Build
    jobs:
      - job: BuildAndTest
        steps:
          - script: strata build run -f deployments/deploy-dev.yaml
          - script: strata build sbom -f deployments/deploy-dev.yaml --audit --fail-on HIGH

  - stage: DeployDev
    dependsOn: Build
    jobs:
      - job: Deploy
        steps:
          - script: strata repo sync
          - script: strata deploy run -f deployments/deploy-dev.yaml --force

  - stage: DeployStaging
    dependsOn: DeployDev
    jobs:
      - deployment: DeployToStaging
        environment: staging
        strategy:
          runOnce:
            deploy:
              - script: strata repo sync
              - script: strata deploy run -f deployments/deploy-stg.yaml --force

  - stage: DeployProduction
    dependsOn: DeployStaging
    jobs:
      - deployment: DeployToProduction
        environment: production
        strategy:
          runOnce:
            preDeploy:
              - script: strata deploy run -f deployments/deploy-prd.yaml --dry-run
            deploy:
              - script: strata repo sync
              - script: strata deploy run -f deployments/deploy-prd.yaml --force
```

### Detecting version updates

Monitor if production is behind the latest release:

```bash
# In CI/CD (e.g., daily check):
strata repo status --output json | \
  jq '.repos[] | select(.name == "infra") | .tags.latest_release'
```

If the current production pin is older than the latest release tag, you have an update available.

---

## Handling Repository Changes

### Adding a new file to a repository

1. Add the file to the git repo:
   ```bash
   cd .strata/repos/infra
   git add terraform/new-module.tf
   git commit -m "Add new module"
   git push
   ```

2. Reference it in your deployment config:
   ```yaml
   # deployments/deploy-prd.yaml
   spec:
     provisioners:
       - name: terraform
         source:
           repository: infra
           source_path: terraform        # now includes new-module.tf
   ```

3. Sync and deploy:
   ```bash
   strata repo sync
   strata deploy run -f deployments/deploy-prd.yaml --force
   ```

### Updating a repository (switching branch or tag)

Change the version in your environment config:

```yaml
# environments/env-prd.yaml
spec:
  repositories:
    - name: infra
      version: v2.3.0  →  v2.4.0       # update to newer release
```

Then:
```bash
strata repo sync
strata deploy run -f deployments/deploy-prd.yaml --force
```

strata will check out the new version and regenerate artifacts.

### Rolling back to a previous repository version

Revert the version pin in your environment config:

```yaml
# Revert: v2.4.0 → v2.3.0
repositories:
  - name: infra
    version: v2.3.0
```

Then:
```bash
strata repo sync
strata deploy run -f deployments/deploy-prd.yaml --force
```

Terraform will detect the change and roll back the infrastructure.

---

## Multi-Repo Workflows

### Workflow 1: Shared modules, separate deployments

**Repository structure:**
```
xyz-platform-modules/          (remote repo)
  - terraform/
      - modules/
      - vpc.tf
      - compute.tf

xyz-deployments/               (local, has .strata/)
  - deployments/
      - deploy-dev.yaml
      - deploy-prd.yaml
  - environments/
      - env-dev.yaml
      - env-prd.yaml
```

**Setup:**
```bash
cd xyz-deployments
strata sln init my-platform
strata repo add modules https://github.com/org/xyz-platform-modules.git --clone
```

**Reference modules:**
```yaml
# deployments/deploy-prd.yaml
spec:
  workspace:
    source:
      repository: modules
      source_path: terraform
```

### Workflow 2: Monorepo split across multiple working directories

Your team has a monorepo but wants to work with subdirectories as separate repos locally:

**Repository structure:**
```
xyz-monorepo/
  - terraform/
  - ansible/
  - helm/
```

**Setup (on each developer machine):**
```bash
cd xyz-deployments
strata sln init my-platform

# Register local subdirectories as separate repos
strata repo add terraform /path/to/xyz-monorepo/terraform --type local
strata repo add ansible /path/to/xyz-monorepo/ansible --type local
strata repo add helm /path/to/xyz-monorepo/helm --type local
```

**Reference them:**
```yaml
spec:
  provisioners:
    - name: terraform
      source:
        repository: terraform
        source_path: .              # root of terraform/ subdir

    - name: ansible
      source:
        repository: ansible
        source_path: playbooks
```

### Workflow 3: Enterprise setup with central module registry

**Repository structure:**
```
org/xyz-terraform-modules (public or private)
  - modules/
      - vpc/
      - compute/
      - database/

team-a/xyz-deployments
  - deployments/
      - deploy-a-prd.yaml

team-b/xyz-deployments
  - deployments/
      - deploy-b-prd.yaml
```

**Team A setup:**
```bash
cd team-a/xyz-deployments
strata repo add org-modules https://github.com/org/xyz-terraform-modules.git --clone
```

**Team B setup:**
```bash
cd team-b/xyz-deployments
strata repo add org-modules https://github.com/org/xyz-terraform-modules.git --clone
```

Both teams use the same central module registry, but each team's deployment is independent.

---

## Troubleshooting

### "Repository not found" error

```
Error: Repository 'infra' not registered
```

**Check:**
```bash
strata repo list
```

Register it if missing:
```bash
strata repo add infra https://github.com/org/xyz-infrastructure.git --clone
```

### "Reference not resolved" error

```
Error: @infra/terraform/main.tf not found
```

**Check:**
1. The file actually exists in the repo:
   ```bash
   ls -la .strata/repos/infra/terraform/main.tf
   ```

2. The repository is synced to the correct version:
   ```bash
   cd .strata/repos/infra && git log -1
   ```

3. The path in your YAML is correct (relative to the repo root, not to source_path)

### Repository is dirty (uncommitted changes)

If a repository has uncommitted changes and you run `strata repo sync --force`, it will hard-reset:

```bash
cd .strata/repos/infra
git status                         # see uncommitted changes

# Option 1: Stash changes locally
git stash

# Option 2: Let strata hard-reset
strata repo sync --name infra --force
```

### Slow sync (large repositories)

If syncing takes too long:

1. **Use shallow clones** (repo registration):
   ```bash
   git clone --depth 1 <url>
   ```
   Currently not exposed in strata CLI, but you can pre-clone manually and register as local.

2. **Use a GitHub personal token** (faster auth):
   ```bash
   git config credential.helper store
   echo "https://user:token@github.com" > ~/.git-credentials
   strata repo sync
   ```

---

## Best Practices

1. **Register repos once per workspace** — they're shared across all deployments
2. **Use semantic versioning** — `vX.Y.Z` tags for releases
3. **Pin production to release tags** — never use branches (too unstable)
4. **Pin staging to quality-gate tags** — use tags like `tested` or `approved-for-staging`
5. **Use branches for development** — `develop` or `feature/*` for dev environments
6. **Tag after CI passes** — automatic step in your CI/CD pipeline
7. **Document the version matrix** — keep a table of which env uses which repo version
8. **Review repo diffs before deploying** — `strata repo status` shows what changed
9. **Test promotion locally first** — use `--dry-run` to verify before promoting to higher envs
10. **Audit repository changes** — integrate `strata repo status` into your compliance dashboard

---

## See Also

- [Environment Composition](./environment-composition.md) — layering configs
- [Commands Reference — repo](../platform/commands.md#repo) — full command documentation
- [Multi-repo setup decision](../decisions/0010-rename-configuration-repositories-to-remotes.md) — design rationale
