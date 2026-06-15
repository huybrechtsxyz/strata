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

### `required_tags`

Evaluates at the `build` phase, after all builders have completed and the platform artifact has been generated.

It checks that every namespace in the built platform artifact has all labels listed in `configuration.required_labels`. Any namespace missing a required label is reported as a violation.

Skipped gracefully when no platform artifact has been generated, or when `required_labels` is not configured.

```yaml
- name: require_standard_labels
  type: required_tags
  phase: build
  enforcement: deny
  configuration:
    required_labels:
      - environment
      - project
      - owner
```

### `naming_pattern`

Evaluates at the `validate` phase, after structural validation completes. Requires the `--deep` flag on `strata validate` to load the configuration service.

It checks whether the `meta.name` field in the loaded configuration file matches the regex in `configuration.pattern`, using a full-string match.

Skipped gracefully when no configuration service has been loaded (i.e. `strata validate` was run without `--deep`).

```yaml
- name: naming_convention
  type: naming_pattern
  phase: validate
  enforcement: warn
  configuration:
    pattern: "^[a-z][a-z0-9-]*$"
```

### `script`

Delegates policy evaluation to an external command — OPA, Checkov, a custom shell script, or any executable that can read JSON from stdin.

strata launches the command, writes a JSON context object to its standard input, and waits for it to exit. Exit code `0` is a pass; any non-zero exit code is a failure. Stdout is captured as the violation message.

**Context sent on stdin:**

```json
{"phase": "plan", "work_path": "/path/to/workspace"}
```

The `script` type can run at any phase — set `phase` to whichever phase you want the evaluation to occur. Timeout defaults to `30` seconds. Skipped gracefully when no `command` is configured.

```yaml
- name: opa_check
  type: script
  phase: plan
  enforcement: deny
  configuration:
    command: "opa eval --data policy.rego --input /dev/stdin -"
    timeout: 30
```

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

### Two policies — zone enforcement + required tags

```yaml
  policies:
    - name: zone_enforcement
      type: customer_zone
      phase: plan
      enforcement: deny
      description: "Block resources outside customer zones"

    - name: require_standard_labels
      type: required_tags
      phase: build
      enforcement: warn
      description: "Warn when required labels are missing"
      configuration:
        required_labels:
          - environment
          - cost_center
```

### Complete example — naming, tags, zone, and OPA

The following configuration shows all four built-in policy types working together on a single deployment. Policies are evaluated in declaration order within each phase.

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: production
spec:
  zones:
    eu-west:
      regions:
        - westeurope
        - northeurope

  policies:
    # validate phase: checked during `strata validate --deep`
    - name: naming_convention
      type: naming_pattern
      phase: validate
      enforcement: warn
      description: "Names must be lowercase, start with a letter"
      configuration:
        pattern: "^[a-z][a-z0-9-]*$"

    # build phase: checked after `strata build run` completes
    - name: require_standard_labels
      type: required_tags
      phase: build
      enforcement: deny
      description: "All namespaces must carry standard cost-tracking labels"
      configuration:
        required_labels:
          - environment
          - project
          - owner

    # plan phase: checked after `terraform plan`, before `terraform apply`
    - name: zone_enforcement
      type: customer_zone
      phase: plan
      enforcement: deny
      description: "Resources must stay within customer-allowed regions"

    # plan phase: delegate to OPA for additional compliance checks
    - name: opa_compliance
      type: script
      phase: plan
      enforcement: deny
      description: "OPA baseline security policy"
      configuration:
        command: "opa eval --data policy.rego --input /dev/stdin -"
        timeout: 30
```

With this setup:
- `strata validate --deep` flags configuration names that don't follow the naming convention.
- `strata build run` stops if any namespace is missing `environment`, `project`, or `owner`.
- `strata deploy run` runs zone enforcement and the OPA check in sequence against the Terraform plan. Both must pass before `terraform apply` is invoked.

---

## See Also

- [Lifecycles](lifecycles.md) — lifecycle hooks for running scripts at named pipeline points
- [Validators](validators.md) — structural validation of individual YAML files
- [Exit Codes](exit-codes.md) — `3` = validation failure (policy `deny` failure)
- [ADR 0006](../decisions/0006-policy-engine-for-deployment-guardrails.md) — design decisions behind the policy engine
