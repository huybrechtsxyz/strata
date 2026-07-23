# Splunk HEC SIEM Integration

strata forwards deployment audit events to Splunk via the **HTTP Event Collector (HEC)**.
Events are `POST`ed to `https://{splunk-host}:8088/services/collector` using
newline-delimited JSON — no Splunk forwarder or agent required.

Prerequisites
1. Splunk Enterprise or Splunk Cloud with HEC enabled
2. A HEC token with write access to the target index

Enable HEC in Splunk
- Splunk Enterprise: Settings → Data Inputs → HTTP Event Collector → New Token
- Splunk Cloud: same path via the Splunk UI

Configuration YAML

```yaml
integrations:
  - name: splunk
    type: splunk
    capabilities: [audit]
    endpoints:
      address: https://splunk.example.com:8088
    authentication:
      method: api_key
      api_key:
        api_key: SPLUNK_HEC_TOKEN    # env var name holding the HEC token
    properties:
      index: main                    # Splunk index (default: main)
      source: strata                 # event source (default: strata)
      sourcetype: _json              # sourcetype (default: _json)
```

Authentication

| Variable           | Purpose                          | Required |
| ------------------ | -------------------------------- | -------- |
| `SPLUNK_HEC_TOKEN` | HEC token (default env var name) | Yes      |

Override the env var name in YAML:
```yaml
authentication:
  api_key:
    api_key: MY_SPLUNK_TOKEN    # reads from MY_SPLUNK_TOKEN instead
```

Optional properties

| Property     | Default  | Description                                  |
| ------------ | -------- | -------------------------------------------- |
| `index`      | `main`   | Splunk index to write events to              |
| `source`     | `strata` | Event source field                           |
| `sourcetype` | `_json`  | Sourcetype field                             |
| `channel`    | —        | HEC channel GUID for indexer acknowledgement |

Audit configuration in deployment YAML

```yaml
spec:
  audit:
    enabled: true
    sinks:
      - name: splunk
        type: splunk
```

Verify HEC connectivity
```
curl -X POST https://splunk.example.com:8088/services/collector/health \
  -H "Authorization: Splunk $SPLUNK_HEC_TOKEN"
```

Expected: `{"text":"HEC is healthy","code":17}`

Test event
```
curl -X POST https://splunk.example.com:8088/services/collector \
  -H "Authorization: Splunk $SPLUNK_HEC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event": {"test": true}, "sourcetype": "_json"}'
```

SSL/TLS
For self-signed certificates in Splunk Enterprise, either add the CA to the system trust
store or set `REQUESTS_CA_BUNDLE` to your CA certificate path.

Requires: `pip install requests` (bundled with strata)

Docs
- HEC overview: https://docs.splunk.com/Documentation/Splunk/latest/Data/UsetheHTTPEventCollector
- HEC token setup: https://docs.splunk.com/Documentation/Splunk/latest/Data/HECExamples
