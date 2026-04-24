# Store Integration (Generic)

Purpose
- Abstraction for persistent stores used by integrations (local files, S3, object stores).

Configuration
- Provide store-specific settings in `.platform/config.yaml` or environment variables.
- Common keys: `store.type` (`filesystem`, `s3`, `azure_blob`), `store.endpoint`, `store.bucket`

Usage
- Read: integration should expose `get(key)` / `list(prefix)` APIs
- Write: `put(key, value)` and optional ACL/metadata

Security
- Store secrets (credentials) in a secret manager, not in repo config

Docs
- Implementation-specific — check the corresponding integration module for details
