#!/usr/bin/env python3
"""Resolve a ``strata://`` URI to a concrete ``{file, line}`` (ADR-0034).

This is the counterpart to the URI's structural design: because the URI carries
no line number, something has to find the line on demand. Doing it here rather
than baking it into the diagram means a reference keeps working when the YAML
around it is reformatted, and means the lookup works headless — in CI, in any
editor, not just VS Code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from strata.controllers.base_controller import BaseController
from strata.utils.strata_uri import StrataUri, UriError, parse_uri

# Fields that identify an item within a collection. 'key' covers environment
# secrets, which are keyed rather than named; 'gate' covers promotion-record
# gate results (PromotionGateResultModel has no 'name'/'key' of its own).
IDENTITY_FIELDS = ("name", "key", "gate")

# Collection names that do not follow the <kind> + 's' convention.
_COLLECTION_ALIASES: Dict[str, Tuple[str, ...]] = {
    "dns": ("dns_zones",),
    "policy": ("policies",),
    "repository": ("repositories",),
}

# Hidden directories to scan anyway — '.strata/' holds machine-generated
# documents (promotion records, deploy logs) with real 'kind'/'meta.name'
# structure worth resolving, unlike '.git' or editor-specific dot-dirs.
_SCANNABLE_HIDDEN_DIRS = {".strata"}


def _collection_names(child_kind: str) -> Tuple[str, ...]:
    """Collection keys a child of *child_kind* may live under."""
    return (child_kind, f"{child_kind}s") + _COLLECTION_ALIASES.get(child_kind, ())


class DiagramResolveController(BaseController):
    """Turn a ``strata://`` URI into the file and line it points at."""

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = Path(work_path).resolve()

    def resolve(self, uri_text: str) -> Optional[Dict[str, Any]]:
        """Resolve *uri_text* to a location.

        Args:
            uri_text: A ``strata://`` URI.

        Returns:
            ``{"uri", "kind", "name", "child_kind", "child_name", "file", "line"}``
            with ``line`` omitted (``None``) for file references, which point at a
            document rather than a position inside one. ``None`` when the URI is
            malformed or names nothing in the workspace.
        """
        try:
            uri = parse_uri(uri_text)
        except UriError as exc:
            self._add_error(str(exc))
            return None

        location = self._resolve_file(uri) if uri.is_file else self._resolve_document(uri)
        if location is None:
            return None

        file_path, line = location
        return {
            "uri": str(uri),
            "kind": uri.kind,
            "name": uri.name,
            "child_kind": uri.child_kind,
            "child_name": uri.child_name,
            "file": self._relative(file_path),
            "line": line,
        }

    # ─── Resolution ───────────────────────────────────────────────────────────

    def _resolve_file(self, uri: StrataUri) -> Optional[Tuple[Path, Optional[int]]]:
        path = self._work_path / uri.name
        if not path.is_file():
            self._add_error(f"'{uri}' points at '{uri.name}', which does not exist in the workspace.")
            return None
        return path, None

    def _resolve_document(self, uri: StrataUri) -> Optional[Tuple[Path, Optional[int]]]:
        for path, root in self._iter_documents():
            if self._document_identity(root) != (uri.kind, uri.name):
                continue
            if uri.child_kind is None:
                return path, self._meta_name_line(root)
            line = _find_child(root, uri.child_kind, uri.child_name or "")
            if line is not None:
                return path, line
            self._add_error(
                f"'{uri}' resolved to '{self._relative(path)}', but that document has no "
                f"{uri.child_kind} named '{uri.child_name}'. "
                f"Looked under {list(_collection_names(uri.child_kind))}."
            )
            return None

        self._add_error(
            f"'{uri}' names no {uri.kind} called '{uri.name}' in the workspace. "
            f"The object may have been renamed or removed."
        )
        return None

    # ─── Workspace scanning ───────────────────────────────────────────────────

    def _iter_documents(self) -> Iterable[Tuple[Path, yaml.MappingNode]]:
        """Yield every parseable strata document with its line-marked YAML tree."""
        for path in self._iter_yaml_files():
            try:
                with open(path, encoding="utf-8") as handle:
                    root = yaml.compose(handle)
            except Exception:
                # An unparseable or non-strata file is simply not a candidate.
                continue
            if isinstance(root, yaml.MappingNode):
                yield path, root

    def _iter_yaml_files(self) -> List[Path]:
        """List workspace YAML files, skipping hidden directories except ``.strata/``."""
        files: List[Path] = []
        for pattern in ("**/*.yaml", "**/*.yml"):
            for path in self._work_path.glob(pattern):
                parts = path.relative_to(self._work_path).parts
                if any(part.startswith(".") and part not in _SCANNABLE_HIDDEN_DIRS for part in parts):
                    continue
                files.append(path)
        return sorted(files)

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._work_path)).replace("\\", "/")
        except ValueError:
            return str(path)

    # ─── Node inspection ──────────────────────────────────────────────────────

    @staticmethod
    def _document_identity(root: yaml.MappingNode) -> Tuple[Optional[str], Optional[str]]:
        kind = _scalar(root, "kind")
        meta = _mapping_value(root, "meta")
        name = _scalar(meta, "name") if meta is not None else None
        return kind, name

    @staticmethod
    def _meta_name_line(root: yaml.MappingNode) -> Optional[int]:
        meta = _mapping_value(root, "meta")
        if meta is None:
            return None
        for key_node, _value_node in meta.value:
            if key_node.value == "name":
                return int(key_node.start_mark.line) + 1
        return None


# ─── Module-level node helpers ────────────────────────────────────────────────


def _mapping_value(node: Optional[yaml.MappingNode], key: str) -> Optional[yaml.MappingNode]:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in node.value:
        if key_node.value == key and isinstance(value_node, yaml.MappingNode):
            return value_node
    return None


def _scalar(node: Optional[yaml.MappingNode], key: str) -> Optional[str]:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in node.value:
        if key_node.value == key and isinstance(value_node, yaml.ScalarNode):
            return str(value_node.value)
    return None


def _find_child(node: yaml.Node, child_kind: str, child_name: str) -> Optional[int]:
    """Find the 1-based line of *child_name* within a *child_kind* collection.

    Searches the whole tree rather than a fixed path, so nested collections —
    a module declared under ``spec.resources[].modules`` — resolve without a
    per-kind path table.
    """
    collections = _collection_names(child_kind)

    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            if key_node.value in collections and isinstance(value_node, yaml.SequenceNode):
                line = _match_in_sequence(value_node, child_name)
                if line is not None:
                    return line
            line = _find_child(value_node, child_kind, child_name)
            if line is not None:
                return line
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            line = _find_child(item, child_kind, child_name)
            if line is not None:
                return line
    return None


def _match_in_sequence(sequence: yaml.SequenceNode, child_name: str) -> Optional[int]:
    """Return the line of the identity field of the matching item, if any."""
    for item in sequence.value:
        if not isinstance(item, yaml.MappingNode):
            continue
        for key_node, value_node in item.value:
            if (
                key_node.value in IDENTITY_FIELDS
                and isinstance(value_node, yaml.ScalarNode)
                and str(value_node.value) == child_name
            ):
                return int(key_node.start_mark.line) + 1
    return None
