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
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from strata.models.common_models import PlatformBaseModel, PlatformKind
from strata.models.solution_model import SolutionModel
from strata.services.artifact_service import ArtifactService
from strata.services.asset_service import AssetService
from strata.services.attributeconfig_service import AttributeConfigService
from strata.services.base_service import BaseService
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService, DeploymentTemplateService
from strata.services.dns_service import DnsService
from strata.services.environment_service import EnvironmentService, EnvironmentTemplateService
from strata.services.firewall_service import FirewallService
from strata.services.integration_service import IntegrationService
from strata.services.module_service import ModuleService
from strata.services.namespace_service import NamespaceService
from strata.services.network_service import NetworkService
from strata.services.parameterset_service import ParameterSetService
from strata.services.provider_config_service import ProviderConfigService
from strata.services.provider_service import ProviderService
from strata.services.resource_service import ResourceService
from strata.services.solution_service import SolutionService
from strata.services.tenant_service import TenantService, TenantTemplateService
from strata.services.topology_config_service import TopologyConfigService
from strata.services.version_service import VersionService
from strata.services.workspace_service import WorkspaceService
from strata.utils.diagnostics import Diagnostics
from strata.utils.layout import (
    DEFAULT_IGNORED_DIRS,
    MANIFEST_FILENAME,
    YAML_SUFFIXES,
    manifest_path,
    path_remote_location,
)
from strata.utils.param_tokens import holds_param_tokens

#: Every `apiVersion` value that marks a document as ours. Anything else (or
#: nothing) means "not a strata document" and is skipped without complaint.
_STRATA_API_PREFIXES = ("strata.",)

#: Which service validates which kind. The service already knows its model
#: and owns Phase 1/Phase 2, so the controller does not duplicate either.
SERVICE_BY_KIND: dict[PlatformKind, type[BaseService[Any]]] = {
    PlatformKind.SOLUTION: SolutionService,
    PlatformKind.CONFIGURATION: ConfigurationService,
    PlatformKind.PROVIDERCONFIG: ProviderConfigService,
    PlatformKind.TOPOLOGYCONFIG: TopologyConfigService,
    PlatformKind.ATTRIBUTECONFIG: AttributeConfigService,
    PlatformKind.PROVIDER: ProviderService,
    PlatformKind.RESOURCE: ResourceService,
    PlatformKind.DNS: DnsService,
    PlatformKind.NETWORK: NetworkService,
    PlatformKind.FIREWALL: FirewallService,
    PlatformKind.MODULE: ModuleService,
    PlatformKind.NAMESPACE: NamespaceService,
    PlatformKind.WORKSPACE: WorkspaceService,
    PlatformKind.INTEGRATION: IntegrationService,
    PlatformKind.TENANT: TenantService,
    PlatformKind.ENVIRONMENT: EnvironmentService,
    PlatformKind.DEPLOYMENT: DeploymentService,
    PlatformKind.VERSION: VersionService,
    PlatformKind.ARTIFACT: ArtifactService,
    PlatformKind.ASSET: AssetService,
    PlatformKind.PARAMETERSET: ParameterSetService,
}

#: The kinds whose spec may read parameter sets, and the service that loads such a document as a template: meta
#: validated, spec as written, validated per instance once the resolver substitutes `${param:}`
#: (docs/design/parameter-sets.md, "Templates are validated per instance").
TEMPLATE_SERVICE_BY_KIND: dict[PlatformKind, type[BaseService[Any]]] = {
    PlatformKind.DEPLOYMENT: DeploymentTemplateService,
    PlatformKind.ENVIRONMENT: EnvironmentTemplateService,
    PlatformKind.TENANT: TenantTemplateService,
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
    """A validated document plus where it was found.

    `written` is the document as written when `model` is what resolution made of it: an environment or tenant
    resolved through `extends` and `${param:}` (`document_chains.resolve_environments_and_tenants()`). Every check
    reads `model`; the chain walks read `as_written`.
    """

    ref: DocumentRef
    model: PlatformBaseModel
    source: Path
    written: PlatformBaseModel | None = None

    @property
    def as_written(self) -> PlatformBaseModel:
        """The document as its file holds it."""
        return self.written if self.written is not None else self.model


class DocumentIndex:
    """Documents keyed by identity, with the path each came from."""

    def __init__(self) -> None:
        self._entries: dict[DocumentRef, IndexEntry] = {}
        self._names: dict[PlatformKind, set[str]] = {}

    def add(self, entry: IndexEntry) -> None:
        """Add an entry. Callers must check `get()` first — duplicates raise."""
        if entry.ref in self._entries:
            existing = self._entries[entry.ref]
            raise ValueError(f"Duplicate {entry.ref}: defined in both '{existing.source}' and '{entry.source}'")
        self._entries[entry.ref] = entry
        self._names.setdefault(entry.ref.kind, set()).add(entry.ref.name)

    def replace(self, entry: IndexEntry) -> None:
        """Put a resolved document in place of the one indexed under the same identity. Used only by resolution.

        Raises:
            KeyError: Nothing is indexed under `entry.ref`.
        """
        if entry.ref not in self._entries:
            raise KeyError(f"cannot replace {entry.ref}: it is not in the index")
        self._entries[entry.ref] = entry

    def get(self, kind: PlatformKind, name: str, remote: str | None = None) -> IndexEntry | None:
        """Return the entry for this identity, or None."""
        return self._entries.get(DocumentRef(kind=kind, name=name, remote=remote))

    def all_of(self, kind: PlatformKind) -> list[IndexEntry]:
        """Every indexed document of one kind, sorted by name."""
        return sorted((e for e in self._entries.values() if e.ref.kind is kind), key=lambda e: e.ref.name)

    def all(self) -> list[IndexEntry]:
        """Every indexed document, in discovery order.

        Discovery order rather than sorted, so findings about one file stay
        together in the order a reader encountered them — the same reason
        `Diagnostics` does not sort by severity.
        """
        return list(self._entries.values())

    def names_of(self, kind: PlatformKind) -> AbstractSet[str]:
        """Every indexed name for one kind. A read-only view: callers look names up per reference, so this must not scan."""
        return self._names.get(kind, frozenset())

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
        if manifest_path(candidate).is_file():
            return candidate
    return None


# libyaml's safe loader (same constructors, no arbitrary tags) parses about 5x faster than the pure-Python one.
_FAST_LOADER: Any = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _parse_documents(text: str) -> list[Any]:
    """Every YAML document in `text`, parsed safely."""
    try:
        return list(yaml.load_all(text, Loader=_FAST_LOADER))
    except yaml.YAMLError:
        # libyaml's messages drop the offending line; the pure-Python loader's keep it.
        return list(yaml.load_all(text, Loader=yaml.SafeLoader))


def _nested(first: Path, second: Path) -> bool:
    """Whether one folder is the other or inside it."""
    return first.is_relative_to(second) or second.is_relative_to(first)


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
        self.diagnostics = Diagnostics()
        #: `spec.discovery.remotes` that were scanned, by name: each checkout's resolved location.
        self.document_remotes: dict[str, Path] = {}

    @property
    def exclude_patterns(self) -> tuple[str, ...]:
        """User-declared exclude globs from the manifest, if any. Each root applies them relative to itself."""
        if self.solution is None or self.solution.spec.discovery is None:
            return ()
        return tuple(self.solution.spec.discovery.exclude or ())

    def _is_excluded(self, path: Path, root: Path) -> bool:
        """True when `path` matches a user-declared exclude pattern.

        Matching is fnmatch-style against the POSIX path relative to `root`: the solution root, or a document
        remote's checkout. Note fnmatch's `*` also spans `/`, so both `templates` (the directory itself) and
        `templates/**` (its contents) behave as expected.
        """
        patterns = self.exclude_patterns
        if not patterns:
            return False
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:  # outside the root — should not happen, but be safe
            return False
        return any(fnmatch(relative, pattern) for pattern in patterns)

    @classmethod
    def from_directory(cls, start: Path) -> "SolutionController | None":
        """Build a controller for the solution containing `start`, if any."""
        root = find_solution_root(start)
        return cls(root) if root is not None else None

    def load(self) -> Diagnostics:
        """Load the manifest, then discover and index every document.

        Findings accumulate — a bad document does not stop the scan, so one run
        reports everything wrong. `.ok` is False when any error was recorded.
        """
        self.diagnostics = Diagnostics()
        self.index = DocumentIndex()
        self.document_remotes = {}

        self._load_manifest()
        self.document_remotes = self._document_remotes()
        skip = frozenset(self.document_remotes.values())
        for root in (self.root, *self.document_remotes.values()):
            for path in self._walk(root, skip):
                self._load_file(path)

        return self.diagnostics

    def _document_remotes(self) -> dict[str, Path]:
        """The checkouts `spec.discovery.remotes` names, each one checked (docs/work/remote-commit-provenance.md, C1).

        One that cannot be scanned is an error naming the remote and the folder: missing (D9: every listed remote
        must be checked out), holding its own `strata.yaml` (one solution, one manifest), or overlapping the solution
        root or another listed checkout (its documents would load twice). A checkout inside the solution root is
        fine: the root's own walk skips it.
        """
        if self.solution is None or self.solution.spec.discovery is None:
            return {}
        remotes = {remote.name: remote for remote in (self.solution.spec.remotes or [])}
        source = str(manifest_path(self.root))
        found: dict[str, Path] = {}
        for position, name in enumerate(self.solution.spec.discovery.remotes or []):
            remote = remotes[name]  # SolutionSpecModel guarantees a declared git remote with a path
            assert remote.path is not None
            location = path_remote_location(self.root, remote.path)
            finding = self._document_remote_problem(name, remote.url, location, found)
            if finding is not None:
                code, problem, hint = finding
                self.diagnostics.error(
                    f"Document remote '{name}' {problem}.",
                    source=source,
                    location=f"spec.discovery.remotes.{position}",
                    code=code,
                    hint=hint,
                )
                continue
            found[name] = location
        return found

    def _document_remote_problem(
        self, name: str, url: str, location: Path, found: dict[str, Path]
    ) -> tuple[str, str, str] | None:
        """`(code, problem, hint)` when the checkout at `location` cannot be scanned, else `None`."""
        overlap_hint = (
            "Give each document remote a checkout of its own that holds neither the solution nor another one."
        )
        if self.root.is_relative_to(location):
            return (
                "document_remote_overlaps",
                f"at '{location}' holds the solution root, so its documents would load twice",
                overlap_hint,
            )
        others = [other for other, path in found.items() if _nested(location, path)]
        if others:
            return (
                "document_remote_overlaps",
                f"at '{location}' overlaps document remote '{others[0]}', so documents would load twice",
                overlap_hint,
            )
        if not location.is_dir():
            return (
                "document_remote_missing",
                f"is not checked out: '{location}' does not exist",
                f"Check out {url} at '{location}' (the remote's 'path'), or remove '{name}' from spec.discovery.remotes.",
            )
        if manifest_path(location).is_file():
            return (
                "document_remote_has_manifest",
                f"holds its own {MANIFEST_FILENAME} at '{location}': it is a solution, not a document remote",
                f"Remove '{location / MANIFEST_FILENAME}', or remove '{name}' from spec.discovery.remotes.",
            )
        return None

    def _load_manifest(self) -> None:
        """Validate `strata.yaml` itself. Its own kind is not indexed."""
        manifest = manifest_path(self.root)
        if not manifest.is_file():
            self.diagnostics.error(
                f"No {MANIFEST_FILENAME} found at solution root '{self.root}'",
                source=str(self.root),
                code="manifest_missing",
            )
            return

        service = SolutionService(path=str(manifest))
        result = service.validate()
        if not result.ok:
            self.diagnostics.extend(result, source=str(manifest))
            return
        self.solution = service.model

    def _walk(self, root: Path, skip: AbstractSet[Path] = frozenset()) -> Iterator[Path]:
        """Yield every candidate YAML file under `root`: the solution root or a document remote's checkout.

        Skips built-in ignored directories and anything matching a
        user-declared `spec.discovery.exclude` pattern (relative to `root`),
        does not descend into a directory holding its own `strata.yaml` — that
        is a different solution — and does not descend into `skip`: document
        remote checkouts, which are walked as roots of their own.
        """
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                children = sorted(current.iterdir())
            except OSError as exc:  # unreadable directory — report, keep going
                self.diagnostics.error(
                    f"cannot read directory: {exc}", source=str(current), code="unreadable_directory"
                )
                continue

            for child in children:
                if child.is_dir():
                    if child.name in DEFAULT_IGNORED_DIRS:
                        continue
                    if manifest_path(child).is_file():
                        continue  # nested solution — boundary
                    if self._is_excluded(child, root):
                        continue
                    if skip and child.resolve() in skip:
                        continue  # a document remote's checkout, walked as its own root
                    stack.append(child)
                elif child.suffix in YAML_SUFFIXES and child.name != MANIFEST_FILENAME:
                    if self._is_excluded(child, root):
                        continue
                    yield child

    def _load_file(self, path: Path) -> None:
        """Parse one file and index every strata document inside it."""
        try:
            documents = _parse_documents(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            self.diagnostics.error(f"cannot parse YAML: {exc}", source=str(path), code="unparseable")
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
            self.diagnostics.error(
                f"unknown kind '{kind_value}'. Known kinds: {known}",
                source=where,
                location="kind",
                code="unknown_kind",
            )
            return

        if kind is PlatformKind.SOLUTION:
            self.diagnostics.error(
                "a nested 'solution' document is not allowed — the manifest at the root is the only one.",
                source=where,
                location="kind",
                code="nested_solution",
            )
            return

        service_class = SERVICE_BY_KIND[kind]
        if kind in TEMPLATE_SERVICE_BY_KIND and holds_param_tokens(raw.get("spec")):
            service_class = TEMPLATE_SERVICE_BY_KIND[kind]
        service = service_class(data=raw)
        result = service.validate()
        if not result.ok or service.model is None:
            self.diagnostics.extend(result, source=where)
            return

        name = str(service.model.meta.name)
        entry = IndexEntry(ref=DocumentRef(kind=kind, name=name), model=service.model, source=path)
        try:
            self.index.add(entry)
        except ValueError as exc:
            self.diagnostics.error(str(exc), source=where, code="duplicate_document")
