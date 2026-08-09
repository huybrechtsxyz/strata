# ELK / Logstash SIEM Integration

strata forwards deployment audit events to an ELK stack via two protocols:
- **TCP JSON** — Logstash TCP input with JSON codec (default)
- **HTTP** — Elasticsearch Bulk API directly

No binary required — events are sent over TCP or HTTP.

Installation (ELK stack)
- Managed: Elastic Cloud — https://www.elastic.co/cloud
- Self-hosted: https://www.elastic.co/guide/en/elastic-stack-get-started/current/get-started-docker.html
- Docker Compose (quick start): `docker compose up elasticsearch logstash kibana`

Logstash input (TCP mode, `logstash.conf` snippet)
```
input {
  tcp {
    port => 5000
    codec => json
  }
}
output {
  elasticsearch {
    hosts => ["http://elasticsearch:9200"]
    index => "strata-audit-%{+YYYY.MM.dd}"
  }
}
```

Configuration YAML — TCP mode (default)

```yaml
integrations:
  - name: elk
    type: elk
    capabilities: [audit]
    endpoints:
      address: logstash.internal:5000
    properties:
      protocol: tcp            # default
      index_pattern: strata-audit
```

Configuration YAML — HTTP mode (Elasticsearch Bulk API)

```yaml
integrations:
  - name: elk
    type: elk
    capabilities: [audit]
    endpoints:
      address: http://elasticsearch.internal:9200
    properties:
      protocol: http
      index_pattern: strata-audit
```

Authentication (HTTP mode)
Set `ELASTIC_USERNAME` and `ELASTIC_PASSWORD`, or configure an API key via the
integration's `authentication` block:

```yaml
authentication:
  method: api_key
  api_key:
    api_key: ELASTIC_API_KEY
```

Audit configuration in deployment YAML

SIEM sinks reference the integration **by name** with `integration:`. Only the built-in
sink types (`stdout`, `ndjson`, `syslog`, `webhook`) use `type:`.

```yaml
spec:
  audit:
    sinks:
      - name: elk
        integration: elk          # must match integrations[].name above
```

Verify TCP connectivity
```
echo '{"event":"test","level":"info"}' | nc logstash.internal 5000
```

Verify HTTP connectivity
```
curl http://elasticsearch.internal:9200/_cluster/health
```

Docs
- Elasticsearch: https://www.elastic.co/docs
- Logstash: https://www.elastic.co/guide/en/logstash/current/index.html
