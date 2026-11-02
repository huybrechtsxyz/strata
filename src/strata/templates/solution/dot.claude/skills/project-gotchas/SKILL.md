---
name: project-gotchas
description: Known pitfalls and hard-won lessons for this repository's tooling (cloud, Terraform, Helm, pipelines, strata). Use before changing infrastructure configuration or pipelines, and add to it when something surprising breaks.
---

# Project gotchas

This file belongs to the team: strata created it once and never changes it. The strata CLI's own pitfalls live in
the `strata` skill, which strata keeps current.

Add one entry per lesson, newest first. Keep each entry short:

- **Symptom:** what you saw (the error text, or what silently went wrong).
- **Cause:** why it happened.
- **Fix:** what to do instead.

## Entries

<!-- Example:
- **Symptom:** `terraform init` fails with `ContainerNotFound`.
  **Cause:** the state container is `tfstate-platform`, not `tfstate`.
  **Fix:** set `tf_state_container_name: tfstate-platform`.
-->
