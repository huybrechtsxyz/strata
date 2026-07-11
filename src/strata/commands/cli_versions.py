"""Click CLI wiring for the ``versions`` command group.

Subcommands
-----------
init   — Scaffold a starter version-manifest (kind: version) file for a ring.
export — Print the resolved flat pin state from a version file.
apply  — Convert a version-manifest into a version-lock file.
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
