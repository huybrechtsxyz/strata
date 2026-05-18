# Adoption Readiness Checklist

Target: a DevOps engineer can install, run, and trust `ruck` without needing the author present.

---

## 1. The Pitch (one sentence)

Before anything else, answer this clearly — in the README, in the first line of the docs:

> "Ruck manages the full lifecycle of your Azure/AKS environments — one consistent YAML config layer over Terraform, Helm, and scripts — so you stop copy-pasting between 8 near-identical environment folders."

Without a clear answer to "why not just Terraform?", every DevOps engineer will bounce.

---

## 2. Onboarding (10-minute rule)

A new team member should be able to go from zero to a validated config without asking anyone.

- [ ] `ruck init` scaffolds a working example config (AKS-flavored template)
- [ ] `ruck init --template aks` for Azure-specific scaffold
- [ ] Output tells you exactly what was created and what to do next
- [ ] A single "Getting Started" page: install → init → validate → deploy to sandbox
- [ ] Document the prerequisites clearly (Python version, uv, Azure CLI, Terraform)

---

## 3. Trust-Building Commands

These are the commands that turn skeptics into believers.

- [ ] **`ruck diff`** — show what *would* change in the environment before deploying
  - Most important missing feature for team adoption
  - Without it, `ruck deploy` feels like flying blind
- [ ] **`ruck validate`** — already exists, but:
  - [ ] Error messages must say *what to fix*, not just *what is wrong*
  - [ ] `--dry-run` should work on `deploy` and `build` too, not just validate
- [ ] **`ruck status`** — show current state of each environment (deployed / drifted / unknown)

---

## 4. Transparency (don't hide the plumbing)

DevOps engineers debug at 2am. They need to see what ruck is actually doing.

- [ ] When ruck runs Terraform, stream Terraform output to the terminal (not swallowed)
- [ ] When ruck runs Helm or scripts, same — full passthrough visible
- [ ] `ruck audit list --last` is good — make it prominent in the docs
- [ ] Add a `--verbose` mode that shows every subprocess call with full args
- [ ] Long-running operations (`deploy run`, `build run`) need progress output, not silence
  - See also: `todo.md` item 1 — NDJSON streaming mode

---

## 5. The Escape Hatch

Counter-intuitively, documenting how to leave *increases* adoption.

- [ ] Document: "If ruck doesn't work for your case, here's your Terraform — take it and go"
- [ ] Make clear that ruck does not generate Terraform state — state is standard, portable
- [ ] Show the file layout so someone can understand what ruck touches vs. what it doesn't

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

- [ ] Document: how to run `ruck validate` as a PR gate in GitHub Actions / Azure Pipelines
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

## 9. Installation

- [ ] Single-line install that works on Windows and Linux
  - e.g. `pipx install xyz-ruck` or install from internal GitHub releases
- [ ] Document: does it need to be in a virtualenv, or is global install fine?
- [ ] Shell completion (`ruck --install-completion`) — nice to have, high perceived polish

---

## 10. Bus Factor

Make the tool survivable without the author.

- [ ] Architecture decision records (why the YAML schema looks like Kubernetes-style)
- [ ] Contribution guide — how to add a new integration, a new command
- [ ] Changelog — what changed between versions, so the team can trust upgrades
- [ ] The rename (see `todo-rename.md`) — `ruck` is more memorable and survives "it's Vince's thing"

---

## Priority Order

| #   | Item                                     | Effort | Impact                         |
| --- | ---------------------------------------- | ------ | ------------------------------ |
| 1   | `ruck diff`                              | High   | Very high — removes #1 blocker |
| 2   | Getting Started page (10-min onboarding) | Low    | Very high                      |
| 3   | Error message quality pass               | Medium | High                           |
| 4   | Terraform/subprocess output passthrough  | Low    | High                           |
| 5   | Operator cookbook docs                   | Medium | High                           |
| 6   | `ruck init --template aks`               | Medium | Medium                         |
| 7   | CI pipeline snippet                      | Low    | Medium                         |
| 8   | `ruck status`                            | High   | Medium                         |
| 9   | Shell completion                         | Low    | Low (polish)                   |
