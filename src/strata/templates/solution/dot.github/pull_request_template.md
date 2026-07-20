## Change Request Reference

<!-- Link to the change request issue opened before this PR -->
Closes #<!-- issue number -->
Work item: <!-- ORG-1234, CAB-567, or equivalent ticket reference -->

---

## What changed

<!-- Specific YAML files, resources, and settings modified. Include before/after values. -->

| File                     | Setting                | Before             | After              |
| ------------------------ | ---------------------- | ------------------ | ------------------ |
| `deploy/deploy-prd.yaml` | <!-- e.g. replicas --> | <!-- old value --> | <!-- new value --> |

---

## Why (business justification)

<!-- Business reason for this change. Required for ISO 27001 / ISAE 3402 compliance. -->

---

## Risk level

<!-- Delete the lines that do not apply -->
- [ ] **Low** — cosmetic or additive change, no service impact expected
- [ ] **Medium** — configuration change, minor service impact possible
- [ ] **High** — core infrastructure change, service disruption possible

---

## Rollback plan

<!-- Step-by-step instructions to revert this change if the deployment fails -->

1. Revert PR: `git revert <merge-commit>`
2. Re-deploy: `strata deploy run -f <deployment.yaml>`
3. Verify: `strata deploy health -f <deployment.yaml>`

---

## Pre-merge checklist (author)

- [ ] Change request issue linked above
- [ ] `strata validate run -f <deployment.yaml>` passes
- [ ] Tested in a lower environment first (or N/A for low-risk changes)
- [ ] Rollback plan documented and verified

## Reviewer checklist

- [ ] Change matches the linked change request
- [ ] Risk level is correctly assessed
- [ ] Rollback plan is adequate
- [ ] Approved: I accept responsibility for this change going to production
