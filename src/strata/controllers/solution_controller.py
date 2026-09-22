#!/usr/bin/env python3
"""Solution root discovery, document indexing and resolution (ADR-0015).

The loading layer every parked Phase 2 validator has been waiting for. It
does three things:

1. **Find the solution root** — walk up from a starting directory to the
   nearest `strata.yaml`, like `go.mod`/`Cargo.toml`/`package.json`.
2. **Discover documents** — recursively scan the root, take each document's
   `kind` from its own field (folder layout is irrelevant, the
   `kubectl apply -R -f` model), validate it through its existing service,
   and index it by identity.
3. **Resolve references** — look documents up by `(remote, kind, name)`.

Deliberate behaviours, all from ADR-0015:

- A YAML file without a strata `apiVersion` is **skipped silently**. Real
  solution trees are full of Helm values, CI pipelines and k8s manifests;
  erroring on them would make discovery unusable.
- A file *with* a strata `apiVersion` but an unrecognised `kind` is an
  **error** — that is a typo, not a foreign file.
- Duplicate `(remote, kind, name)` is a **hard error**, never last-write-wins.
- Recursion stops at any nested `strata.yaml`: that is a different solution.
  Self-maintaining, unlike a hardcoded deny-list.
- Every entry carries the path it came from. With `file:` gone from the
  schema, provenance lives only here, and every error message needs it.

The index key includes a `remote` slot that is always `None` today. Remote
qualified references (`@platform/azure`) are not implemented — but the key
is shaped for them now so adding them later is additive rather than a
rewrite (ADR-0015).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from strata.models.common_models import PlatformBaseModel, PlatformKind
from strata.models.solution_model import SolutionModel
from strata.services.base_service import BaseService
from strata.services.configuration_service import ConfigurationService
from strata.services.dns_service import DnsService
from strata.services.firewall_service import FirewallService
from strata.services.integration_service import IntegrationService
from strata.services.module_service import ModuleService
from strata.services.namespace_service import NamespaceService
from strata.services.network_service import NetworkService
from strata.services.provider_config_service import ProviderConfigService
from strata.services.provider_service import ProviderService
from strata.services.resource_service import ResourceService
from strata.services.solution_service import SolutionService
from strata.services.topology_config_service import TopologyConfigService
from strata.services.topology_service import TopologyService
from strata.services.workspace_service import WorkspaceService

#: The solution manifest filename — both the root marker and the recursion
#: boundary (a nested one means a different solution).
MANIFEST_FILENAME = "strata.yaml"

#: Every `apiVersion` value that marks a document as ours. Anything else (or
#: nothing) means "not a strata document" and is skipped without complaint.
_STRATA_API_PREFIXES = ("strata.",)

#: Directories never descended into, regardless of configuration. Tool
#: caches, virtualenvs, build output, and `.strata/` (runtime state — v2
#: declares it runtime-only, ADR-0015). This is the floor;
#: `spec.discovery.exclude` adds to it and cannot remove from it, so a
#: user-supplied pattern can never re-enable scanning `.git`.
#:
#: Deliberately NOT here: `.archive/`, `repos/` and similar. Those are local
#: conventions observed in particular repositories, not universals — a
#: solution that wants them skipped declares them in `spec.discovery.exclude`.
#: `repos/` in particular is already handled structurally: a checked-out
#: remote carries its own `strata.yaml`, which stops recursion.
DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        ".strata",
        "build",
        "dist",
    }
)

_YAML_SUFFIXES = (".yaml", ".yml")

#: Which service validates which kind. The service already knows its model
#: and owns Phase 1/Phase 2, so the controller does not duplicate either.
SERVICE_BY_KIND: dict[PlatformKind, type[BaseService[Any]]] = {
    PlatformKind.SOLUTION: SolutionService,
    PlatformKind.CONFIGURATION: ConfigurationService,
    PlatformKind.PROVIDERCONFIG: ProviderConfigService,
    PlatformKind.TOPOLOGYCONFIG: TopologyConfigService,
    PlatformKind.PROVIDER: ProviderService,
    PlatformKind.RESOURCE: ResourceService,
    PlatformKind.DNS: DnsService,
    PlatformKind.NETWORK: NetworkService,
    PlatformKind.FIREWALL: FirewallService,
    PlatformKind.MODULE: ModuleService,
    PlatformKind.NAMESPACE: NamespaceService,
    PlatformKind.TOPOLOGY: TopologyService,
    PlatformKind.WORKSPACE: WorkspaceService,
    PlatformKind.INTEGRATION: IntegrationService,
}


@dataclass(frozen=True)
class DocumentRef:
    """Identity of a document: what it is and what it's called.

    `remote` is always `None` today; it exists so remote-qualified
    references can be added without rekeying the index (ADR-0015).
    """

    kind: PlatformKind
    name: str
    remote: str | None = None

    def __str__(self) -> str:
        prefix = f"@{self.remote}/" if self.remote else ""
        return f"{prefix}{self.kind.value}/{self.name}"


@dataclass(frozen=True)
class IndexEntry:
    """A validated document plus where it was found."""

    ref: DocumentRef
    model: PlatformBaseModel
    source: Path


class DocumentIndex:
    """Documents keyed by identity, with the path each came from."""

    def __init__(self) -> None:
        self._entries: dict[DocumentRef, IndexEntry] = {}

    def add(self, entry: IndexEntry) -> None:
        """Add an entry. Callers must check `get()` first — duplicates raise."""
        if entry.ref in self._entries:
            existing = self._entries[entry.ref]
            raise ValueError(
                f"Duplicate {entry.ref}: defined in both '{existing.source}' and '{entry.source}'"
            )
        self._entries[entry.ref] = entry

    def get(self, kind: PlatformKind, name: str, remote: str | None = None) -> IndexEntry | None:
        """Return the entry for this identity, or None."""
        return self._entries.get(DocumentRef(kind=kind, name=name, remote=remote))

    def all_of(self, kind: PlatformKind) -> list[IndexEntry]:
        """Every indexed document of one kind, sorted by name."""
        return sorted((e for e in self._entries.values() if e.ref.kind is kind), key=lambda e: e.ref.name)

    def names_of(self, kind: PlatformKind) -> set[str]:
        """Every indexed name for one kind."""
        return {e.ref.name for e in self._entries.values() if e.ref.kind is kind}

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, ref: DocumentRef) -> bool:
        return ref in self._entries


def find_solution_root(start: Path) -> Path | None:
    """Walk up from `start` to the nearest directory holding a `strata.yaml`.

    Returns None when there is none, which means "not inside a solution".
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / MANIFEST_FILENAME).is_file():
            return candidate
    return None


def _is_strata_document(raw: object) -> bool:
    """True when `raw` is a mapping carrying a strata `apiVersion`."""
    if not isinstance(raw, dict):
        return False
    api_version = raw.get("apiVersion")
    return isinstance(api_version, str) and api_version.startswith(_STRATA_API_PREFIXES)


class SolutionController:
    """Loads a solution: its manifest, then every document under its root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.solution: SolutionModel | None = None
        self.index = DocumentIndex()
        self.errors: list[str] = []

    @property
    def _exclude_patterns(self) -> tuple[str, ...]:
        """User-declared exclude globs from the manifest, if any."""
        if self.solution is None or self.solution.spec.discovery is None:
            return ()
        return tuple(self.solution.spec.discovery.exclude or ())

    def _is_excluded(self, path: Path) -> bool:
        """True when `path` matches a user-declared exclude pattern.

        Matching is fnmatch-style against the solution-relative POSIX path.
        Note fnmatch's `*` also spans `/`, so both `templates` (the directory
        itself) and `templates/**` (its contents) behave as expected.
        """
        patterns = self._exclude_patterns
        if not patterns:
            return False
        try:
            relative = path.resolve().relative_to(self.root).as_posix()
        except ValueError:  # outside the root — should not happen, but be safe
            return False
        return any(fnmatch(relative, pattern) for pattern in patterns)

    @classmethod
    def from_directory(cls, start: Path) -> "SolutionController | None":
        """Build a controller for the solution containing `start`, if any."""
        root = find_solution_root(start)
        return cls(root) if root is not None else None

    def load(self) -> tuple[bool, list[str]]:
        """Load the manifest, then discover and index every document.

        Returns `(is_valid, errors)`. Errors accumulate — a bad document does
        not stop the scan, so one run reports everything wrong.
        """
        self.errors = []
        self.index = DocumentIndex()

        self._load_manifest()
        for path in self._walk():
            self._load_file(path)

        return (not self.errors), self.errors

    def _load_manifest(self) -> None:
        """Validate `strata.yaml` itself. Its own kind is not indexed."""
        manifest = self.root / MANIFEST_FILENAME
        if not manifest.is_file():
            self.errors.append(f"No {MANIFEST_FILENAME} found at solution root '{self.root}'")
            return

        service = SolutionService(path=str(manifest))
        is_valid, errors = service.validate()
        if not is_valid:
            self.errors.extend(f"{manifest}: {e}" for e in errors)
            return
        self.solution = service.model

    def _walk(self) -> Iterator[Path]:
        """Yield every candidate YAML file under the root.

        Skips built-in ignored directories and anything matching a
        user-declared `spec.discovery.exclude` pattern, and does not descend
        into a directory holding its own `strata.yaml` — that is a different
        solution.
        """
        stack = [self.root]
        while stack:
            current = stack.pop()
            try:
                children = sorted(current.iterdir())
            except OSError as exc:  # unreadable directory — report, keep going
                self.errors.append(f"Cannot read directory '{current}': {exc}")
                continue

            for child in children:
                if child.is_dir():
                    if child.name in DEFAULT_IGNORED_DIRS:
                        continue
                    if (child / MANIFEST_FILENAME).is_file():
                        continue  # nested solution — boundary
                    if self._is_excluded(child):
                        continue
                    stack.append(child)
                elif child.suffix in _YAML_SUFFIXES and child.name != MANIFEST_FILENAME:
                    if self._is_excluded(child):
                        continue
                    yield child

    def _load_file(self, path: Path) -> None:
        """Parse one file and index every strata document inside it."""
        try:
            documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError) as exc:
            self.errors.append(f"{path}: cannot parse YAML: {exc}")
            return

        for position, raw in enumerate(documents):
            if raw is None:
                continue
            if not _is_strata_document(raw):
                continue  # not ours — skip silently
            self._index_document(path, position, raw)

    def _index_document(self, path: Path, position: int, raw: dict[str, Any]) -> None:
        """Validate one strata document and add it to the index."""
        where = f"{path}" if position == 0 else f"{path} (document {position + 1})"

        kind_value = raw.get("kind")
        try:
            kind = PlatformKind(kind_value)
        except ValueError:
            known = ", ".join(sorted(k.value for k in PlatformKind))
            self.errors.append(f"{where}: unknown kind '{kind_value}'. Known kinds: {known}")
            return

        if kind is PlatformKind.SOLUTION:
            self.errors.append(
                f"{where}: a nested 'solution' document is not allowed — "
                f"the manifest at the root is the only one."
            )
            return

        service_class = SERVICE_BY_KIND[kind]
        service = service_class(data=raw)
        is_valid, errors = service.validate()
        if not is_valid or service.model is None:
            self.errors.extend(f"{where}: {e}" for e in errors)
            return

        name = str(service.model.meta.name)
        entry = IndexEntry(ref=DocumentRef(kind=kind, name=name), model=service.model, source=path)
        try:
            self.index.add(entry)
        except ValueError as exc:
            self.errors.append(str(exc))
