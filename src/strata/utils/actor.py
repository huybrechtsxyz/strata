#!/usr/bin/env python3
"""Resolve a weak, best-effort actor identity for the audit manifest's
`deployed_by` field (docs/design/audit-trail.md's "Actor/identity").

Phase 3 of that doc's Layer 2 Implementation Plan. Pure function, no
dependency on other layers — lives in `utils/` (not `controllers/`), same
placement rationale as `path_conventions.py`.

Deliberately weak — explicitly acknowledged in the design as strictly
better than v1's own bare `commit_author` stand-in, not a real identity
model (that is a separate, much larger, not-yet-started effort — v1's own
ADR-0067 was out of scope there too). Resolution order:

1. `BUILD_REQUESTEDFOR` / `BUILD_REQUESTEDFOREMAIL` — Azure Pipelines' own
   CI identity vars (`config-deploy`'s actual, real CI). A future
   GitHub Actions consumer would add `GITHUB_ACTOR` to this same chain.
2. `getpass.getuser()` — the OS login, for a local/manual run.
3. The literal string `"unknown"` — never an exception; a missing actor
   must never block a deploy.

Two steps *above* this one exist in v1's real chain (a control-plane
session; the signed-in cloud CLI identity) but need infrastructure v2
doesn't have yet (an OIDC login/RBAC server; `azure_cli`/`aws_cli`/
`gcloud_cli` integrations) and no real consumer configures either today —
see the design doc's own note on this. Revisit once v2 grows its own
cloud-CLI integrations for other reasons.
"""

import getpass
import os


def resolve_actor() -> str:
    """Return the best-effort actor identity for this process. Never raises."""
    actor = os.environ.get("BUILD_REQUESTEDFOR") or os.environ.get("BUILD_REQUESTEDFOREMAIL")
    if actor:
        return actor
    return _safe_getpass() or "unknown"


def _safe_getpass() -> str | None:
    try:
        return getpass.getuser()
    except Exception:
        return None
