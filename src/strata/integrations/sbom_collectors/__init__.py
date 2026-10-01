#!/usr/bin/env python3
"""SBOM component collectors (docs/design/sbom-generation.md Phase 1).

One pluggable `SbomCollector` per technology source — `image`, `compose`,
`helm`, `terraform` (Phase 1); `ansible`/`deps` (lockfiles) deferred, no
trigger defined yet. See `base.py` for the collector contract and
`registry.py` for how a new source (built-in or third-party) is added.
"""
