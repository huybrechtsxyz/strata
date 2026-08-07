# Audit Trail and SIEM Integration

Record every deployment action and send events to security/compliance systems.

Every deployment execution (build, plan, apply, failure, gate, approval) is
recorded in the deploy-log and forwarded to SIEM sinks (Azure Sentinel, Splunk,
ELK) for compliance, alerting, and real-time dashboards.

See ADR-0018 for deploy-log design; ADR-0057 for work-item events.

---

## Configured Under `spec.audit`

The deploy-log is always written locally (`.strata/deploy-log/` by default) — that is not a
sink. Use `deploy_log_path` and `structure` to control it. Sinks are where events are
*additionally* forwarded, and come in two flavours.

**Built-in sinks** carry their own settings under `type:` — `stdout`, `ndjson`, `syslog`,
and `webhook` are the only valid values:

```yaml
spec:
  audit:
    sinks:
      - name: local
        type: ndjson
        path: .strata/audit.ndjson

      - name: alert_hook
        type: webhook
        url: https://hooks.example.com/strata
        headers:
          Authorization: "Bearer ${WEBHOOK_TOKEN}"
        events: [policy_violation]    # optional filter; omit for all events
```

**Integration-backed sinks** (Splunk, Sentinel, ELK, OTel) are declared once in
`configuration.spec.integrations` and referenced by name with `integration:`:

```yaml
# configuration.yaml
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

    - name: splunk
      type: splunk
      capabilities: [audit]
      endpoints:
        address: https://splunk.example.com:8088
      authentication:
        method: api_key
        api_key:
          api_key: SPLUNK_HEC_TOKEN     # env var name holding the token
```

```yaml
# environment YAML
spec:
  audit:
    sinks:
      - name: sentinel
        integration: sentinel
      - name: splunk
        integration: splunk
```

A sink must specify **either** `type` **or** `integration`, never both. Integration-backed
sinks must not carry `endpoints`, `authentication`, or `properties` — those belong to the
integration declaration.

---

## Event Types Emitted

| Event                   | When                                              | SIEM visibility                        |
| ----------------------- | ------------------------------------------------- | -------------------------------------- |
| `deploy_request`        | Deployment begins                                 | User initiates deploy                  |
| `workitem.created`      | Gate triggered (approval, cost, security, verify) | Approval pending                       |
| `workitem.approved`     | Approver signs off                                | Who approved, when                     |
| `workitem.rejected`     | Approver rejects                                  | Who rejected, reason                   |
| `deploy_apply_start`    | Provisioning begins                               | Infra changes starting                 |
| `deploy_apply_complete` | Provisioning succeeds                             | Resources deployed, changes summary    |
| `deploy_apply_failed`   | Provisioning fails                                | Error, root cause, affected stage      |
| `deploy_rollback`       | Rollback triggered                                | What was rolled back, why              |
| `policy_violation`      | Policy fails                                      | What violated, enforcement, suggestion |

---

## SIEM Sinks

| Type             | Service                       | Use case                       |
| ---------------- | ----------------------------- | ------------------------------ |
| `local`          | `.strata/deploy-log/`         | Development, auditing locally  |
| `azure_sentinel` | Microsoft Sentinel            | Azure environments, compliance |
| `splunk`         | Splunk                        | Enterprise SIEM, dashboards    |
| `elk`            | Elasticsearch/Logstash/Kibana | Self-hosted, on-prem           |
| `otel`           | OpenTelemetry                 | Cloud-agnostic observability   |

---

## Querying the Deploy-Log

```bash
strata audit changes                    # List recent deployments
strata audit changes --last 20 --ai     # Summarise trends
strata audit export --last 20 --format ndjson --out deploy-log.ndjson  # Export deploy-log records
```

---

## SIEM Events Enable

✅ Compliance audit trail (who deployed what, when, why)  
✅ Real-time alerts (approval pending, deploy failed, cost spike)  
✅ Dashboards (deployment success rate, approval latency, policy violations)  
✅ Forensics (when did production change? who approved it? what changed?)  
✅ Notifications (email, Teams, PagerDuty via SIEM alert rules)
