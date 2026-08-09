# OpenTelemetry (OTel) SIEM Integration

strata forwards deployment audit events as OpenTelemetry Log Records to any
OTLP-compatible backend via **OTLP/HTTP JSON** (`POST /v1/logs`). No OTel SDK or
collector binary is required — events are posted directly using standard HTTP.

Compatible backends
- **Grafana Loki** (with OTel Collector)
- **Datadog** (OTel ingest endpoint)
- **Splunk** (OTel Collector)
- **Elastic APM** (OTLP endpoint)
- **Jaeger**, **Tempo**, **Honeycomb**, **New Relic** — any OTLP/HTTP receiver

OpenTelemetry Collector (optional, recommended for production)
- Docs: https://opentelemetry.io/docs/collector/getting-started/
- Docker: `docker run otel/opentelemetry-collector`
- Receives events on port `4318` (OTLP/HTTP) or `4317` (OTLP/gRPC)

Configuration YAML

```yaml
integrations:
  - name: otel
    type: otel
    capabilities: [audit]
    endpoints:
      address: https://otel-collector.internal:4318
    properties:
      protocol: http              # default: http (grpc falls back to http)
      resource_attributes:        # optional: extra OTel resource attributes
        service.name: strata
        deployment.environment: production
```

For Datadog:
```yaml
endpoints:
  address: https://http-intake.logs.datadoghq.com
```
Set `OTEL_EXPORTER_OTLP_HEADERS=DD-API-KEY=<your-key>` or configure an API key
in the authentication block.

For Grafana Cloud:
```yaml
endpoints:
  address: https://tempo-prod-xx.grafana.net:443
```

Authentication
Headers (API keys, bearer tokens) are passed via the `authentication` block:

```yaml
authentication:
  method: api_key
  api_key:
    api_key: OTEL_AUTH_TOKEN    # env var holding "Bearer <token>" or similar
```

No authentication is needed when targeting a local OTel Collector on a private network.

Audit configuration in deployment YAML

SIEM sinks reference the integration **by name** with `integration:`. Only the built-in
sink types (`stdout`, `ndjson`, `syslog`, `webhook`) use `type:`.

```yaml
spec:
  audit:
    sinks:
      - name: otel
        integration: otel         # must match integrations[].name above
```

Verify endpoint
```
curl -X POST https://otel-collector.internal:4318/v1/logs \
  -H "Content-Type: application/json" \
  -d '{"resourceLogs":[]}'
```

Requires: `pip install requests` (bundled with strata)

Docs
- OTLP/HTTP spec: https://opentelemetry.io/docs/specs/otlp/#otlphttp
- OTel Collector: https://opentelemetry.io/docs/collector/
