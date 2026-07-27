# Audit Trail and SIEM Integration

Record every deployment action and send events to security/compliance systems.

Every deployment execution (build, plan, apply, failure, gate, approval) is
recorded in the deploy-log and forwarded to SIEM sinks (Azure Sentinel, Splunk,
ELK) for compliance, alerting, and real-time dashboards.

See ADR-0018 for deploy-log design; ADR-0057 for work-item events.

---

## Configured Under `spec.audit`

```yaml
spec:
  audit:
    # Deploy-log storage
    sinks:
      - name: local
        type: local
        # Entries written to .strata/deploy-log/
      - name: sentinel
        type: azure_sentinel
        endpoints:
          address: https://dce-xxx.westeurope-1.ingest.monitor.azure.com
        properties:
          data_collection_rule_id: dcr-xxx
          stream_name: Custom-DeployAudit_CL
        authentication:
          method: azure_cli

      - name: splunk
        type: splunk
        endpoints:
          address: https://splunk.example.com:8088
        authentication:
          method: api_key
          api_key: SPLUNK_HEC_TOKEN
```

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
strata log list                         # Show execution log entries
```

---

## SIEM Events Enable

✅ Compliance audit trail (who deployed what, when, why)  
✅ Real-time alerts (approval pending, deploy failed, cost spike)  
✅ Dashboards (deployment success rate, approval latency, policy violations)  
✅ Forensics (when did production change? who approved it? what changed?)  
✅ Notifications (email, Teams, PagerDuty via SIEM alert rules)
