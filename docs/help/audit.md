# Audit Trail and SIEM Integration

Record every deployment action and route domain events to security/compliance systems.

Every deployment execution (build, plan, apply, failure, gate, approval) is recorded
locally in the deploy-log, and select events are additionally routed to configured
SIEM sinks (Azure Sentinel, Splunk, ELK, or any custom integration with the `audit`
capability) for compliance, alerting, and real-time dashboards.

See ADR-0066 for the audit event routing and policy model; ADR-0018 for deploy-log
design; ADR-0057 for work-item events.

---

## `spec.audit` — one configuration location

Everything audit-related is configured under `spec.audit` in `configuration.yaml`:

```yaml
spec:
  audit:
    journal:
      path: .strata/audit.log      # local NDJSON invocation log (who ran what, when)
      rotation: size                # "size" (default) or "daily"
      max_bytes: 5242880
      backup_count: 3

    deploy_log_path: .strata/deploy-log   # local deploy-log directory (ADR-0018)
    structure: by-execution                # flat, by-stage, by-execution, by-date, ...

    policy:
      events:
        command.executed: false     # off by default — high volume, no signal
        secret.accessed: true

    sinks:
      - name: sentinel
        integration: sentinel        # references configuration.spec.integrations[].name
      - name: my-webhook
        integration: my-webhook
        events: [policy.violated, drift.detected]   # optional filter
```

There are three independent sub-sections:

| Section   | Answers                                             |
| --------- | --------------------------------------------------- |
| `journal` | Where is the local CLI-invocation log written?      |
| `policy`  | Which event types are audited at all (the gate)?    |
| `sinks`   | Where do audited events get additionally forwarded? |

`deploy_log_path`/`structure` configure the separate deploy-log (full deployment
records, ADR-0018) — distinct from the `journal` (lightweight per-invocation entries).

---

## The journal — `spec.audit.journal`

The journal is the NDJSON log every CLI invocation writes to via `logger.audit.audit()`.
Its configuration is resolved with a fixed precedence, so exactly one place wins:

```
spec.audit.journal            (primary — shared, committed)
      ↓ overridden by
.strata/logging.yaml → audit: (machine-local escape hatch, e.g. a developer's own path)
      ↓ falls back to
built-in defaults              (.strata/audit.log, 5 MB × 3 backups)
```

Run `strata audit status` to see which layer is currently in effect.

---

## The policy gate — `spec.audit.policy.events`

A closed set of event types, each with a class-aware default. Set explicitly to
override; a type left unset keeps its default. An unrecognised key is a validation
error naming the closest valid option — there is no silent typo.

| Event type             | Default | Class      | CloudEvents `type` (wire)      | Producer                                                                    |
| ---------------------- | ------- | ---------- | ------------------------------ | --------------------------------------------------------------------------- |
| `command.executed`     | off     | Invocation | `…strata.command.executed`     | every CLI command                                                           |
| `deployment.completed` | on      | Outcome    | `…strata.deployment.completed` | `deploy run`                                                                |
| `deployment.destroyed` | on      | Outcome    | `…strata.deployment.destroyed` | `deploy destroy`                                                            |
| `deployment.measured`  | on      | Outcome    | `…strata.deployment.measured`  | not yet wired (ADR-0064 metrics record)                                     |
| `build.completed`      | off     | Outcome    | `…strata.build.completed`      | not yet wired                                                               |
| `validation.completed` | off     | Outcome    | `…strata.validation.completed` | not yet wired                                                               |
| `workitem.created`     | on      | Outcome    | `…strata.workitem.created`     | `deploy run` (gate/approval created)                                        |
| `workitem.resumed`     | on      | Outcome    | `…strata.workitem.resumed`     | `deploy run` (gate/approval resumed)                                        |
| `workitem.approved`    | on      | Outcome    | `…strata.workitem.approved`    | `workitem approve` (gate approved)                                          |
| `workitem.rejected`    | on      | Outcome    | `…strata.workitem.rejected`    | `workitem reject` (gate rejected)                                           |
| `workitem.completed`   | on      | Outcome    | `…strata.workitem.completed`   | `workitem complete` (gate completed)                                        |
| `workitem.cancelled`   | on      | Outcome    | `…strata.workitem.cancelled`   | `workitem cancel` / scheduled-gate window miss                              |
| `policy.violated`      | on      | Domain     | `…strata.policy.violated`      | `validate` / `build` / `deploy` / `check_policy` (any failed policy result) |
| `secret.accessed`      | on      | Domain     | `…strata.secret.accessed`      | deliberately not wired — see note below                                     |
| `lock.acquired`        | off     | Domain     | `…strata.lock.acquired`        | `deploy run` / `deploy destroy`                                             |
| `lock.released`        | off     | Domain     | `…strata.lock.released`        | `deploy run` / `deploy destroy`                                             |
| `drift.detected`       | on      | Domain     | `…strata.drift.detected`       | `deploy drift`                                                              |

"Not yet wired" means the event type, gate, and envelope all exist and work —
there is simply no producer calling `AuditController.forward()` for it yet. Setting
its policy to `true` has no observable effect until a producer is added.

`secret.accessed` is different: it isn't blocked by anything, it's a deliberate
choice not to wire it. Secret stores (Vault, Key Vault, Bitwarden) already produce
more rigorous native audit trails than strata could add, the only value strata's
own event would contribute is correlating an access to a specific `execution_id`,
and the one available hook point (`ValueController.resolve_values()`) is shared by
real deploys *and* read-only inspection commands (`deploy values list`/`get`/`show`)
with no way to tell them apart there — wiring it would reproduce the same
read-only-command volume problem step 1 fixed for `command.executed`. See ADR-0066's
"`secret.accessed` — deliberately not wired" for the full reasoning.

A disabled event type reaches neither the journal-adjacent sink fan-out nor any sink —
the gate is consulted before anything else in `AuditController.forward()`.

Note on `command.executed`: since it defaults to disabled, a plain CLI invocation does
**not** leave a `.strata/audit.log` entry unless `spec.audit.policy.events.command.executed`
is explicitly set to `true` — this applies to the local journal too, not just SIEM
forwarding, matching every other event type's gate. Set it to `true` (with no sink
referencing it) to get a local-only invocation trail without any SIEM traffic.

---

## Sinks — `spec.audit.sinks`

A sink is only a *routing reference* to an integration. All transport configuration
(endpoint, credentials, format) lives on the integration itself, declared once under
`configuration.spec.integrations[]`:

```yaml
spec:
  integrations:
    - name: sentinel
      type: sentinel
      capabilities: [audit]
      endpoints:
        address: https://dce-xxx.westeurope-1.ingest.monitor.azure.com
      authentication:
        method: managed_identity
      properties:
        data_collection_rule_id: dcr-xxx
        stream_name: Custom-DeployAudit_CL

    - name: my-webhook
      type: webhook
      capabilities: [audit]
      endpoints:
        address: https://hooks.example.com/strata
      properties:
        headers:
          Authorization: "Bearer ${WEBHOOK_TOKEN}"

    - name: my-syslog
      type: syslog
      capabilities: [audit]
      endpoints:
        address: collector.example.com:6514
      properties:
        transport: tcp+tls   # udp, tcp, or tcp+tls (default: tcp)

  audit:
    sinks:
      - name: sentinel
        integration: sentinel
      - name: my-webhook
        integration: my-webhook
        events: [policy.violated, drift.detected]   # optional filter
      - name: my-syslog
        integration: my-syslog
        enabled: false
```

`webhook` and `syslog` are built-in integration types — not sink types. There are no
other built-in sink types; any SIEM/webhook/syslog target is an integration.

A sink's `events` filter can only narrow further than the policy gate — it is a
validation error to filter on an event type the gate has already disabled, since
that would silently make the filter believe it re-enables something it cannot.

### This is a clean break from pre-ADR-0066 configuration

Old-shape sinks (`type: webhook`/`ndjson`/`stdout`/`syslog` with `path`/`address`/`url`/
`headers`/`format` fields directly on the sink) and old event names (`deploy_audit`,
`cli_action`, `policy_violation`, `secret_access`, `lock_event`, `validation_result`,
`drift_alert`, `build_event`) are rejected at validation (exit code 3) with the exact
replacement spelled out — there is no silent translation.

---

## The envelope — CloudEvents 1.0 + ECS

Every routed event is wrapped in a [CloudEvents 1.0](https://cloudevents.io/) envelope
with [ECS](https://www.elastic.co/guide/en/ecs/current/index.html)-shaped `data`:

```json
{
  "specversion": "1.0",
  "type": "xyz.huybrechts.strata.deployment.completed",
  "source": "/strata/haven-prd/haven",
  "id": "63f43461-12cd-44c9-a902-77cade548ddd",
  "time": "2026-08-07T09:12:58.041Z",
  "datacontenttype": "application/json",
  "subject": "haven",
  "data": {
    "event": {
      "kind": "event",
      "category": ["configuration"],
      "action": "deployment-completed",
      "outcome": "success",
      "duration": 412000000000
    },
    "user":   { "name": "vhuybrec" },
    "labels": {
      "execution_id": "63f43461-12cd-44c9-a902-77cade548ddd",
      "workspace": "haven-prd",
      "environment": "production",
      "deployment": "haven",
      "tenant": "acme"
    },
    "strata": { }
  }
}
```

`event.kind` does real work: SIEMs route `alert` (policy violations, drift) differently
from `event` (most types) and `metric` (`deployment.measured`). Everything strata-specific
lives under `data.strata`, matching ECS's convention for custom fields.

---

## CLI commands

```bash
strata audit status                              # Effective journal/policy/sink configuration
strata audit changes                              # List recent deployments from the deploy-log
strata audit changes --last 20 --ai                # Summarise trends with an AI integration
strata audit export --last 20 --format ndjson --out deploy-log.ndjson
strata audit export --siem <name>                  # Forward exported entries to a configured sink
strata audit resend --last 50                      # Re-forward deploy-log entries to configured sinks
strata audit diff <execution_id>                   # Diff two deployment executions
```

`strata audit status` reports:

- the journal's effective path/rotation and which layer supplied it,
- every event type in the policy gate and whether it is currently admitted,
- every configured sink, whether it is enabled, and whether its referenced
  integration actually exists.

`strata audit export --siem <name>` requires `<name>` to match an *enabled*
`spec.audit.sinks[].integration` — the same sinks `deploy run` and `strata audit resend`
use. Entries are wrapped in the same CloudEvents 1.0 + ECS envelope as every other path,
and the policy gate applies: if `policy.events.deployment.completed` is disabled, or the
sink's own `events` filter excludes it, the export is skipped (not an error) with a
message explaining why.

If `.strata/sbom-ignore.yaml` declares any rules, `--siem <name>` also forwards them
as a second, `sbom_ignore_rules`-typed batch to the same resolved sink — the same
integration instance, the same envelope shape, and a failure here now surfaces as an
error and fails the command too, rather than being silently best-effort.

---

## What this enables

✅ Compliance audit trail (who deployed what, when, why)
✅ Real-time alerts (policy violations, drift, deploy failures) routed as CloudEvents `alert`s
✅ Dashboards (deployment success rate, approval latency, policy violations) via ECS fields
✅ Forensics (when did production change? who approved it? what changed?)
✅ One configuration location, one event vocabulary, one envelope shape across every sink

