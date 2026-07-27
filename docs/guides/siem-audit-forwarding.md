# SIEM Audit Forwarding

Forward deployment audit logs to external monitoring and compliance systems for real-time alerting, long-term archival, and regulatory evidence.

---

## Overview

Every `strata deploy run` writes a deploy-log entry locally. These entries can be forwarded to a SIEM (Security Information and Event Management) system in two ways:

1. **Automatic forwarding** — configured sinks fire during every deploy (build time)
2. **On-demand forwarding** — `strata audit export --siem <name>` pushes historical entries

**Post-deployment audit pipeline** (all steps are best-effort and never block the deployment):

| Step                   | What happens                                                                                                       | Config required         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------ | ----------------------- |
| **1. Write**           | `_execution.json` written to `.strata/deploy-log/`                                                                 | — (always)              |
| **2. PR enrichment**   | `gh pr list` looks up the merge PR for the commit SHA; if found, PR title/approvers/labels are embedded in the log | `gh` CLI in PATH        |
| **3. SIEM forwarding** | Enriched payload forwarded to all configured sinks                                                                 | `spec.audit.sinks`      |
| **4. Git push**        | Deploy-log committed and pushed to a registered solution repo                                                      | `spec.audit.repository` |

Both paths use the same integration infrastructure. The integration determines the protocol (HTTP, TCP, OTLP) and format (JSON, CEF, HEC).

---

## Supported Backends

| Backend            | Type string | Protocol                           | Best for                                                  |
| ------------------ | ----------- | ---------------------------------- | --------------------------------------------------------- |
| **Splunk**         | `splunk`    | HTTP Event Collector (HEC)         | Enterprise Splunk deployments                             |
| **Azure Sentinel** | `sentinel`  | DCR Logs Ingestion API             | Azure-native environments                                 |
| **ELK / Logstash** | `elk`       | TCP JSON or Elasticsearch Bulk API | Self-hosted ELK stacks                                    |
| **OpenTelemetry**  | `otel`      | OTLP/HTTP                          | Grafana, Datadog, Sumo Logic, any OTLP-compatible backend |

The `otel` integration reaches Datadog and Sumo Logic without a dedicated integration — point it at their OTLP endpoints.

---

## Audit Events

The following events are logged during deployments:

| Event               | When                                   | Payload                                     |
| ------------------- | -------------------------------------- | ------------------------------------------- |
| `workitem.created`  | A gate pauses the deployment           | Work item ID, gate type, approvers, timeout |
| `workitem.approved` | Operator approves a gate               | Work item ID, approver, timestamp, note     |
| `workitem.rejected` | Operator rejects a gate                | Work item ID, rejector, reason              |
| `workitem.resumed`  | Deployment resumes after gate approval | Work item ID, commit SHA, deploy stage      |

For SIEM, these events are forwarded to your configured backend during the deploy or on-demand via `strata audit export --siem <name>`.

---

## Setup: Automatic Forwarding

### Step 1 — Declare the integration

In your `configuration.yaml`, add the SIEM integration under `spec.integrations`:

**Splunk HEC**

```yaml
spec:
  integrations:
    - name: splunk_hec
      type: splunk
      capabilities: [audit]
      endpoints:
        address: https://splunk.acme.com:8088
      authentication:
        method: api_key
        api_key:
          api_key: ${SPLUNK_HEC_TOKEN}
      properties:
        index: infra_audit
        source: strata
        sourcetype: _json
```

**Azure Sentinel (DCR Logs Ingestion)**

```yaml
spec:
  integrations:
    - name: sentinel_prod
      type: sentinel
      capabilities: [audit]
      endpoints:
        address: https://my-dce.eastus-1.ingest.monitor.azure.com
      authentication:
        method: managed_identity
      properties:
        data_collection_rule_id: dcr-abc123456789
        stream_name: Custom-StrataDeploy_CL
```

**ELK / Logstash (TCP)**

```yaml
spec:
  integrations:
    - name: elk_sink
      type: elk
      capabilities: [audit]
      endpoints:
        address: logstash.internal:5044
      properties:
        protocol: tcp
```

**OpenTelemetry (to Datadog / Grafana / Sumo Logic)**

```yaml
spec:
  integrations:
    - name: otel_sink
      type: otel
      capabilities: [audit]
      endpoints:
        address: https://api.datadoghq.com
      authentication:
        method: api_key
        api_key:
          api_key: ${DD_API_KEY}
          header_name: DD-API-KEY
      properties:
        resource_attributes:
          service.name: strata-infra
          deployment.environment: production
```

### Step 2 — Configure the audit sink

In your environment YAML, reference the integration from `spec.audit.sinks`:

```yaml
spec:
  audit:
    sinks:
      - name: splunk_forward
        integration: splunk_hec
        enabled: true
        events: [deploy_audit]   # optional filter; omit for all events
```

### Step 3 — Deploy

```bash
strata deploy run -f deploy/prod.yaml
```

The audit entry is forwarded automatically after the deployment completes.

---

## Setup: On-Demand Forwarding (`--siem`)

Use `strata audit export --siem <name>` to push historical entries without a full re-deploy.

```bash
# Forward all entries to Splunk
strata audit export --siem splunk_hec

# Forward last 10 entries only
strata audit export --siem splunk_hec --last 10

# Forward entries since a date
strata audit export --siem splunk_hec --since 2024-06-01T00:00:00Z

# Forward AND write to a file (both happen)
strata audit export --siem splunk_hec --out audit-backup.json

# Suppress console output
strata audit export --siem splunk_hec --quiet
```

The `--siem` value must match an integration name declared in one of your configuration YAMLs.

---

## Syslog with CEF Format

For SIEM systems that ingest via syslog (and expect CEF format), use the built-in `syslog` sink type with `format: cef`:

```yaml
spec:
  audit:
    sinks:
      - name: syslog_cef
        type: syslog
        address: siem.acme.com:514
        format: cef          # "json" (default) or "cef"
```

### CEF Message Structure

```
CEF:0|strata|strata-audit|{version}|deploy_audit|Deployment Audit Event|{severity}|
  rt={timestamp} src={user} dst={deployment} act={success|failure}
  externalId={execution_id} msg={full_json}
```

**Severity mapping:**

| Outcome | CEF Severity | Level |
| ------- | ------------ | ----- |
| Success | `3`          | Low   |
| Failure | `7`          | High  |

**Example (success):**

```
<14>CEF:0|strata|strata-audit|1.2.0|deploy_audit|Deployment Audit Event|3|
  rt=2024-06-17T10:45:33Z src=ops@acme.com dst=prod act=success
  externalId=abc-123 msg={"deployment":"prod","success":true,...}
```

**Example (failure):**

```
<14>CEF:0|strata|strata-audit|1.2.0|deploy_audit|Deployment Audit Event|7|
  rt=2024-06-17T11:02:01Z src=ops@acme.com dst=prod act=failure
  externalId=def-456 msg={"deployment":"prod","success":false,...}
```

### Default syslog (JSON)

Without `format: cef`, syslog sends raw JSON:

```
<14>{"execution_id":"abc-123","deployment":"prod","success":true,...}
```

---

## All Sink Types

| Type        | Config fields         | Protocol        | Format          |
| ----------- | --------------------- | --------------- | --------------- |
| `stdout`    | _(none)_              | stdout          | JSON            |
| `ndjson`    | `path`                | file append     | NDJSON          |
| `syslog`    | `address`, `format`   | UDP             | JSON or CEF     |
| `webhook`   | `url`, `headers`      | HTTP POST       | JSON            |
| integration | `integration: <name>` | per-integration | per-integration |

---

## Splunk HEC Reference

### Configuration

```yaml
integrations:
  - name: splunk_hec
    type: splunk
    capabilities: [audit]
    endpoints:
      address: https://splunk.acme.com:8088   # HEC base URL (port 8088 standard)
    authentication:
      method: api_key
      api_key:
        api_key: ${SPLUNK_HEC_TOKEN}           # HEC token (env var recommended)
    properties:
      index: main                              # Splunk index (default: main)
      source: strata                           # Source field (default: strata)
      sourcetype: _json                        # Sourcetype (default: _json)
      channel: <guid>                          # Optional: HEC channel for ack
```

### Event Structure

Each event sent to Splunk:

```text
{
  "event": {
    "execution_id": "abc-123",
    "timestamp": "2024-06-17T10:45:33Z",
    "version": "1.2.0",
    "deployment": "prod",
    "file": "deploy/prod.yaml",
    "success": true,
    "duration_seconds": 215.3,
    "stages": [...],
    "_log_type": "deploy_audit"
  },
  "index": "infra_audit",
  "source": "strata",
  "sourcetype": "_json"
}
```

### Verify Connectivity

```bash
strata tools check splunk_hec
```

This probes `/services/collector/health` on the configured endpoint.

### Environment Variable

```bash
export SPLUNK_HEC_TOKEN="your-hec-token-here"
strata deploy run -f deploy/prod.yaml
```

Or store permanently in `.strata/cli.yaml`:

```bash
strata config set SPLUNK_HEC_TOKEN "your-token"
```

---

## Azure Sentinel Reference

### Configuration

```yaml
integrations:
  - name: sentinel_prod
    type: sentinel
    capabilities: [audit]
    endpoints:
      address: https://<dce-name>.<region>.ingest.monitor.azure.com
    authentication:
      method: managed_identity   # or oauth2 with service principal
    properties:
      data_collection_rule_id: dcr-<immutable-id>
      stream_name: Custom-StrataDeploy_CL
```

**Required Azure setup:**
1. Create a Data Collection Endpoint (DCE)
2. Create a Data Collection Rule (DCR) with a custom stream
3. Assign the `Monitoring Metrics Publisher` role to the identity running strata

### Authentication options

| Method             | When to use                                                 |
| ------------------ | ----------------------------------------------------------- |
| `managed_identity` | Running on Azure VM / AKS / container with managed identity |
| `oauth2`           | Service principal with client credentials                   |

---

## ELK Reference

### TCP (Logstash JSON codec input)

```yaml
integrations:
  - name: elk_sink
    type: elk
    capabilities: [audit]
    endpoints:
      address: logstash.internal:5044
    properties:
      protocol: tcp
```

Logstash input config:

```
input {
  tcp {
    port => 5044
    codec => json
  }
}
```

### HTTP (Elasticsearch Bulk API)

```yaml
integrations:
  - name: elk_http
    type: elk
    capabilities: [audit]
    endpoints:
      address: https://elasticsearch:9200
    authentication:
      method: api_key
      api_key:
        api_key: ${ES_API_KEY}
        header_name: Authorization
    properties:
      protocol: http
      index_pattern: strata-audit
```

---

## OpenTelemetry Reference

### To Grafana Loki

```yaml
integrations:
  - name: otel_sink
    type: otel
    capabilities: [audit]
    endpoints:
      address: https://otel-collector.internal:4318
```

### To Datadog (native OTLP)

```yaml
integrations:
  - name: otel_dd
    type: otel
    capabilities: [audit]
    endpoints:
      address: https://api.datadoghq.com
    authentication:
      method: api_key
      api_key:
        api_key: ${DD_API_KEY}
        header_name: DD-API-KEY
    properties:
      resource_attributes:
        service.name: strata-infra
        env: production
```

### To Sumo Logic (OTLP endpoint)

```yaml
integrations:
  - name: otel_sumo
    type: otel
    capabilities: [audit]
    endpoints:
      address: https://open-collectors.sumologic.com/receiver/v1/otlp/<token>
```

---

## Retry Behavior

All HTTP-based integrations (Splunk, Sentinel, ELK HTTP, OTel) retry on server errors (5xx) and network exceptions:

| Attempt | Backoff   |
| ------- | --------- |
| 1       | immediate |
| 2       | 1 second  |
| 3       | 2 seconds |

Client errors (4xx) are not retried — they indicate a configuration problem (bad token, wrong endpoint, etc.).

Failures are **always graceful** — a failed SIEM forward never blocks or fails a deployment. Errors are logged at `WARNING` level.

---

## Event Fields Reference

Every audit event forwarded to a SIEM contains:

| Field              | Type          | Description                                    |
| ------------------ | ------------- | ---------------------------------------------- |
| `execution_id`     | string (UUID) | Unique identifier for the deployment execution |
| `timestamp`        | ISO 8601      | When the deployment ran                        |
| `version`          | string        | strata CLI version                             |
| `deployment`       | string        | Deployment name                                |
| `file`             | string        | Deployment file path                           |
| `success`          | bool          | Outcome                                        |
| `duration_seconds` | float         | Total deployment duration                      |
| `stages`           | array         | Per-stage name, status, duration, outputs      |
| `commit_sha`       | string        | Git commit SHA (if available)                  |
| `pull_request`     | object        | PR number, title, author (if enriched)         |
| `_log_type`        | string        | Always `"deploy_audit"`                        |

---

## Filtering Events

Each sink can filter to specific event types:

```yaml
sinks:
  - name: splunk_security
    integration: splunk_hec
    events: [deploy_audit, policy_violation]   # only these
```

Omit `events` to receive all enabled event types.

---

## Resend Historical Entries

Replay all local deploy-logs to configured sinks (useful after SIEM downtime):

```bash
# Resend all entries
strata audit resend

# Resend last 5 entries
strata audit resend --last 5

# Resend since a date
strata audit resend --since 2024-06-01T00:00:00Z
```

`audit resend` uses the sinks configured in your `configuration.yaml` — not a `--siem` flag.

---

## Troubleshooting

### SIEM integration not found

```
SIEM integration 'splunk_hec' not found in configuration.
```

Check that:
1. Integration is declared in a configuration YAML under `spec.integrations`
2. Name matches exactly (case-sensitive)
3. The configuration YAML is in the workspace

```bash
# List all loaded configurations
strata validate config/ --output json | jq '.data.files'
```

### Authentication errors

```bash
# Verify integration is reachable
strata tools check splunk_hec

# Check Splunk HEC token
strata tools status --output json | jq '.data.integrations[] | select(.name == "splunk_hec")'
```

### No events appearing in Splunk

1. Confirm the HEC token has write permission to the target index
2. Check Splunk HEC is enabled: `Settings → Data Inputs → HTTP Event Collector`
3. Verify port 8088 is open from the strata host

### CEF messages not parsed

Ensure your syslog receiver is configured for CEF:

```bash
# Validate CEF format manually
echo '<14>CEF:0|strata|strata-audit|1.0|deploy_audit|Deployment Audit Event|3|rt=2024-01-01T00:00:00Z src=user dst=prod act=success externalId=abc msg={}' \
  | nc -u siem.host 514
```

---

## See Also

- [Compliance & Deployment Manifests](./compliance-and-deployment-manifests.md) — Full NIS2/ISAE 3402 evidence guide
- [Deployment Manifests Guide](./deployment-manifests.md) — Build and deploy manifest generation
- [ADR 0022: Splunk HEC + CEF Format](../decisions/0022-siem-integration-splunk-hec-cef.md) — Design rationale
- [Configuration Schema](../config/configuration.md) — `spec.integrations` and `spec.audit`
