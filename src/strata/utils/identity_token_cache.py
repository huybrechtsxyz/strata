"""On-disk token cache shared by identity-provider integrations (ADR-0067).

Unlike ``AzureCLIIntegration``/``GCloudCLIIntegration``'s in-process token caches
(which rely on ``az``/``gcloud`` persisting the actual login to disk themselves),
an identity-provider integration has no external tool maintaining a session — strata
itself must persist it across CLI invocations for the "log in once" experience to work.

One JSON file per integration name under ``~/.strata/identity/``, permissioned so only
the current user can read it. This is a plain utility (no controller/integration
dependency) so both layers can use it without violating strata's layered architecture
(ADR-0003): identity-provider integrations write to it, ``IdentityController`` reads
from it for actor resolution.
"""

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional

from strata.logger import get_logger

logger = get_logger(__name__)

_CACHE_DIR = Path.home() / ".strata" / "identity"


def _cache_path(name: str) -> Path:
    # Integration names are already validated as safe identifiers (PlatformName),
    # but sanitize defensively since this touches the filesystem.
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
    return _CACHE_DIR / f"{safe_name}.json"


def load_token(name: str) -> Optional[Dict[str, Any]]:
    """Return the cached token payload for *name*, or None if absent/unreadable."""
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("identity_token_cache_read_failed", name=name, error=str(exc))
        return None


def save_token(name: str, payload: Dict[str, Any]) -> None:
    """Persist *payload* for *name*, creating the cache directory if needed.

    The file is written with 0600 permissions (owner read/write only) since it
    contains bearer/refresh tokens.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(name)
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # best-effort on platforms without POSIX permission bits


def clear_token(name: str) -> None:
    """Remove the cached token for *name*, if present."""
    path = _cache_path(name)
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("identity_token_cache_clear_failed", name=name, error=str(exc))
