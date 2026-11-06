#!/usr/bin/env python3
"""Collect Terraform provider and module components from build-output
`*.tf` files (docs/design/sbom-generation.md Phase 1) — v2 equivalent of
v1's `TerraformProviderCollector` + `TerraformModuleCollector`, merged into
one collector sharing a single HCL parse pass (the design doc's own
"Bundle the Terraform collectors" decision — same release, same file
walk, only new dependency either one would need is `python-hcl2`).
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hcl2

from strata.integrations.resolved_context import ResolvedWorkspaceGraph
from strata.integrations.sbom_collectors.base import CollectorResult, SbomCollector
from strata.models.module_model import ModuleModel
from strata.models.sbom_model import SbomComponentModel
from strata.utils.diagnostics import Diagnostic, Severity
from strata.utils.sbom_utils import terraform_module_to_purl, terraform_provider_address, terraform_provider_to_purl

#: Terraform's dependency lock file, one per root module, beside its `*.tf` files.
_LOCK_FILENAME = ".terraform.lock.hcl"
_CONSTRAINT_PROPERTY = "strata:version-constraint"
#: Root-level blocks that make `terraform init` resolve providers, so the root needs a lock file.
_PROVIDER_USING_BLOCKS = ("provider", "resource", "data", "module")


#: What `terraform init` writes: downloaded modules and providers, not the sources.
_INIT_DIR = ".terraform"


def _terraform_files(root: Path, *, separate_roots: bool = False) -> list[Path]:
    """Every `*.tf` file under `root`, sorted, never inside `.terraform`.

    With `separate_roots`, a folder below `root` that has its own lock file is another Terraform root, so it
    and everything under it is left out.
    """
    found: list[Path] = []
    for folder, dirs, files in os.walk(root):
        here = Path(folder)
        dirs[:] = [d for d in dirs if d != _INIT_DIR and not (separate_roots and (here / d / _LOCK_FILENAME).is_file())]
        found.extend(here / name for name in files if name.endswith(".tf"))
    return sorted(found)


@dataclass(frozen=True)
class ProviderLocks:
    """What one Terraform root declares and what its `.terraform.lock.hcl` locks."""

    lock_file: Path | None
    declared: frozenset[str]
    locked: frozenset[str]
    needs_providers: bool

    @property
    def unlocked(self) -> list[str]:
        """Declared provider addresses the lock file does not lock (all of them without one)."""
        return sorted(self.declared - self.locked)


def read_provider_locks(root: Path) -> ProviderLocks:
    """The providers a root module's `*.tf` tree declares, and the ones its own lock file locks.

    A root needs providers when any file declares `required_providers`, or a root-level file has a
    `provider`, `resource`, `data` or `module` block. A nested folder with its own lock file is another
    root and is not read. An unparseable file is skipped; `build run`'s SBOM already reports it.
    """
    declared: set[str] = set()
    needs = False
    for tf_file in _terraform_files(root, separate_roots=True):
        data = _load_hcl(tf_file)
        if data is None:
            continue
        providers: dict[str, dict[str, str]] = {}
        TerraformCollector._extract_required_providers(data, providers)
        declared.update(terraform_provider_address(cfg.get("source", name)) for name, cfg in providers.items())
        if tf_file.parent == root and any(data.get(block) for block in _PROVIDER_USING_BLOCKS):
            needs = True
    lock_file = root / _LOCK_FILENAME
    present = lock_file.is_file()
    return ProviderLocks(
        lock_file=lock_file if present else None,
        declared=frozenset(declared),
        locked=frozenset(_read_lock(lock_file, []) if present else {}),
        needs_providers=needs or bool(declared),
    )


def _load_hcl(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = hcl2.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _topmost(folder: Path, candidates: list[Path]) -> Path:
    """The highest of `candidates` that is `folder` or contains it, so an unlocked root reports once."""
    return min((c for c in candidates if c == folder or c in folder.parents), key=lambda c: len(c.parts))


def _unlocked_message(folder: str, has_lock: bool, names: list[str]) -> str:
    if has_lock:
        unversioned = "it is" if len(names) == 1 else "they are"
        return (
            f"{folder}: {_LOCK_FILENAME} does not lock {', '.join(names)}; {unversioned} listed in the SBOM "
            "without a version."
        )
    if not names:
        return f"{folder}: no {_LOCK_FILENAME}; the providers it uses are not listed in the SBOM."
    providers = f"provider {names[0]} is" if len(names) == 1 else f"providers {', '.join(names)} are"
    return f"{folder}: no {_LOCK_FILENAME}; {providers} listed in the SBOM without a version."


def _nearest_lock(folder: Path, top: Path) -> Path | None:
    """The `.terraform.lock.hcl` in `folder` or the closest parent, not above `top`: Terraform's root lock
    file covers the providers its child modules declare."""
    for candidate in (folder, *folder.parents):
        lock_file = candidate / _LOCK_FILENAME
        if lock_file.is_file():
            return lock_file
        if candidate == top:
            break
    return None


def _read_lock(lock_file: Path, diagnostics: list[Diagnostic]) -> dict[str, tuple[str | None, str | None]]:
    """`address -> (locked version, constraints)` from one lock file; empty without one."""
    if not lock_file.is_file():
        return {}
    try:
        with lock_file.open("r", encoding="utf-8") as fh:
            data = hcl2.load(fh)
    except Exception as exc:
        diagnostics.append(
            Diagnostic(
                severity=Severity.WARNING,
                message=f"failed to parse {lock_file}: {exc}",
                source="sbom:terraform",
            )
        )
        return {}
    locked: dict[str, tuple[str | None, str | None]] = {}
    for block in data.get("provider") or []:
        if not isinstance(block, dict):
            continue
        for label, cfg in block.items():
            address = _strip_hcl_string(label)
            if label == "__is_block__" or not address or not isinstance(cfg, dict):
                continue
            locked[terraform_provider_address(address)] = (
                _strip_hcl_string(cfg.get("version")),
                _strip_hcl_string(cfg.get("constraints")),
            )
    return locked


def _strip_hcl_string(value: Any) -> str | None:
    """Strip surrounding double-quotes that `python-hcl2` adds to string values."""
    if not isinstance(value, str):
        return str(value) if value is not None else None
    return value.strip('"')


class TerraformCollector(SbomCollector):
    """Collects Terraform provider (`required_providers {}`) and module
    (`module "x" {}`) components from every `*.tf` file under `build_path`.

    Providers are grouped by the lock file that covers them: the nearest
    `.terraform.lock.hcl` in a file's folder or its parents within
    `build_path`, so a root and its child modules share one. A provider
    takes its version from that lock file, which also adds providers used
    without a `required_providers` entry. Modules
    are deduplicated by source string, first occurrence wins. A parse error
    on an individual file produces a `Severity.WARNING` diagnostic rather
    than aborting the collector.
    """

    def collect(self, graph: ResolvedWorkspaceGraph, modules: list[ModuleModel], build_path: Path) -> CollectorResult:
        components: list[SbomComponentModel] = []
        diagnostics: list[Diagnostic] = []

        if not build_path.exists():
            return CollectorResult()

        # Keyed by the covering lock file, or by the folder itself when none covers it.
        providers_by_lock: dict[Path, tuple[Path | None, dict[str, dict[str, str]]]] = {}
        using_unlocked: set[Path] = set()
        tf_modules: dict[str, str | None] = {}

        for tf_file in _terraform_files(build_path):
            try:
                with tf_file.open("r", encoding="utf-8") as fh:
                    data = hcl2.load(fh)
            except Exception as exc:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.WARNING,
                        message=f"failed to parse {tf_file.name}: {exc}",
                        source="sbom:terraform",
                    )
                )
                continue

            lock_file = _nearest_lock(tf_file.parent, build_path)
            _, declared = providers_by_lock.setdefault(lock_file or tf_file.parent, (lock_file, {}))
            self._extract_required_providers(data, declared)
            self._extract_modules(data, tf_modules)
            if lock_file is None and any(data.get(block) for block in _PROVIDER_USING_BLOCKS):
                using_unlocked.add(tf_file.parent)

        seen_purls: set[str] = set()
        unlocked_folders = [key for key, (lock_file, _) in providers_by_lock.items() if lock_file is None]
        unlocked: dict[Path, tuple[bool, set[str]]] = {}
        for key, (lock_file, declared) in providers_by_lock.items():
            locked = _read_lock(lock_file, diagnostics) if lock_file is not None else {}
            for component in self._provider_components(declared, locked):
                if component.purl not in seen_purls:
                    seen_purls.add(component.purl)
                    components.append(component)
            names = {
                name
                for name, cfg in declared.items()
                if terraform_provider_address(cfg.get("source", name)) not in locked
            }
            if names:
                folder = lock_file.parent if lock_file is not None else _topmost(key, unlocked_folders)
                unlocked.setdefault(folder, (lock_file is not None, set()))[1].update(names)
        # A provider used but not declared is not in the SBOM at all without a lock file.
        for key in using_unlocked:
            unlocked.setdefault(_topmost(key, unlocked_folders), (False, set()))

        # With the policy set, its finding is the report; the notice would repeat it.
        if graph.workspace.spec.provisioner_lock_policy is not None:
            unlocked.clear()
        for folder, (has_lock, names) in unlocked.items():
            diagnostics.append(
                Diagnostic(
                    severity=Severity.INFO,
                    message=_unlocked_message(folder.relative_to(build_path).as_posix(), has_lock, sorted(names)),
                    source="sbom:terraform",
                    code="sbom_provider_unlocked",
                )
            )

        for source, version in tf_modules.items():
            purl = terraform_module_to_purl(source, version)
            if purl is None:
                continue
            base = source.split("?")[0].split("//")[0]
            if base.startswith("registry.terraform.io/"):
                base = base[len("registry.terraform.io/") :]
            parts = base.split("/")
            name = "/".join(parts[:2]) if len(parts) >= 2 else base
            components.append(
                SbomComponentModel(
                    component_type="library",
                    name=name,
                    version=version,
                    purl=purl,
                    properties={},
                    source_collector="terraform-module",
                )
            )

        return CollectorResult(components=components, diagnostics=diagnostics)

    @staticmethod
    def _provider_components(
        declared: dict[str, dict[str, str]], locked: dict[str, tuple[str | None, str | None]]
    ) -> list[SbomComponentModel]:
        """One root module's providers: the declared ones, then any only its lock file names.

        The version is the locked one, or none: a `required_providers` constraint is not a version a
        scanner can match, so it is kept as the `strata:version-constraint` property instead.
        """
        entries: dict[str, tuple[str, str | None, str | None]] = {}
        for local_name, cfg in declared.items():
            address = terraform_provider_address(cfg.get("source", local_name))
            version, lock_constraint = locked.get(address, (None, None))
            entries.setdefault(address, (local_name, version, cfg.get("version") or lock_constraint))
        for address, (version, constraint) in locked.items():
            entries.setdefault(address, (address.rsplit("/", 1)[-1], version, constraint))
        return [
            SbomComponentModel(
                component_type="library",
                name=name,
                version=version,
                purl=terraform_provider_to_purl(address, version),
                properties={_CONSTRAINT_PROPERTY: constraint} if constraint else {},
                source_collector="terraform",
            )
            for address, (name, version, constraint) in entries.items()
        ]

    @staticmethod
    def _extract_required_providers(data: dict[str, Any], providers: dict[str, dict[str, str]]) -> None:
        """Extract `required_providers` entries into `providers` (deduplicated, first-wins)."""
        for block in data.get("terraform") or []:
            if not isinstance(block, dict):
                continue
            for rp in block.get("required_providers") or []:
                if not isinstance(rp, dict):
                    continue
                for provider_name, cfg in rp.items():
                    if provider_name == "__is_block__" or not isinstance(cfg, dict):
                        continue
                    if provider_name in providers:
                        continue
                    source = _strip_hcl_string(cfg.get("source"))
                    version = _strip_hcl_string(cfg.get("version"))
                    entry: dict[str, str] = {}
                    if source:
                        entry["source"] = source
                    if version:
                        entry["version"] = version
                    providers[provider_name] = entry

    @staticmethod
    def _extract_modules(data: dict[str, Any], tf_modules: dict[str, str | None]) -> None:
        """Extract `module {}` block entries into `tf_modules` (deduplicated, first-wins).

        `python-hcl2` represents labeled blocks as:
            {"module": [{"label": [{"source": '"..."', "version": '"..."'}]}]}
        """
        for block in data.get("module") or []:
            if not isinstance(block, dict):
                continue
            for label, cfg_list in block.items():
                if label == "__is_block__":
                    continue
                cfgs = cfg_list if isinstance(cfg_list, list) else [cfg_list]
                for cfg in cfgs:
                    if not isinstance(cfg, dict):
                        continue
                    source = _strip_hcl_string(cfg.get("source"))
                    if not source or source in tf_modules:
                        continue
                    version = _strip_hcl_string(cfg.get("version"))
                    tf_modules[source] = version
