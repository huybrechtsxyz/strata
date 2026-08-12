# Strata state service — Running a central history store

The strata state service is a small HTTP server that accepts and stores deployment history, audit events, cost data, and drift records from one or more strata workspaces. This guide covers setup, operation, and integration.

**Key concepts:**

- **Local-first by default** — history (deploy-logs, cost tracking, drift records) is written to local files by default and remains local unless explicitly forwarded
- **Central when configured** — point a workspace's webhook sink at the state service and history is forwarded over HTTP; the service stores it durably in SQL
- **Queryable for reporting** — operators can query the database directly with `psql`, Grafana, Metabase, or any SQL tool to build dashboards, reports, and cost/drift trends
- **No state dependency** — deployments succeed or fail independently of the state service; ingestion failures never block the commands that produce the events
- **Bearer-token protected** — each workspace gets its own token; tokens are created/revoked by an admin through HTTP, not by direct database access

---

## Installation

The state service is an optional extra, not installed by default:

```bash
# Install with SQLite support (zero-config, good for dev/small deployments)
pip install xyz-strata[server]

# Or for production backends
pip install xyz-strata[server,server-postgres]      # PostgreSQL
pip install xyz-strata[server,server-mssql]         # SQL Server
pip install "xyz-strata[server,server-postgres,server-mssql]"  # All three
```

SQLite is used by default; it requires no external service and is suitable for up to hundreds of thousands of events. For production deployments with many workspaces, PostgreSQL or SQL Server is recommended.

---

## Starting the server

### Zero-config local start (SQLite)

```bash
strata serve run
# Listening on http://127.0.0.1:8000
# Press Ctrl+C to stop
```

The server binds to `127.0.0.1:8000` by default and creates a `./strata-state.db` file in the current directory. On first start, the database tables are created automatically by the migration step below.

### Initialize the database (first time only)

Before the server can accept events, create the `events` table:

```bash
# SQLite (same file as 'serve run' will use)
strata serve migrate --db-url sqlite:///./strata-state.db

# PostgreSQL
strata serve migrate --db-url postgresql+psycopg://user:pass@localhost/strata_state

# SQL Server
strata serve migrate --db-url mssql+pyodbc://user:pass@hostname/strata_state
```

This step is idempotent — running it again is safe.

### Production configuration

#### TLS (recommended for remote access)

```bash
# Generate a self-signed certificate (development)
openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365

# Start with TLS
strata serve run \
  --host 0.0.0.0 \
  --tls-cert cert.pem \
  --tls-key key.pem
```

**TLS requirement:** If binding to any address other than `127.0.0.1`, `::1`, or `localhost`, TLS is mandatory. Attempting a non-loopback bind without TLS fails fast with a clear error.

#### PostgreSQL backend

```bash
strata serve migrate --db-url postgresql+psycopg://strata:$PASSWORD@postgres.internal:5432/strata_state

strata serve run \
  --host 0.0.0.0 \
  --db-url postgresql+psycopg://strata:$PASSWORD@postgres.internal:5432/strata_state \
  --tls-cert cert.pem \
  --tls-key key.pem
```

#### Environment variables (for containerization)

All CLI flags have `STRATA_SERVE_*` environment-variable equivalents:

```bash
export STRATA_SERVE_HOST=0.0.0.0
export STRATA_SERVE_PORT=8000
export STRATA_SERVE_DB_URL=postgresql+psycopg://user:pass@postgres:5432/strata_state
export STRATA_SERVE_TLS_CERT=/etc/certs/cert.pem
export STRATA_SERVE_TLS_KEY=/etc/certs/key.pem

strata serve run
# Uses all env vars, no flags needed
```

### Docker / Kubernetes

**Example Dockerfile:**

```dockerfile
FROM python:3.13-slim

RUN pip install xyz-strata[server,server-postgres]

ENTRYPOINT ["strata", "serve", "run"]
```

**Example pod manifest:**

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: strata-state-service
spec:
  containers:
    - name: strata-state
      image: ghcr.io/org/strata-state:latest
      ports:
        - containerPort: 8000
          name: http
      env:
        - name: STRATA_SERVE_HOST
          value: "0.0.0.0"
        - name: STRATA_SERVE_PORT
          value: "8000"
        - name: STRATA_SERVE_DB_URL
          valueFrom:
            secretKeyRef:
              name: strata-state-db
              key: connection-string
        - name: STRATA_SERVE_TLS_CERT
          value: /etc/certs/tls.crt
        - name: STRATA_SERVE_TLS_KEY
          value: /etc/certs/tls.key
      volumeMounts:
        - name: tls-certs
          mountPath: /etc/certs
          readOnly: true
      livenessProbe:
        httpGet:
          path: /healthz
          port: 8000
      readinessProbe:
        httpGet:
          path: /healthz
          port: 8000
  volumes:
    - name: tls-certs
      secret:
        secretName: strata-state-tls
```

---

## Token management

Workspaces authenticate to the service using bearer tokens. Tokens are created by an admin and rotated by operators — never stored or shared through deployment configuration.

### Create a workspace token

```bash
# Generate a new token for the "prod" workspace
strata serve token create \
  --url https://state-service.internal:8000 \
  --admin-token $ADMIN_TOKEN \
  --workspace prod

# Output:
# Token: strata_state_e1d3f6c9a2b4e7k...
# Workspace: prod
# Created: 2026-08-12T15:30:45Z
```

Store this token securely (e.g., in your secret management system — Vault, Bitwarden, GitHub Secrets).

### List tokens (admin only)

```bash
strata serve token list \
  --url https://state-service.internal:8000 \
  --admin-token $ADMIN_TOKEN
```

### Revoke a token

```bash
strata serve token revoke \
  --url https://state-service.internal:8000 \
  --admin-token $ADMIN_TOKEN \
  --workspace prod
```

**Admin token:** By default, the first time the service starts, it prints an admin token to stdout (save it immediately). To rotate the admin token, consult the [API Reference](#api-reference) below or use your database client directly to `UPDATE` the `admin_tokens` table. The admin token is never stored in clear text — only its hash — so you **cannot** recover a lost one; you must generate a new one and update the database.

---

## Configuring workspaces to forward events

Once the service is running and a workspace token exists, point a workspace's audit configuration at it:

```yaml
# config/my-config.yaml (kind: configuration)

spec:
  audit:
    policy:
      events:
        deployment.completed: true
        deployment.failed: true
        policy.violated: true
    sinks:
      - name: strata-state
        type: webhook
        url: https://state-service.internal:8000/v1/events
        authentication:
          method: oauth2
          oauth2:
            client_secret: $STRATA_STATE_TOKEN  # from 'serve token create'
        headers:
          User-Agent: strata-workspace/prod
```

This configuration:
- Captures deploy completion, failures, and policy violations
- Forwards them to the state service over HTTPS
- Uses bearer-token authentication (the token is automatically prefixed with `Authorization: Bearer`)
- Continues to work even if the service is temporarily unavailable (best-effort delivery, with bounded retry)

The token can be an environment variable, a file reference via `@repo/path`, or a secret from a store like Vault or Azure Key Vault — see [Secrets & Variables](../platform/config/configuration.md) for the full reference.

---

## Querying the stored events

Events are stored in the `events` table as JSON. Query directly with `psql`, `mysql`, a Grafana datasource, or any SQL tool:

### Basic queries

**Last 10 events:**

```sql
SELECT execution_id, record_type, deployment, workspace, outcome, recorded_at
FROM events
ORDER BY recorded_at DESC
LIMIT 10;
```

**Failed deployments in the last 7 days:**

```sql
SELECT deployment, workspace, environment, recorded_at, payload
FROM events
WHERE record_type LIKE '%.deployment.failed'
  AND recorded_at >= NOW() - INTERVAL '7 days'
ORDER BY recorded_at DESC;
```

**Events by workspace:**

```sql
SELECT workspace, COUNT(*) as event_count, MAX(recorded_at) as latest
FROM events
GROUP BY workspace
ORDER BY latest DESC;
```

**Extract a field from the JSON payload (PostgreSQL):**

```sql
SELECT
  deployment,
  (payload -> 'duration_seconds')::numeric as duration,
  recorded_at
FROM events
WHERE record_type LIKE '%.deployment.completed'
ORDER BY recorded_at DESC;
```

### Building dashboards

**Grafana datasource setup:**

1. Add a PostgreSQL datasource
2. Point it at your state-service database
3. Build panels with queries like:

```text
SELECT
  $__timeGroupAlias(recorded_at, $__interval),
  COUNT(*) as deployments,
  SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successful,
  SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) as failed
FROM events
WHERE record_type LIKE '%.deployment.%'
  AND workspace = '$workspace'
  AND environment = '$environment'
GROUP BY $__timeGroupAlias(recorded_at, $__interval)
ORDER BY time DESC;
```

---

## Health checks and monitoring

### CLI health check

```bash
# Local (loopback, no TLS)
strata serve health http://127.0.0.1:8000

# Remote (HTTPS)
strata serve health https://state-service.internal:8000
```

Success: `{"status": "ok"}`  
Failure: HTTP 503 with error detail (e.g., database connection failed).

### Health check endpoint

```bash
curl https://state-service.internal:8000/healthz
# {"status": "ok"}

curl -I https://state-service.internal:8000/healthz
# HTTP/1.1 200 OK
```

Use this endpoint for Kubernetes `livenessProbe`/`readinessProbe` (see Docker/Kubernetes example above).

### Metrics

The service exposes Prometheus-compatible metrics at `/metrics` (optional; requires `pip install xyz-strata[server-metrics]`, not yet shipped). For now, monitor via your database connection — query `SELECT COUNT(*) FROM events` to track ingestion rate.

---

## Troubleshooting

### "Connection refused" or "403 Forbidden" when workspace tries to forward

**Cause:** The workspace token is invalid, expired, or the service is not reachable.

**Fix:**
1. Verify the service is running: `strata serve health <url>`
2. Verify the token is current: `strata serve token list --url <url> --admin-token ...` and recreate if needed
3. Verify network access: `curl -v https://state-service.internal:8000/healthz`
4. Check the workspace's audit sink configuration (URL must be exact, token must be set)

### "database connection failed" on /healthz

**Cause:** The database backend (PostgreSQL, SQL Server) is unreachable or credentials are wrong.

**Fix:**
1. Test the connection manually:
   ```bash
   psql postgresql+psycopg://user:pass@postgres/strata_state
   # or
   strata serve migrate --db-url postgresql+psycopg://user:pass@postgres/strata_state
   ```
2. Check environment variables: `env | grep STRATA_SERVE`
3. Verify firewall rules if using a remote database
4. For PostgreSQL, ensure `psycopg` is installed: `pip install psycopg[binary]`

### "TLS certificate required" error

**Cause:** Attempting to bind to a non-loopback address (e.g., `0.0.0.0`) without TLS.

**Fix:**
1. Provide TLS certs: `--tls-cert cert.pem --tls-key key.pem`
2. Or bind to loopback only: `--host 127.0.0.1` (local-only, not suitable for production)

### Slow ingest or high latency

**Causes:**
- Database is far away (high network latency) — move closer or use a local cache
- Database is overloaded — check `SELECT COUNT(*) FROM events` and consider archiving old rows
- Network issues between workspace and service — check retry logs in the workspace's local audit output

**Fix:**
- Query the `events` table's size: `SELECT pg_size_pretty(pg_total_relation_size('events'));` (PostgreSQL)
- Archive old events (older than 90 days, for example) to a separate table or CSV for cold storage
- Monitor database connection pool if using a connection pooler like PgBouncer

---

## Production checklist

- [ ] Database is PostgreSQL or SQL Server (not SQLite for production)
- [ ] Database backups are configured and tested
- [ ] TLS certificates are valid and will be renewed before expiry
- [ ] Admin token is stored securely (password manager, secret vault)
- [ ] Workspace tokens are rotated regularly (e.g., quarterly)
- [ ] `/healthz` endpoint is monitored and alerts on failure
- [ ] Database connectivity is monitored (e.g., Prometheus scrape of row count)
- [ ] Firewall rules allow only required access to the service
- [ ] Service is behind a load balancer if high availability is needed
- [ ] Old events are archived or purged according to retention policy
- [ ] Server process runs under a non-root user (not `root`)

---

## API Reference

### POST /v1/events

Ingest an audit event.

**Request:**

```
Authorization: Bearer <workspace_token>
Content-Type: application/json

{
  "specversion": "1.0",
  "type": "xyz.huybrechts.strata.deployment.completed",
  "source": "my-workspace/my-deployment",
  "id": "deployment-abc123-20260812",
  "time": "2026-08-12T15:30:45Z",
  "deployment": "my-deployment",
  "workspace": "my-workspace",
  "environment": "prod",
  "outcome": "success",
  "data": { ...full event payload... }
}
```

**Response:**

- `202 Accepted` — Event accepted (will be written to database)
- `400 Bad Request` — Invalid payload (e.g., missing required fields)
- `401 Unauthorized` — Invalid or missing token
- `409 Conflict` — Duplicate (same `id` + `type`; idempotent)
- `500 Internal Server Error` — Database error

### GET /healthz

Health check endpoint.

**Response:**

- `200 OK` — Service healthy
- `503 Service Unavailable` — Database unreachable

```json
{ "status": "ok" }
```

### GET /v1/events/tail

Retrieve the most recent events (admin-only, read-only).

**Request (requires admin token):**

```
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  'https://state-service.internal:8000/v1/events/tail?limit=100&workspace=prod'
```

**Parameters:**

- `limit` (default: 100, max: 1000) — Number of recent events to return
- `workspace` (optional) — Filter to a specific workspace

**Response:**

```text
{
  "events": [
    {
      "execution_id": "...",
      "record_type": "xyz.huybrechts.strata.deployment.completed",
      "deployment": "...",
      "workspace": "...",
      "outcome": "success",
      "recorded_at": "2026-08-12T15:30:45Z",
      "payload": { ... }
    }
  ],
  "count": 5,
  "limit": 100
}
```

### POST /v1/tokens (admin-only)

Create a new workspace token.

**Request:**

```
Authorization: Bearer <admin_token>
Content-Type: application/json

{ "workspace": "prod" }
```

**Response:**

```json
{
  "token": "strata_state_e1d3f6c9a2b4e7k...",
  "workspace": "prod",
  "created_at": "2026-08-12T15:30:45Z",
  "expires_at": null
}
```

### GET /v1/tokens (admin-only)

List all workspace tokens.

**Request:**

```
Authorization: Bearer <admin_token>
```

**Response:**

```json
{
  "tokens": [
    {
      "id": "tok_123",
      "workspace": "prod",
      "created_at": "2026-08-12T15:30:45Z",
      "last_used_at": "2026-08-12T16:00:00Z",
      "expires_at": null
    }
  ]
}
```

### DELETE /v1/tokens/:id (admin-only)

Revoke a workspace token.

**Request:**

```
Authorization: Bearer <admin_token>
DELETE /v1/tokens/tok_123
```

**Response:** 204 No Content

---

## See also

- [ADR-0065: Strata state service](../decisions/0065-strata-state-service.md) — Design rationale and phases
- [SIEM integration & audit forwarding](siem-audit-forwarding.md#webhook--strata-state-service-reference) — Configuring webhook sinks
- [Deployment audit & traceability](../platform/commands.md#audit) — Audit command reference
