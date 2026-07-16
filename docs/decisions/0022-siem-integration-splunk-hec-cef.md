# SIEM Integration: Splunk HEC + CEF Format

- Status: completed
- Date: 2026-07-05
- Related: [ADR 0018 — Deployment Audit Traceability](0018-deployment-audit-traceability.md)

## Summary

Extend SIEM audit forwarding with two additions:

1. **Splunk HTTP Event Collector (HEC)** — a native Splunk integration for enterprises that use Splunk directly without an OpenTelemetry Collector
2. **CEF format for syslog sink** — Common Event Format support on the existing `syslog` built-in sink, enabling SIEM systems that ingest syslog with CEF (IBM QRadar, ArcSight, LogRhythm, etc.)

Additionally: `strata audit export --siem <name>` flag for on-demand forwarding of historical entries to any configured SIEM integration.

---

## Problem

### Before this ADR

Three SIEM backends existed: `sentinel`, `elk`, `otel`. These covered:
- Azure Monitor (sentinel)
- Logstash/Elasticsearch (elk)
- Any OTLP-compatible backend via OpenTelemetry (otel)

**Gap 1 — Splunk without OTel Collector:**

Splunk supports OTLP since the OTel Add-On, but many enterprise Splunk deployments use the HTTP Event Collector (HEC) directly — especially in environments where an OTel Collector is not deployed or managed. HEC is a fundamentally different protocol: it expects `Authorization: Splunk <token>` and newline-delimited JSON events with explicit `index`, `source`, and `sourcetype` fields.

Pointing the `otel` integration at a Splunk OTLP endpoint requires the OTel Splunk Add-On. Using HEC requires none of it — just an endpoint and a token.

**Gap 2 — CEF over syslog:**

The existing `syslog` sink sent raw JSON. Many enterprise SIEMs ingest syslog with CEF (Common Event Format), a standardized log format used by IBM QRadar, ArcSight, McAfee ESM, and others. Without CEF support, operators either had to run a parser at the SIEM end or skip syslog sinks entirely.

**Gap 3 — No on-demand SIEM forwarding:**

`strata audit resend` forwards to configured `spec.audit.sinks`. There was no way to push entries to an integration by name without editing YAML configuration — inconvenient for operations teams doing one-off compliance exports.

---

## Decision

### 1. `SplunkSiemIntegration` (type: `splunk`)

A new `SiemBaseIntegration` subclass that implements the Splunk HEC protocol:

```
POST https://{splunk-host}:8088/services/collector
Authorization: Splunk <token>
Content-Type: application/json

{"event": {..., "_log_type": "deploy_audit"}, "index": "main", "source": "strata", "sourcetype": "_json"}
{"event": {...}, "index": "main", "source": "strata", "sourcetype": "_json"}
```

Batch requests use **newline-delimited events** (not JSON arrays) — the most efficient HEC format.

**Why not extend `otel`?** The OTel integration adds OTLP envelope structure (resource attributes, log records, severity numbers). HEC expects a flat event envelope. These are different protocols requiring different implementations.

**Why not extend `elk`?** ELK uses Elasticsearch Bulk API ndjson or Logstash TCP. HEC has different authentication and payload structure.

### 2. CEF format on `syslog` sink

Add `format: cef` option to `AuditSinkModel.sinks[].syslog`. When set, `AuditController._send_syslog()` formats the payload as:

```
CEF:0|Vendor|Product|Version|SignatureID|Name|Severity|Extension
```

Specifically:

```
<14>CEF:0|strata|strata-audit|{version}|deploy_audit|Deployment Audit Event|{3or7}|
  rt={timestamp} src={user} dst={deployment} act={success|failure}
  externalId={execution_id} msg={json}
```

**Severity mapping:** 3 (Low) on success, 7 (High) on failure — aligned with CEF severity scale (0-10).

**Why not a new sink type?** CEF is a format choice, not a different transport. The UDP syslog transport is identical — only the payload encoding changes. Adding `format` as a field on the syslog sink avoids proliferating sink types (`syslog_cef`, `syslog_json`, etc.).

### 3. `--siem <name>` flag on `audit export`

```bash
strata audit export --siem splunk_hec [--last N] [--since ISO] [--out FILE]
```

Resolution path:
1. Scan configuration YAMLs for an integration with matching name
2. Create via `IntegrationFactory`
3. Verify it implements `ISiemSink`
4. Call `send_batch("deploy_audit", entries)`

**Why `--siem` on `export` instead of a new `strata audit forward` command?**

- `audit export` already queries, filters, and shapes entries — `--siem` adds a destination rather than duplicating the query logic
- `audit resend` exists for sink-config-driven forwarding; `--siem` adds integration-by-name forwarding as a complement
- Keeps `audit` surface area small: `changes`, `resend`, `export`

**Behavior with `--out`:** Both happen — file is written AND entries are forwarded. This supports compliance workflows where both a local backup and SIEM ingestion are required.

---

## Alternatives Considered

### Alt A: Add Datadog and Sumo Logic integrations

**Rationale for rejection:** Both Datadog and Sumo Logic natively support OTLP. The existing `otel` integration forwards to either with no code changes — point `endpoints.address` at the OTLP endpoint and set the appropriate auth header. No dedicated integration needed.

**When this changes:** If Datadog/Sumo Logic–specific features (service maps, APM traces, custom metrics) are needed, a dedicated integration would add value. For plain audit log forwarding, `otel` is sufficient.

### Alt B: CEF as a separate sink type (`syslog_cef`)

**Rationale for rejection:** Transport and format are orthogonal. Adding a `syslog_cef` type implies CEF requires a different transport implementation. It doesn't — CEF is just a string format applied before the UDP send. A `format` field on the existing `syslog` sink is cleaner.

### Alt C: `strata audit forward --siem <name>` as a new command

**Rationale for rejection:** Would duplicate the `--last`, `--since`, `--output` flags already on `audit export`. Using `--siem` as an output destination on `export` reuses existing query and filtering logic without a new command.

---

## Impact

### New files

- `src/strata/integrations/siem/splunk_siem_integration.py`

### Modified files

| File                                         | Change                                       |
| -------------------------------------------- | -------------------------------------------- |
| `src/strata/integrations/siem/__init__.py`   | Export `SplunkSiemIntegration`               |
| `src/strata/integrations/factory.py`         | Register `"splunk"` type                     |
| `src/strata/models/audit_config_model.py`    | `format` field on `AuditSinkModel`           |
| `src/strata/controllers/audit_controller.py` | `_send_syslog(fmt)` + `_format_cef()`        |
| `src/strata/commands/cli_audit.py`           | `--siem` flag + `_forward_entries_to_siem()` |

### New tests

- `tests/strata/integrations/siem/test_splunk_siem_integration.py` — 17 tests
- `tests/strata/commands/test_commands_audit_siem.py` — 16 tests
- Extensions to `test_controllers_audit_layer4.py` — 27 additional tests

### Backward compatibility

All changes are additive:
- `format` defaults to `None` on existing `AuditSinkModel` instances
- `--siem` is an optional flag; `audit export` without it behaves identically
- Existing `sentinel`, `elk`, `otel` integrations unchanged

---

## Configuration Examples

### Splunk HEC

```yaml
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

### Syslog with CEF

```yaml
sinks:
  - name: syslog_cef
    type: syslog
    address: siem.acme.com:514
    format: cef
```

### On-demand forwarding

```bash
strata audit export --siem splunk_hec --last 100 --out backup.json
```

---

## References

- [Splunk HTTP Event Collector documentation](https://docs.splunk.com/Documentation/Splunk/latest/Data/UsetheHTTPEventCollector)
- [CEF standard (ArcSight)](https://www.microfocus.com/documentation/arcsight/arcsight-smartconnectors-8.4/cef-implementation-standard/)
- [ADR 0018 — Deployment Audit Traceability](0018-deployment-audit-traceability.md)
- [SIEM Audit Forwarding Guide](../guides/siem-audit-forwarding.md)
