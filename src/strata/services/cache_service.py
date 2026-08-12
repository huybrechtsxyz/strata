#!/usr/bin/env python3
"""Resolved-model cache service (ADR-0026).

SQLite-backed cache at ``.strata/cache/model/cache.db`` for expensive
fleet-wide/single-deployment model resolution. See
``docs/decisions/0026-resolved-model-cache.md`` for the full design.

Correctness contract: an entry is fresh if and only if the sha256 hash of the
current contents of every contributing input file matches the hash recorded
at warm time (``cache_key``). This is *provably* fresh, not TTL-based — the
same guarantee documented for a future store-value cache does not apply here
(see ADR-0026 OQ-4).

All public methods are non-fatal by design: a corrupt or unreadable cache is
treated as a cold cache and logged at WARNING, never raised to the caller.
The cache is a performance optimisation, never a source of truth — every
entry is fully rebuildable from the committed YAML files.
"""

import hashlib
import json
import sqlite3
import zlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from strata.logger import get_logger
from strata.utils.config import get_model_cache_db_path, get_model_cache_dir
from strata.utils.version import get_version

# Bump when the shape of cached ``resolved`` payloads changes incompatibly.
# All existing rows are treated as stale (not deleted) until overwritten by a warm.
CACHE_SCHEMA_VERSION = 1


class CacheStatus:
    """Result values for :meth:`CacheService.status`."""

    FRESH = "fresh"
    STALE = "stale"
    COLD = "cold"


class CacheService:
    """SQLite-backed cache for fully-resolved deployment models."""

    def __init__(self, work_path: Path) -> None:
        self._work_path = Path(work_path)
        self._db_path = get_model_cache_db_path(self._work_path)
        self.logger = get_logger(self.__class__.__module__)

    @property
    def db_path(self) -> Path:
        return self._db_path

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        get_model_cache_dir(self._work_path).mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._migrate_legacy_schema(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                kind            TEXT NOT NULL,
                cache_version   INTEGER NOT NULL,
                strata_version  TEXT NOT NULL,
                cache_key       TEXT NOT NULL,
                written_at      TEXT NOT NULL,
                resolved        BLOB NOT NULL,
                UNIQUE (name, kind, cache_version)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_inputs (
                cache_id    INTEGER NOT NULL REFERENCES cache(id) ON DELETE CASCADE,
                file_path   TEXT NOT NULL,
                PRIMARY KEY (cache_id, file_path)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_kind ON cache (kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_name_kind ON cache (name, kind)")
        return conn

    @staticmethod
    def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
        """Drop the pre-surrogate-key ``cache``/``cache_inputs`` tables if found.

        The original schema used ``name`` as the sole PRIMARY KEY, which cannot
        support more than one cache ``kind`` per deployment name (e.g. the
        ``"deployment"`` build-artifact cache and the ``"resolved_environment"``
        cache both keyed by the same deployment name). Any local cache built
        under that schema is dropped and rebuilt on next warm — the cache is
        always fully rebuildable from committed YAML, so this is safe and
        requires no data migration.
        """
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(cache)").fetchall()}
        except sqlite3.Error:
            return
        if cols and "id" not in cols:
            conn.execute("DROP TABLE IF EXISTS cache_inputs")
            conn.execute("DROP TABLE IF EXISTS cache")

    # ------------------------------------------------------------------
    # Cache key computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_cache_key(input_paths: List[str]) -> Optional[str]:
        """Compute a sha256 hash over the sorted contents of *input_paths*.

        Returns ``None`` if any input file cannot be read — callers should
        treat that as "not cacheable right now" and resolve live.
        """
        hasher = hashlib.sha256()
        try:
            for path in sorted(set(input_paths)):
                with open(path, "rb") as fh:
                    hasher.update(fh.read())
        except OSError:
            return None
        return hasher.hexdigest()

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def get(self, name: str, kind: str, cache_key: str) -> Optional[Dict[str, Any]]:
        """Return the cached resolved model for *(name, kind)* if fresh, else ``None``.

        Two-step read: cheap staleness check first (no BLOB deserialisation
        unless the key matches). *kind* scopes the lookup so different cache
        concepts for the same deployment name (e.g. the build-artifact
        ``"deployment"`` cache and the ``"resolved_environment"`` cache) never
        collide.
        """
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT cache_key, cache_version, resolved FROM cache WHERE name = ? AND kind = ?",
                    (name, kind),
                ).fetchone()
        except sqlite3.Error as exc:
            self.logger.warning("Cache read failed, treating as cold", name=name, kind=kind, error=str(exc))
            return None

        if row is None:
            return None

        stored_key, stored_version, resolved_blob = row
        if stored_version != CACHE_SCHEMA_VERSION or stored_key != cache_key:
            return None

        try:
            return json.loads(zlib.decompress(resolved_blob).decode("utf-8"))
        except Exception as exc:
            self.logger.warning("Cache entry corrupt, treating as cold", name=name, kind=kind, error=str(exc))
            return None

    def status(self, name: str, kind: str, cache_key: Optional[str] = None) -> str:
        """Return :class:`CacheStatus` for *(name, kind)* without deserialising ``resolved``.

        When *cache_key* is ``None`` only presence is checked (used by ``strata
        cache status`` for entries whose source files can no longer be found).
        """
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT cache_key, cache_version FROM cache WHERE name = ? AND kind = ?",
                    (name, kind),
                ).fetchone()
        except sqlite3.Error:
            return CacheStatus.COLD

        if row is None:
            return CacheStatus.COLD

        stored_key, stored_version = row
        if cache_key is None:
            return CacheStatus.FRESH
        if stored_version != CACHE_SCHEMA_VERSION or stored_key != cache_key:
            return CacheStatus.STALE
        return CacheStatus.FRESH

    def list_entries(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return metadata (no ``resolved`` payload) for every cached entry.

        With *kind*, restricts the listing to entries of that cache kind.
        """
        try:
            with closing(self._connect()) as conn:
                if kind is None:
                    rows = conn.execute(
                        "SELECT name, kind, cache_version, strata_version, cache_key, written_at, "
                        "length(resolved) AS size_bytes FROM cache ORDER BY name, kind"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT name, kind, cache_version, strata_version, cache_key, written_at, "
                        "length(resolved) AS size_bytes FROM cache WHERE kind = ? ORDER BY name",
                        (kind,),
                    ).fetchall()
        except sqlite3.Error as exc:
            self.logger.warning("Cache list failed", error=str(exc))
            return []

        return [
            {
                "name": r[0],
                "kind": r[1],
                "cache_version": r[2],
                "strata_version": r[3],
                "cache_key": r[4],
                "written_at": r[5],
                "size_bytes": r[6],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def warm(
        self,
        name: str,
        kind: str,
        cache_key: str,
        resolved: Dict[str, Any],
        input_paths: List[str],
    ) -> bool:
        """Persist *resolved* for *name*, replacing any existing entry.

        Returns ``True`` on success, ``False`` on failure (logged, non-fatal).
        """
        try:
            payload = zlib.compress(json.dumps(resolved).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            self.logger.warning(
                "Cache warm skipped: resolved model is not JSON-serialisable", name=name, error=str(exc)
            )
            return False

        written_at = datetime.now(timezone.utc).isoformat()
        try:
            with closing(self._connect()) as conn:
                with conn:
                    cur = conn.execute(
                        "INSERT OR REPLACE INTO cache "
                        "(name, kind, cache_version, strata_version, cache_key, written_at, resolved) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (name, kind, CACHE_SCHEMA_VERSION, get_version(), cache_key, written_at, payload),
                    )
                    cache_id = cur.lastrowid
                    conn.execute("DELETE FROM cache_inputs WHERE cache_id = ?", (cache_id,))
                    conn.executemany(
                        "INSERT OR IGNORE INTO cache_inputs (cache_id, file_path) VALUES (?, ?)",
                        [(cache_id, p) for p in sorted(set(input_paths))],
                    )
            return True
        except sqlite3.Error as exc:
            self.logger.warning(
                "Cache warm failed (non-fatal, live result still returned)", name=name, kind=kind, error=str(exc)
            )
            return False

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate(self, name: str, kind: Optional[str] = None) -> None:
        """Remove the cache entry for *name*, if any.

        With *kind*, only that cache kind is removed; without it, every kind
        cached under *name* is removed.
        """
        try:
            with closing(self._connect()) as conn:
                with conn:
                    if kind is None:
                        conn.execute("DELETE FROM cache WHERE name = ?", (name,))
                    else:
                        conn.execute("DELETE FROM cache WHERE name = ? AND kind = ?", (name, kind))
        except sqlite3.Error as exc:
            self.logger.warning("Cache invalidate failed", name=name, kind=kind, error=str(exc))

    def invalidate_all(self) -> None:
        """Remove every cache entry (``strata cache clear``)."""
        try:
            with closing(self._connect()) as conn:
                with conn:
                    conn.execute("DELETE FROM cache")
        except sqlite3.Error as exc:
            self.logger.warning("Cache clear failed", error=str(exc))

    def invalidate_by_path_prefix(self, path_prefix: str) -> int:
        """Invalidate every entry that referenced a file under *path_prefix*.

        Used by ``strata repo sync`` to invalidate all deployments whose
        workspace/environment files came from a synced remote in one query.
        Returns the number of entries invalidated.
        """
        try:
            with closing(self._connect()) as conn:
                with conn:
                    cur = conn.execute(
                        "DELETE FROM cache WHERE id IN (SELECT cache_id FROM cache_inputs WHERE file_path LIKE ?)",
                        (f"{path_prefix}%",),
                    )
                    return cur.rowcount
        except sqlite3.Error as exc:
            self.logger.warning("Cache invalidate_by_path_prefix failed", prefix=path_prefix, error=str(exc))
            return 0

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_json(self) -> Dict[str, Any]:
        """Return the full cache (including decompressed ``resolved`` payloads) as a dict.

        Used by ``strata cache export`` for debugging a binary cache file.
        """
        entries: Dict[str, Any] = {}
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT name, kind, cache_version, strata_version, cache_key, written_at, resolved FROM cache"
                ).fetchall()
        except sqlite3.Error as exc:
            self.logger.warning("Cache export failed", error=str(exc))
            return entries

        for name, kind, cache_version, strata_version, cache_key, written_at, resolved_blob in rows:
            try:
                resolved = json.loads(zlib.decompress(resolved_blob).decode("utf-8"))
            except Exception:
                resolved = None
            # Keyed by "name::kind" (not just name) — a deployment can have more than
            # one cache kind (e.g. "deployment" build artifact + "resolved_environment").
            entries[f"{name}::{kind}"] = {
                "name": name,
                "kind": kind,
                "cache_version": cache_version,
                "strata_version": strata_version,
                "cache_key": cache_key,
                "written_at": written_at,
                "resolved": resolved,
            }
        return entries

    # ------------------------------------------------------------------
    # High-level convenience API
    # ------------------------------------------------------------------

    def get_or_resolve(
        self,
        name: str,
        kind: str,
        input_paths: List[str],
        resolve_fn: Callable[[], Dict[str, Any]],
        no_cache: bool = False,
        refresh_cache: bool = False,
    ) -> Tuple[Dict[str, Any], str]:
        """Return ``(resolved, indicator)`` for *name*, using the cache when possible.

        ``indicator`` is one of ``"cached"``, ``"refreshed"``, or ``"no-cache"`` —
        intended for callers to print per-row (e.g. ``promote matrix``).

        Auto-warms transparently on stale/cold; callers never need to check
        whether the cache is warm themselves.
        """
        if no_cache:
            return resolve_fn(), "no-cache"

        cache_key = self.compute_cache_key(input_paths)
        if cache_key is None:
            # Inputs unreadable right now — fall back to live resolve without caching.
            return resolve_fn(), "no-cache"

        if not refresh_cache:
            cached = self.get(name, kind, cache_key)
            if cached is not None:
                return cached, "cached"

        resolved = resolve_fn()
        self.warm(name, kind, cache_key, resolved, input_paths)
        return resolved, "refreshed"
