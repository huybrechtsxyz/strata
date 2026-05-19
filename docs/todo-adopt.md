# Adoption Readiness Checklist

Target: a DevOps engineer can install, run, and trust `strata` without needing the author present.

---

## 1. The Pitch (one sentence)

> ✅ README.md updated with compelling pitch and Quick Start section.

Before anything else, answer this clearly — in the README, in the first line of the docs:

> "strata manages the full lifecycle of your Azure/AKS environments — one consistent YAML config layer over Terraform, Helm, and scripts — so you stop copy-pasting between 8 near-identical environment folders."

Without a clear answer to "why not just Terraform?", every DevOps engineer will bounce.

---

## 2. Onboarding (10-minute rule)

A new team member should be able to go from zero to a validated config without asking anyone.

- [ ] `strata init` scaffolds a working example config (AKS-flavored template)
- [ ] `strata init --template aks` for Azure-specific scaffold
- [ ] Output tells you exactly what was created and what to do next
- [x] A single "Getting Started" page: install → init → validate → deploy to sandbox → `docs/platform/getting-started.md`
- [x] Document the prerequisites clearly (Python version, uv, Azure CLI, Terraform)

---

## 3. Trust-Building Commands

These are the commands that turn skeptics into believers.

- [ ] **`strata diff`** — show what *would* change in the environment before deploying
  - Most important missing feature for team adoption
  - Without it, `strata deploy` feels like flying blind
- [ ] **`strata validate`** — already exists, but:
  - [ ] Error messages must say *what to fix*, not just *what is wrong*
  - [ ] `--dry-run` should work on `deploy` and `build` too, not just validate
- [ ] **`strata status`** — show current state of each environment (deployed / drifted / unknown)

---

## 4. Transparency (don't hide the plumbing)

DevOps engineers debug at 2am. They need to see what strata is actually doing.

- [ ] When strata runs Terraform, stream Terraform output to the terminal (not swallowed)
- [ ] When strata runs Helm or scripts, same — full passthrough visible
- [ ] `strata audit list --last` is good — make it prominent in the docs
- [ ] Add a `--verbose` mode that shows every subprocess call with full args
- [ ] Long-running operations (`deploy run`, `build run`) need progress output, not silence
  - See also: `todo.md` item 1 — NDJSON streaming mode

---

## 5. The Escape Hatch

Counter-intuitively, documenting how to leave *increases* adoption.

- [ ] Document: "If strata doesn't work for your case, here's your Terraform — take it and go"
- [ ] Make clear that strata does not generate Terraform state — state is standard, portable
- [ ] Show the file layout so someone can understand what strata touches vs. what it doesn't

---

## 6. Error Messages

The difference between a tool people tolerate and one they trust is error quality.

- [ ] Every validation error must include: what file, what field, what the valid options are
- [ ] Cross-reference errors (e.g. unknown `@repo/path`) must show the resolved path it tried
- [ ] On crash (exit code 1), `messages` must tell you enough to file a bug or fix your config
- [ ] Review all `ModelValidationError` output — is it actionable without reading source code?

---

## 7. CI Integration

Teams adopt tools that fit into their existing PR flow.

- [ ] Document: how to run `strata validate` as a PR gate in GitHub Actions / Azure Pipelines
- [ ] Provide a sample pipeline snippet (GitHub Actions YAML)
- [ ] Exit codes are already well-defined (0/1/2/3) — document them prominently
- [ ] `--output json` mode for machine-readable results in CI — already exists, document it

---

## 8. Documentation Gaps

Current docs are developer/internals-facing. Team docs need to be operator-facing.

- [ ] **"How do I add a new environment?"** — step-by-step cookbook
- [ ] **"How do I change a config value across all environments?"** — pattern guide
- [ ] **"Something broke in production — how do I see what changed?"** — audit trail guide
- [ ] **"How do I add a new AKS cluster?"** — from zero to deployed
- [ ] YAML schema reference with examples for every field (not just type definitions)
- [ ] FAQ: "Why not Terragrunt?", "Why not Ansible?", "Can I use this with existing Terraform state?"

---

## 9. Installation & Editor Experience

- [ ] Single-line install that works on Windows and Linux
  - e.g. `pipx install xyz-strata` or install from internal GitHub releases
- [ ] Document: does it need to be in a virtualenv, or is global install fine?
- [ ] Shell completion (`strata --install-completion`) — nice to have, high perceived polish

### YAML Schema → VS Code autocomplete

Highest-leverage editor improvement. Requires no per-user setup once done.

- [ ] Publish a JSON Schema for the strata YAML document format (`strata-schema.json`)
- [ ] Register it with the [YAML VS Code extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml) via schema URL
- [ ] Result: autocomplete, inline docs, and red squiggles on `spec.` fields in any YAML config — for free
- [ ] Add `.vscode/settings.json` to the config repo pointing at the schema so it works without any user config

### VS Code Tasks in the config repo

- [x] Add `.vscode/tasks.json` to `xyz-configuration` with one-click tasks:
  - [x] `xyz validate` — validate current file
  - [ ] `xyz diff` — preview changes (pending `strata diff` feature)
  - [x] `xyz deploy run` — deploy current environment
  - [x] `xyz build run` — run a build
- [x] These surface in the VS Code Command Palette — no terminal knowledge required

---

## 10. Dev Container

**The single highest-leverage onboarding improvement.** Someone opens the config repo, clicks "Reopen in Container", and has everything working in 2 minutes — no local setup, no "works on my machine."

What it provides automatically:
- Python + `uv` + `strata` at the right versions
- Terraform, Azure CLI, `kubectl`, `helm` pre-installed
- VS Code YAML extension with the strata schema pre-configured
- Shell completion already set up
- `strata doctor` passes out of the box
- Works in GitHub Codespaces (deploy from a browser tab)

Files to create in `xyz-configuration`:

```
.devcontainer/
  devcontainer.json
```

Sample `devcontainer.json`:
```json
{
  "name": "strata",
  "image": "mcr.microsoft.com/devcontainers/python:3.13",
  "features": {
    "ghcr.io/devcontainers/features/azure-cli:1": {},
    "ghcr.io/devcontainers/features/terraform:1": {},
    "ghcr.io/devcontainers/features/kubectl-helm-minikube:1": {}
  },
  "postCreateCommand": "pip install xyz-strata && strata --install-completion bash",
  "customizations": {
    "vscode": {
      "extensions": ["redhat.vscode-yaml"],
      "settings": {
        "yaml.schemas": {
          "https://schema.huybrechts.xyz/strata/v1/schema.json": "**/*.yaml"
        }
      }
    }
  }
}
```

Checklist:
- [x] Create `.devcontainer/devcontainer.json` in `xyz-configuration`
- [ ] Pin tool versions (Terraform, kubectl) to avoid silent upgrades breaking things
- [ ] Handle Azure CLI auth flow inside container (document the `az login --use-device-code` workaround)
- [ ] Test on Windows (Docker Desktop), Mac, and Linux
- [ ] Test in GitHub Codespaces
- [ ] Check corporate Docker Desktop licensing situation before making it the primary path

**Caveat:** In some corporate environments Docker Desktop requires a license or is blocked by IT. Keep native install as a fallback.

---

## 11. `strata doctor`

Eliminates the "it doesn't work and I don't know why" onboarding call.

- [ ] Implement `strata doctor` command that checks:
  - Python version
  - Terraform installed + version
  - Azure CLI installed + logged in + correct subscription
  - `kubectl` installed (if AKS workloads present)
  - `helm` installed (if Helm workloads present)
  - `.strata/` workspace marker found
- [ ] Each check prints ✅ / ❌ with a fix hint on failure
- [ ] Include in the Getting Started page: "Run `strata doctor` first if anything isn't working"

---

---

## 12. Bus Factor

Make the tool survivable without the author.

- [ ] Architecture decision records (why the YAML schema looks like Kubernetes-style)
- [ ] Contribution guide — how to add a new integration, a new command
- [ ] Changelog — what changed between versions, so the team can trust upgrades
- [ ] The rename (see `todo-rename.md`) — `strata` is more memorable and survives "it's Vince's thing"

---

## Priority Order

| #   | Item                                     | Effort | Impact                                      |
| --- | ---------------------------------------- | ------ | ------------------------------------------- |
| 1   | Dev container in config repo             | Low    | Very high — zero setup for new team members | ✅ Done |
| 2   | `strata diff`                              | High   | Very high — removes #1 trust blocker        |
| 3   | Getting Started page (10-min onboarding) | Low    | Very high                                   | ✅ Done |
| 1a  | The Pitch — README.md                    | Low    | Very high                                   | ✅ Done |
| 4   | YAML schema → VS Code autocomplete       | Low    | High — editing config feels first-class     |
| 5   | `strata doctor`                            | Low    | High — eliminates onboarding support calls  |
| 6   | Error message quality pass               | Medium | High                                        |
| 7   | Terraform/subprocess output passthrough  | Low    | High                                        |
| 8   | Operator cookbook docs                   | Medium | High                                        |
| 9   | `strata init --template aks`               | Medium | Medium                                      |
| 10  | CI pipeline snippet                      | Low    | Medium                                      |
| 11  | VS Code tasks in config repo             | Low    | Medium                                      | ✅ Done |
| 12  | `strata status`                            | High   | Medium                                      |
| 13  | Shell completion                         | Low    | Low (polish)                                |
