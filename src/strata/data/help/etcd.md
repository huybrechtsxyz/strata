# etcd Integration

etcd is a strongly consistent, distributed key-value store used heavily in Kubernetes
and other cloud-native platforms. strata uses it for variable and KV storage via the
`etcdctl` CLI (preferred) or the v3 HTTP API (fallback).

Installation
- macOS: `brew install etcd`
- Linux: download from https://github.com/etcd-io/etcd/releases or use package manager
- Windows: download binary from GitHub releases
- Docs: https://etcd.io/docs/latest/install/

Verify install
```
etcdctl version
```

Configuration YAML

```yaml
integrations:
  - name: etcd
    type: etcd
    capabilities: [variables, keyvalue]
    endpoints:
      address: http://127.0.0.1:2379
```

Authentication

| Method          | Variables                                                            |
| --------------- | -------------------------------------------------------------------- |
| Basic auth      | `ETCD_USERNAME`, `ETCD_PASSWORD`                                     |
| TLS client cert | `ETCD_CA_FILE`, `ETCD_CERT_FILE`, `ETCD_KEY_FILE`                    |
| etcdctl aliases | `ETCDCTL_ENDPOINTS`, `ETCDCTL_CACERT`, `ETCDCTL_CERT`, `ETCDCTL_KEY` |

Environment variables

| Variable         | Purpose                           | Required                              |
| ---------------- | --------------------------------- | ------------------------------------- |
| `ETCD_ENDPOINTS` | Comma-separated etcd endpoints    | No (default: `http://127.0.0.1:2379`) |
| `ETCD_USERNAME`  | Basic auth username               | No                                    |
| `ETCD_PASSWORD`  | Basic auth password               | No                                    |
| `ETCD_CA_FILE`   | CA certificate for TLS            | No                                    |
| `ETCD_CERT_FILE` | Client certificate for mutual TLS | No                                    |
| `ETCD_KEY_FILE`  | Client private key for mutual TLS | No                                    |

Endpoint override in YAML (takes priority over env vars):
```yaml
integrations:
  - name: etcd
    type: etcd
    endpoints:
      address: https://etcd.internal:2379
    authentication:
      method: oauth2
      oauth2:
        client_id: ETCD_USERNAME
        client_secret: ETCD_PASSWORD
```

Common checks
```
etcdctl endpoint health
etcdctl get / --prefix --keys-only    # list all keys
etcdctl put mykey myvalue
etcdctl get mykey
```

Docs
- https://etcd.io/docs
