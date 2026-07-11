"""Click CLI wiring for the ``versions`` command group.

Subcommands
-----------
init    — Scaffold a starter version-manifest (kind: version) file for a ring.
export  — Print the resolved flat pin state from a version file.
apply   — Convert a version-manifest into a version-lock file.
refresh — Sync a manifest against discovered targets in the workspace.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
import yaml

from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
)
from strata.models.common_models import PlatformKind
from strata.utils.system import resolve_work_path

_API_VERSION = "strata.huybrechts.xyz/v1"


# ── scanner ───────────────────────────────────────────────────────────────────


def _scan_for_targets(scan_dir: Path) -> dict[str, dict[str, str]]:
    """Walk *scan_dir* and extract versionable targets from module/workspace YAML files.

    Returns a dict with keys ``images``, ``charts``, ``remotes``, each mapping
    target name → seed version string (may be empty when no version is declared
    in the source file).

    Sources scanned:
    - ``kind: module``         — ``spec.source.chart_name``/``chart_version`` and
                                 ``spec.services[].image``
    - ``kind: workspace``      — ``spec.provisioners[].source.repository`` (remote name)
                                 and ``spec.provisioners[].source.chart_name``/``chart_version``
    - ``kind: configuration``  — ``spec.remotes[].name`` with ``reference`` as seed
    - ``kind: environment``    — ``spec.overrides.remotes[].remote``/``reference``,
                                 ``spec.overrides.modules[].chart_version``,
                                 ``spec.overrides.modules[].services[].image``

    Files that cannot be parsed are silently skipped.
    """
    discovered: dict[str, dict[str, str]] = {"images": {}, "charts": {}, "remotes": {}}

    _KIND_EXTRACTORS = {
        PlatformKind.MODULE.value: _extract_module_targets,
        PlatformKind.WORKSPACE.value: _extract_workspace_targets,
        PlatformKind.CONFIGURATION.value: _extract_configuration_targets,
        PlatformKind.ENVIRONMENT.value: _extract_environment_targets,
    }

    for yaml_path in sorted(scan_dir.rglob("*.yaml")):
        try:
            with yaml_path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            if not isinstance(raw, dict):
                continue
            extractor = _KIND_EXTRACTORS.get(raw.get("kind"))
            if extractor:
                extractor(raw, discovered)
        except Exception:
            continue

    return discovered


def _extract_module_targets(raw: dict, discovered: dict[str, dict[str, str]]) -> None:
    """Extract helm chart and image targets from a raw module YAML dict."""
    spec = raw.get("spec") or {}
    meta = raw.get("meta") or {}
    module_name: str = meta.get("name") or ""
    source = spec.get("source") or {}

    chart_name: str | None = source.get("chart_name")
    if chart_name:
        # Key by module name so it matches the environment override key convention.
        key = module_name or chart_name
        seed = source.get("chart_version") or ""
        discovered["charts"].setdefault(key, seed)

    for svc in spec.get("services") or []:
        image: str | None = svc.get("image")
        svc_name: str = svc.get("name") or ""
        if image and svc_name:
            # Seed with the current image reference so the user has a starting point.
            discovered["images"].setdefault(svc_name, image)


def _extract_workspace_targets(raw: dict, discovered: dict[str, dict[str, str]]) -> None:
    """Extract remote and chart targets from a raw workspace YAML dict."""
    spec = raw.get("spec") or {}
    for prov in spec.get("provisioners") or []:
        source = prov.get("source") or {}
        # Git-based provisioner → remote name
        repo: str | None = source.get("repository")
        if repo:
            discovered["remotes"].setdefault(repo, "")
        # Chart-based provisioner (Helm/ArgoCD registry pull)
        chart_name: str | None = source.get("chart_name")
        if chart_name:
            key = prov.get("name") or chart_name
            seed = source.get("chart_version") or ""
            discovered["charts"].setdefault(key, seed)


def _extract_configuration_targets(raw: dict, discovered: dict[str, dict[str, str]]) -> None:
    """Extract remote targets from a raw configuration YAML dict.

    ``spec.remotes[]`` are the canonical named-remote definitions.  Their
    ``reference`` field is the default version/tag/branch and makes a useful
    seed for a new version manifest entry.
    """
    spec = raw.get("spec") or {}
    for remote in spec.get("remotes") or []:
        name: str | None = remote.get("name")
        reference: str = remote.get("reference") or ""
        if name:
            discovered["remotes"].setdefault(name, reference)


def _extract_environment_targets(raw: dict, discovered: dict[str, dict[str, str]]) -> None:
    """Extract version targets from a raw environment YAML dict.

    Covers three override sections:
    - ``spec.overrides.remotes[].remote`` + ``reference`` → remote targets
    - ``spec.overrides.modules[].chart_version``           → helm chart targets
    - ``spec.overrides.modules[].services[].image``        → image targets
    """
    spec = raw.get("spec") or {}
    overrides = spec.get("overrides") or {}

    # Remote version overrides
    for remote_override in overrides.get("remotes") or []:
        name: str | None = remote_override.get("remote")
        reference: str = remote_override.get("reference") or ""
        if name:
            discovered["remotes"].setdefault(name, reference)

    # Module overrides (chart version + service images)
    for mod_override in overrides.get("modules") or []:
        mod_name: str | None = mod_override.get("module")
        chart_version: str | None = mod_override.get("chart_version")
        if mod_name and chart_version:
            discovered["charts"].setdefault(mod_name, chart_version)

        for svc_override in mod_override.get("services") or []:
            svc_name: str | None = svc_override.get("name")
            image: str | None = svc_override.get("image")
            if svc_name and image:
                discovered["images"].setdefault(svc_name, image)


@click.group(name="versions", help="Manage version manifests and version locks.")
def versions_group() -> None:
    """Versions command group."""


# ── init ──────────────────────────────────────────────────────────────────────


@versions_group.command(name="init", help="Scaffold a starter version-manifest file for a ring.")
@click.option("--ring", "-r", required=True, metavar="NAME", help="Ring name (e.g. dev, prd).")
@click.option(
    "--out",
    "-o",
    default=None,
    metavar="PATH",
    help="Output path. Defaults to versions/<ring>.yaml relative to work-path.",
)
@click.option("--force", is_flag=True, default=False, help="Overwrite if the file already exists.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_init(
    ring: str,
    out: Optional[str],
    force: bool,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Scaffold a starter version-manifest file for a ring."""
    work = resolve_work_path(work_path)

    if out:
        dest = Path(out)
        if not dest.is_absolute():
            dest = work / dest
    else:
        dest = work / "versions" / f"{ring}.yaml"

    if dest.exists() and not force:
        click.echo(f"❌  File already exists: {dest}\n   Use --force to overwrite.", err=True)
        raise click.exceptions.Exit(1)

    doc = {
        "apiVersion": _API_VERSION,
        "kind": PlatformKind.VERSION_MANIFEST.value,
        "meta": {
            "name": ring,
            "annotations": {"description": f"Version manifest for the {ring} ring"},
        },
        "spec": {
            "ring": ring,
            "pins": {
                "images": {},
                "charts": {},
                "remotes": {},
            },
        },
    }

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8") as fh:
        yaml.dump(doc, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    if output == "json":
        click.echo(json.dumps({"success": True, "file": str(dest), "ring": ring}, indent=2))
        return
    if output == "text":
        click.echo(str(dest))
        return
    if not quiet:
        click.echo(f"✅  Created {dest}")
        click.echo(f"    kind: {PlatformKind.VERSION_MANIFEST.value}")
        click.echo(f"    ring: {ring}")
        click.echo("    Next: populate spec.pins and run 'strata versions apply'")


# ── export ─────────────────────────────────────────────────────────────────────


@versions_group.command(
    name="export",
    help="Print the resolved flat pin state from a version-manifest or version-lock file.",
)
@click.option(
    "--file",
    "-f",
    required=True,
    metavar="PATH",
    help="Path to a version-manifest (kind: version) or version-lock (kind: version-lock) file.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_export(
    file: str,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Print the resolved flat pin state."""
    from strata.services.version_service import VersionService

    work = resolve_work_path(work_path)
    file_path = Path(file)
    if not file_path.is_absolute():
        file_path = work / file_path

    if not file_path.exists():
        click.echo(f"❌  File not found: {file_path}", err=True)
        raise click.exceptions.Exit(1)

    model = VersionService.load(str(file_path))
    pins = VersionService.resolve_pins([model])

    # Build output dict: type_value → {name: version} (only populated types)
    flat: dict[str, dict[str, str]] = {
        pt.value: entries for pt, entries in pins.items() if entries
    }

    if output == "json":
        click.echo(json.dumps({"success": True, "pins": flat}, indent=2))
        return

    if output == "text":
        for type_key, entries in sorted(flat.items()):
            for name, version in sorted(entries.items()):
                click.echo(f"{type_key}/{name}={version}")
        return

    if not quiet:
        if not flat:
            click.echo("  (no resolved pins)")
            return
        click.echo("")
        for type_key, entries in sorted(flat.items()):
            click.echo(f"  {type_key.upper()}")
            for name, version in sorted(entries.items()):
                click.echo(f"    {name:<32}  {version}")
        click.echo("")


# ── apply ──────────────────────────────────────────────────────────────────────


@versions_group.command(
    name="apply",
    help="Convert a version-manifest (kind: version) into a version-lock (kind: version-lock) file.",
)
@click.option(
    "--file",
    "-f",
    required=True,
    metavar="PATH",
    help="Path to the version-manifest (kind: version) YAML file.",
)
@click.option(
    "--out",
    "-o",
    default=None,
    metavar="PATH",
    help="Output path for the lock file. Defaults to <stem>.lock.yaml alongside input.",
)
@click.option("--force", is_flag=True, default=False, help="Overwrite existing lock file.")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_apply(
    file: str,
    out: Optional[str],
    force: bool,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Convert a version-manifest into a version-lock file."""
    from strata.models.version_lock_model import VersionPinTargetType
    from strata.models.version_manifest_model import VersionManifestModel
    from strata.services.version_service import VersionService

    work = resolve_work_path(work_path)
    file_path = Path(file)
    if not file_path.is_absolute():
        file_path = work / file_path

    if not file_path.exists():
        click.echo(f"❌  File not found: {file_path}", err=True)
        raise click.exceptions.Exit(1)

    model = VersionService.load(str(file_path))
    if not isinstance(model, VersionManifestModel):
        click.echo(
            f"❌  Expected kind: {PlatformKind.VERSION_MANIFEST.value}, "
            f"got '{model.kind}'. Provide a version-manifest file.",
            err=True,
        )
        raise click.exceptions.Exit(1)

    # Resolve output path
    if out:
        lock_path = Path(out)
        if not lock_path.is_absolute():
            lock_path = work / lock_path
    else:
        lock_path = file_path.parent / f"{file_path.stem}.lock.yaml"

    if lock_path.exists() and not force:
        click.echo(f"❌  Lock file already exists: {lock_path}\n   Use --force to overwrite.", err=True)
        raise click.exceptions.Exit(1)

    # Convert manifest pins (flat dicts) → lock pins (list of target+version objects)
    pins_raw = model.spec.pins
    lock_pins: list[dict] = []

    if pins_raw.images:
        for name, version in pins_raw.images.items():
            lock_pins.append(
                {"target": {"type": VersionPinTargetType.IMAGE.value, "name": name}, "version": version}
            )
    if pins_raw.charts:
        for name, version in pins_raw.charts.items():
            lock_pins.append(
                {"target": {"type": VersionPinTargetType.HELM_CHART.value, "name": name}, "version": version}
            )
    if pins_raw.remotes:
        for name, version in pins_raw.remotes.items():
            lock_pins.append(
                {"target": {"type": VersionPinTargetType.REMOTE.value, "name": name}, "version": version}
            )

    now_ts = datetime.now(timezone.utc).isoformat()
    lock_doc = {
        "apiVersion": _API_VERSION,
        "kind": PlatformKind.VERSION_LOCK.value,
        "meta": {
            "name": model.meta.name,
            "annotations": {
                "generated_at": now_ts,
                "generated_by": "strata versions apply",
            },
        },
        "spec": {
            "ring": model.spec.ring,
            "pins": lock_pins,
        },
    }

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as fh:
        yaml.dump(lock_doc, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    if output == "json":
        click.echo(
            json.dumps(
                {
                    "success": True,
                    "lock_file": str(lock_path),
                    "ring": model.spec.ring,
                    "pins_count": len(lock_pins),
                },
                indent=2,
            )
        )
        return
    if output == "text":
        click.echo(str(lock_path))
        return
    if not quiet:
        click.echo(f"✅  Lock file written: {lock_path}")
        click.echo(f"    kind:  {PlatformKind.VERSION_LOCK.value}")
        click.echo(f"    ring:  {model.spec.ring}")
        click.echo(f"    pins:  {len(lock_pins)}")


# ── refresh ────────────────────────────────────────────────────────────────────


@versions_group.command(
    name="refresh",
    help=(
        "Sync a version-manifest against versionable targets discovered in the workspace.\n\n"
        "Scans for kind:module and kind:workspace YAML files and compares them to the "
        "manifest's current pins.  New targets are added (with their current version as a "
        "seed value).  Targets no longer found are reported; pass --remove-stale to also "
        "delete them from the manifest."
    ),
)
@click.option(
    "--file",
    "-f",
    required=True,
    metavar="PATH",
    help="Path to the version-manifest (kind: version) file to update.",
)
@click.option(
    "--scan",
    "-d",
    default=None,
    metavar="PATH",
    help="Directory to scan for module/workspace YAML files. Defaults to work-path.",
)
@click.option(
    "--remove-stale",
    "remove_stale",
    is_flag=True,
    default=False,
    help="Remove manifest entries whose targets were not found during the scan.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Show what would change without writing the file.",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def versions_refresh(
    file: str,
    scan: Optional[str],
    remove_stale: bool,
    dry_run: bool,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Sync a version-manifest against discovered workspace targets."""
    work = resolve_work_path(work_path)

    # Resolve manifest path
    file_path = Path(file)
    if not file_path.is_absolute():
        file_path = work / file_path
    if not file_path.exists():
        click.echo(f"❌  File not found: {file_path}", err=True)
        raise click.exceptions.Exit(1)

    # Validate it's a manifest
    with file_path.open("r", encoding="utf-8") as fh:
        raw_doc = yaml.safe_load(fh)
    if not isinstance(raw_doc, dict) or raw_doc.get("kind") != PlatformKind.VERSION_MANIFEST.value:
        click.echo(
            f"❌  Expected kind: {PlatformKind.VERSION_MANIFEST.value} — got '{raw_doc.get('kind')}'.",
            err=True,
        )
        raise click.exceptions.Exit(1)

    # Scan directory
    scan_dir = Path(scan) if scan else work
    if not scan_dir.is_absolute():
        scan_dir = work / scan_dir
    discovered = _scan_for_targets(scan_dir)

    # Current pins (raw dict — preserves existing versions)
    spec = raw_doc.setdefault("spec", {})
    current_pins: dict = spec.setdefault("pins", {})

    # Per-type diffing
    added: dict[str, list[str]] = {"images": [], "charts": [], "remotes": []}
    stale: dict[str, list[str]] = {"images": [], "charts": [], "remotes": []}

    for type_key in ("images", "charts", "remotes"):
        current = current_pins.setdefault(type_key, {}) or {}
        found = discovered.get(type_key, {})

        for name, seed in found.items():
            if name not in current:
                current[name] = seed
                added[type_key].append(name)

        for name in list(current.keys()):
            if name not in found:
                stale[type_key].append(name)
                if remove_stale:
                    del current[name]

        current_pins[type_key] = current

    total_added = sum(len(v) for v in added.values())
    total_stale = sum(len(v) for v in stale.values())

    if output == "json":
        payload = {
            "success": True,
            "dry_run": dry_run,
            "added": added,
            "stale": stale,
            "stale_removed": remove_stale,
            "file": str(file_path),
        }
        if not dry_run:
            _write_manifest(file_path, raw_doc)
        click.echo(json.dumps(payload, indent=2))
        return

    if output == "text":
        for type_key, names in added.items():
            for name in names:
                click.echo(f"added:{type_key}/{name}")
        for type_key, names in stale.items():
            for name in names:
                verb = "removed" if remove_stale else "stale"
                click.echo(f"{verb}:{type_key}/{name}")
        if not dry_run:
            _write_manifest(file_path, raw_doc)
        return

    # Console mode
    if not quiet:
        if total_added == 0 and total_stale == 0:
            click.echo("✅  Manifest is already up to date — no changes needed.")
        else:
            if total_added:
                click.echo(f"\n  ➕  {total_added} new target(s) added:")
                for type_key, names in added.items():
                    for name in sorted(names):
                        seed = current_pins.get(type_key, {}).get(name, "")
                        seed_note = f"  (seed: {seed})" if seed else "  (no version — fill in)"
                        click.echo(f"       {type_key}/{name}{seed_note}")
            if total_stale:
                verb = "removed" if remove_stale else "found (use --remove-stale to delete)"
                click.echo(f"\n  ⚠   {total_stale} stale target(s) {verb}:")
                for type_key, names in stale.items():
                    for name in sorted(names):
                        click.echo(f"       {type_key}/{name}")
            if dry_run:
                click.echo("\n  (dry-run — manifest not written)")
            else:
                click.echo(f"\n✅  Updated: {file_path}")

    if not dry_run:
        _write_manifest(file_path, raw_doc)


def _write_manifest(path: Path, doc: dict) -> None:
    """Write a YAML document dict back to *path*."""
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(doc, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)
