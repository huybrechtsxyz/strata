"""Resolver for deployment ``spec.extends`` inheritance chains.

Loads a deployment YAML file and recursively resolves any ``spec.extends``
reference, producing a single merged raw dict that can be passed to
``DeploymentService(data=...)`` for Pydantic validation.

Merge rules (child always wins):
- **Top-level spec fields** — child value replaces base value.
- **``stages``** — merged by ``name``.  Child entries override matching base
  entries field-by-field; new names are appended.
- **``environments``** — base list + child list (append).
- **``partial`` and ``extends``** — consumed during resolution and stripped
  from the returned dict so the caller sees a clean, fully-resolved payload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from strata.logger import get_logger
from strata.utils.system import resolve_path

logger = get_logger(__name__)


class DeploymentExtensionResolver:
    """Resolves ``spec.extends`` chains for deployment files.

    Usage::

        resolver = DeploymentExtensionResolver(work_path, repo_map)
        merged_raw = resolver.resolve(file_path)
        service = DeploymentService(path=str(file_path), data=merged_raw)
    """

    def __init__(
        self,
        work_path: Path,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self._work_path = work_path
        self._repo_map: Dict[str, str] = repo_map or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def needs_resolution(self, file_path: Path) -> bool:
        """Return True if the file has a ``spec.extends`` reference."""
        raw = self._load_raw(file_path)
        return bool((raw.get("spec") or {}).get("extends"))

    def resolve(
        self,
        file_path: Path,
        _visited: Optional[frozenset[str]] = None,
    ) -> Dict[str, Any]:
        """Load *file_path*, resolve its ``spec.extends`` chain, and return the
        merged raw dict.

        The returned dict has ``spec.partial`` and ``spec.extends`` stripped —
        the merged result is always treated as a concrete, non-partial document
        for the purposes of Pydantic validation.

        Raises:
            ValueError: Circular ``extends`` reference detected.
            FileNotFoundError: A referenced base file does not exist.
        """
        visited = _visited or frozenset()
        canonical = str(file_path.resolve())

        if canonical in visited:
            raise ValueError(f"Circular extends reference detected: '{file_path}' is already in the resolution chain.")

        visited = visited | {canonical}
        child_raw = self._load_raw(file_path)
        child_spec: Dict[str, Any] = dict(child_raw.get("spec") or {})

        extends_ref: Optional[str] = child_spec.get("extends")
        if not extends_ref:
            # Nothing to extend — return as-is (strip partial/extends keys)
            child_spec.pop("partial", None)
            child_spec.pop("extends", None)
            return {**child_raw, "spec": child_spec}

        # Resolve the base file path — relative to the workspace root, not
        # file_path's own directory. ADR-0039: "spec.extends accepts a single
        # @repo/path reference (same resolution rules as all other cross-file
        # references in strata)" — matches BaseService._resolve_file_path().
        try:
            base_path = resolve_path(
                str(self._work_path),
                extends_ref,
                repo_map=self._repo_map,
            )
        except ValueError as exc:
            raise ValueError(f"Cannot resolve extends reference '{extends_ref}' in '{file_path}': {exc}") from exc

        if not base_path.exists():
            raise FileNotFoundError(f"Base file '{base_path}' referenced by extends in '{file_path}' does not exist.")

        # Recursively resolve the base
        base_merged = self.resolve(base_path, _visited=visited)
        base_spec: Dict[str, Any] = dict(base_merged.get("spec") or {})

        # Merge child onto base
        merged_spec = self._merge_specs(base_spec, child_spec)

        # Strip resolution-only keys from the merged result
        merged_spec.pop("partial", None)
        merged_spec.pop("extends", None)

        # Preserve top-level meta from the child (name, labels, annotations)
        merged_meta = {**(base_merged.get("meta") or {}), **(child_raw.get("meta") or {})}

        logger.debug(
            "Resolved extends chain",
            child=str(file_path),
            base=str(base_path),
        )

        return {
            **child_raw,
            "meta": merged_meta,
            "spec": merged_spec,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_raw(file_path: Path) -> Dict[str, Any]:
        raw = file_path.read_text(encoding="utf-8")
        doc = yaml.safe_load(raw)
        if not isinstance(doc, dict):
            raise ValueError(f"Expected a YAML mapping in '{file_path}', got {type(doc).__name__}.")
        return doc

    @staticmethod
    def _merge_specs(
        base: Dict[str, Any],
        child: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge *child* spec onto *base* spec following ADR 0039 rules."""
        merged = dict(base)

        for key, child_value in child.items():
            if key == "stages":
                merged["stages"] = DeploymentExtensionResolver._merge_stages(
                    base.get("stages") or [],
                    child_value or [],
                )
            elif key == "environments":
                merged["environments"] = DeploymentExtensionResolver._append_environments(
                    base.get("environments") or [],
                    child_value or [],
                )
            else:
                # Top-level field replacement — child wins
                merged[key] = child_value

        return merged

    @staticmethod
    def _merge_stages(
        base_stages: List[Dict[str, Any]],
        child_stages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merge stage lists by ``name``.

        Child entries override matching base entries field-by-field; new names
        are appended after the base list.
        """
        result: List[Dict[str, Any]] = [dict(s) for s in base_stages]
        base_index: Dict[str, int] = {s["name"]: i for i, s in enumerate(result) if "name" in s}

        for child_stage in child_stages:
            name = child_stage.get("name")
            if name and name in base_index:
                # Override existing stage fields
                result[base_index[name]] = {**result[base_index[name]], **child_stage}
            else:
                # New stage — append
                result.append(dict(child_stage))

        return result

    @staticmethod
    def _append_environments(
        base_envs: List[Any],
        child_envs: List[Any],
    ) -> List[Any]:
        """Return base environments followed by child environments."""
        return list(base_envs) + list(child_envs)
