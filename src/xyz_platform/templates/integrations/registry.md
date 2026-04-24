# Integration Registry

Purpose
- `registry.py` maps integration names to implementation classes for discovery.

How it works
- Each integration module should expose a public class and register an entry in `registry`.
- The `factory` uses this map to instantiate the chosen integration.

Example
```
# registry.py
REGISTRY = {
  'docker': DockerIntegration,
  'git': GitIntegration,
}
```

Docs
- Keep registry entries small and import-safe (avoid heavy imports at module import time).
