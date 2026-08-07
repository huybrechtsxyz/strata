# Azure Sentinel SIEM Integration

strata forwards deployment audit events to Azure Sentinel (Microsoft Sentinel) via the
**DCR-based Logs Ingestion API**. No agent or SDK is required — events are posted
directly to a Data Collection Rule endpoint.

Prerequisites
1. An Azure Log Analytics workspace with Microsoft Sentinel enabled
2. A Data Collection Rule (DCR) with a custom table stream
3. A Data Collection Endpoint (DCE)
4. An identity with the **Monitoring Metrics Publisher** role on the DCR

Setup in Azure Portal
1. Create a DCE: Monitor → Data Collection Endpoints → Create
2. Create a DCR with a custom stream (e.g., `Custom-StrataAudit_CL`)
3. Assign **Monitoring Metrics Publisher** to your managed identity or service principal on the DCR
4. Note the **DCE endpoint URL** and the **DCR immutable ID** (`dcr-xxx`)

Configuration YAML

```yaml
integrations:
  - name: sentinel
    type: sentinel
    capabilities: [audit]
    endpoints:
      address: https://my-dce.westeurope-1.ingest.monitor.azure.com
    properties:
      data_collection_rule_id: dcr-abc1234567890
      stream_name: Custom-StrataAudit_CL
```

Authentication
The integration uses `azure-identity` `DefaultAzureCredential`, which tries these sources
in order: managed identity → service principal env vars → Azure CLI login.

| Variable              | Purpose                  | Required      |
| --------------------- | ------------------------ | ------------- |
| `AZURE_CLIENT_ID`     | Service principal app ID | Yes (SP auth) |
| `AZURE_CLIENT_SECRET` | Service principal secret | Yes (SP auth) |
| `AZURE_TENANT_ID`     | Azure tenant ID          | Yes (SP auth) |

Or use managed identity (no env vars needed when running on Azure compute):
```
az login   # for local development
```

Requires: `pip install azure-identity` (or `pip install strata[azure]`)

Audit configuration in deployment YAML

SIEM sinks reference the integration **by name** with `integration:`. Only the built-in
sink types (`stdout`, `ndjson`, `syslog`, `webhook`) use `type:`.

```yaml
spec:
  audit:
    sinks:
      - name: sentinel
        integration: sentinel     # must match integrations[].name above
```

Verify connectivity
```
curl -X POST \
  "https://<dce-endpoint>/dataCollectionRules/<dcr-id>/streams/<stream>?api-version=2023-01-01" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '[{"event": "test"}]'
```

Docs
- Logs Ingestion API: https://learn.microsoft.com/en-us/azure/azure-monitor/logs/logs-ingestion-api-overview
- DCR setup: https://learn.microsoft.com/en-us/azure/azure-monitor/logs/tutorial-logs-ingestion-portal
