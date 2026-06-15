# Policies

Declarative guardrails evaluated at specific lifecycle phases before or after infrastructure changes are applied.

**Built-in types:** `customer_zone` | **Phases:** `validate` / `build` / `plan` / `deploy` | **Enforcement:** `deny` / `warn` / `audit` | **Declared in:** `configuration.spec.policies`

---

## Overview

Policies let you declare constraints that strata enforces automatically as part of its standard commands — no extra scripts or wrappers required.

Unlike [lifecycle hooks](lifecycles.md), which run arbitrary scripts at named points in the pipeline, policies evaluate structured data against a rule and produce a **pass / fail result**. The enforcement level determines what happens on failure: block the pipeline, emit a warning, or silently record the result.

| Feature          | Lifecycle hooks                         | Policies                                       |
| ---------------- | --------------------------------------- | ---------------------------------------------- |
| What they do     | Run arbitrary scripts                   | Evaluate structured rules against config/plans |
| Where declared   | `lifecycle:` on any YAML document       | `configuration.spec.policies`                  |
| Failure mode     | Non-zero exit code stops the pipeline   | Configurable: `deny`, `warn`, or `audit`       |
| Scope            | Single document (workspace/namespace/…) | Whole deployment (cross-document visibility)   |
| Example use case | Run a backup before apply               | Prevent resources in disallowed regions        |

The practical difference: if you need to *enforce* something about the infrastructure plan or configuration values, use a policy. If you need to *do* something at a lifecycle point (download a file, rotate a secret, send a notification), use a lifecycle hook.

---

## Configuration

Policies are declared in your `configuration.yaml` under `spec.policies`. Each policy is an item in the list.

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: my-configuration
spec:
  policies:
    - name: zone_enforcement
      type: customer_zone
      phase: plan
      enforcement: deny
      description: "Reject any Terraform resource targeting a disallowed region"
```

### Fields

| Field           | Required | Type    | Description                                                                        |
| --------------- | -------- | ------- | ---------------------------------------------------------------------------------- |
| `name`          | Yes      | string  | Unique identifier for this policy. Used in logs and manifest records.              |
| `type`          | Yes      | string  | Policy implementation to use. See [Policy types](#policy-types).                   |
| `phase`         | Yes      | string  | Lifecycle phase when this policy runs. See [Phases](#phases).                      |
| `enforcement`   | No       | string  | What happens on failure: `deny`, `warn`, or `audit`. Default: `deny`.              |
| `enabled`       | No       | bool    | Set to `false` to temporarily disable without removing the entry. Default: `true`. |
| `description`   | No       | string  | Human-readable note recorded in logs and the deployment manifest.                  |
| `configuration` | No       | mapping | Type-specific settings. Required by some policy types (e.g. `script`).             |

---

## Policy Types

### Built-in types

| Type             | Phase      | What it checks                                                         | Status  |
| ---------------- | ---------- | ---------------------------------------------------------------------- | ------- |
| `customer_zone`  | `plan`     | Terraform resource regions vs. the customer's allowed zones            | Phase 1 |
| `required_tags`  | `build`    | Required tags/labels present on all resources in the platform artifact | Phase 2 |
| `naming_pattern` | `validate` | `meta.name` fields match a configured regex pattern                    | Phase 2 |
| `script`         | any        | Delegates to an external command (OPA, Checkov, custom script)         | Phase 2 |

### `customer_zone`

Evaluates at the `plan` phase, after `terraform plan` produces a plan JSON and before `terraform apply` runs.

It reads:

1. The customer's allowed zones from `customer.auto.tfvars.json` in the working directory.
2. The zone-to-region mapping from `configuration.spec.zones`.
3. Every `create` and `update` resource change in the plan JSON.

For each resource change, it checks whether the target region (from `location` or `region` in the plan) falls within the customer's allowed regions. Any resource targeting a disallowed region is reported as a violation.

No `configuration` block is needed — the policy reads zone data from the configuration file automatically.

---

## Enforcement Levels

| Level   | Behaviour on failure                                                                                          |
| ------- | ------------------------------------------------------------------------------------------------------------- |
| `deny`  | Stops the pipeline immediately. Exit code `3` (validation failure). No apply is run.                          |
| `warn`  | Logs a warning to the console and continues. The pipeline completes normally.                                 |
| `audit` | Records the result in the deployment manifest only. No console output unless `--verbose`. Pipeline continues. |

Use `deny` for hard constraints (compliance, security, data residency). Use `warn` for advisory checks during a rollout period. Use `audit` to collect baseline data before deciding whether to enforce.

---

## Phases

| Phase      | Triggered by           | When it runs                                                         |
| ---------- | ---------------------- | -------------------------------------------------------------------- |
| `validate` | `strata validate -f …` | After Pydantic structural validation and cross-reference checks pass |
| `build`    | `strata build run …`   | After the platform artifact is generated                             |
| `plan`     | `strata deploy run …`  | After `terraform plan`, before `terraform apply`                     |
| `deploy`   | `strata deploy run …`  | After `terraform apply` completes, before the manifest is persisted  |

The `plan` phase has the highest impact: it sits between planning and applying, giving policies access to the full Terraform plan JSON. A `deny` at this phase prevents any infrastructure change from being made.

---

## Example Configuration

### Single policy — zone enforcement

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: production
  annotations:
    description: Production environment configuration
spec:
  zones:
    eu-west:
      regions:
        - westeurope
        - northeurope
    eu-central:
      regions:
        - germanywestcentral
        - swedencentral

  policies:
    - name: zone_enforcement
      type: customer_zone
      phase: plan
      enforcement: deny
      description: "All provisioned resources must be in customer-allowed zones"
```

With this configuration, running `strata deploy run` will evaluate every resource in the Terraform plan against the customer's declared zones. If any resource targets `us-east-1` or another region outside the allowed set, the deploy stops before apply with exit code `3`.

### Two policies — zone enforcement + advisory tags

```yaml
  policies:
    - name: zone_enforcement
      type: customer_zone
      phase: plan
      enforcement: deny
      description: "Block resources outside customer zones"

    - name: required_tags_advisory
      type: required_tags
      phase: build
      enforcement: warn
      description: "Warn when cost_center tag is missing (Phase 2 feature)"
      configuration:
        required:
          - environment
          - cost_center
```

> **Note:** `required_tags` is a Phase 2 feature. The `configuration` block shown above is the intended declaration format; the policy type is not yet implemented.

---

## Script Escape Hatch (Phase 2)

For organisations that already use OPA/Conftest, Checkov, or custom validation scripts, the `script` type delegates policy evaluation to an external command. This is a Phase 2 feature.

```yaml
    - name: opa_security_baseline
      type: script
      phase: plan
      enforcement: deny
      configuration:
        command: "conftest test --policy policies/ --input"
        input: plan_json   # plan_json | platform_json | deployment_yaml
        timeout: 60
```

strata passes the specified input file path as an argument to `command`. Exit code `0` is a pass; any non-zero exit code is a failure. Stdout is captured as the violation message.

---

## See Also

- [Lifecycles](lifecycles.md) — lifecycle hooks for running scripts at named pipeline points
- [Validators](validators.md) — structural validation of individual YAML files
- [Exit Codes](exit-codes.md) — `3` = validation failure (policy `deny` failure)
- [ADR 0006](../decisions/0006-policy-engine-for-deployment-guardrails.md) — design decisions behind the policy engine
