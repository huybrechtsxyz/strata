#!/usr/bin/env python3
"""Version pin checking — does a declared pin target anything real?

There is no build/deploy layer yet, so a pin cannot be "applied" to a
rendered artifact — nothing renders artifacts. What `strata validate` can
honestly do today is check *existence and applicability*: does the pinned
target exist, and if it does, is pinning it even meaningful (a chart pin
against a git-mode module, a remote pin against one CI already placed)?
Computing an "effective image/chart/ref" map for a future builder to consume
would be machinery with no consumer yet — the same restraint this session
applied to ring/promotion and NDJSON.

Checked once per **Version document**, not once per deployment that
references it — a pin is a fact about the version document, independent of
which deployment names it. Checking per-deployment would double-report a
version file shared by two deployments.

Two severities, both decided in ADR-0019:

- **Error** — a `remotes` pin naming a `fetch: external` remote. CI already
  placed that remote before strata ran; the pin cannot take effect, so
  resolution rejects it rather than silently ignoring it.
- **Warning** — everything else that does not resolve: "a stale pin for a
  deleted module is the likeliest real failure," and should be visible
  without failing the build over it.
"""

from typing import cast

from strata.controllers.solution_controller import DocumentIndex
from strata.models.common_models import PlatformKind
from strata.models.module_model import ModuleModel
from strata.models.solution_model import RemoteFetch, SolutionModel
from strata.models.version_model import VersionModel
from strata.utils.diagnostics import Diagnostics


def check_version_pins(index: DocumentIndex, solution: SolutionModel | None) -> Diagnostics:
    """Check every pin in every Version document against the solution's inventory.

    Args:
        index: The loaded `DocumentIndex`.
        solution: The manifest, for resolving remote names and their `fetch`
            mode. Remote pins are all reported as unresolved when absent.

    Returns:
        One warning per pin whose target does not exist or cannot be pinned
        this way; one error per pin naming a `fetch: external` remote.
    """
    diagnostics = Diagnostics()
    known_services = _known_service_names(index)
    remotes_by_name = {remote.name: remote for remote in (solution.spec.remotes or [])} if solution else {}

    for entry in index.all_of(PlatformKind.VERSION):
        version = cast(VersionModel, entry.model)
        for category, name, _pin in version.spec.pins.iter_pins():
            location = f"spec.pins.{category}.{name}"

            if category == "images":
                if name not in known_services:
                    diagnostics.warning(
                        f"image pin '{name}' matched no module service",
                        source=str(entry.source),
                        location=location,
                        code="stale_pin",
                    )

            elif category == "charts":
                module_entry = index.get(PlatformKind.MODULE, name)
                if module_entry is None:
                    diagnostics.warning(
                        f"chart pin '{name}' matched no module document",
                        source=str(entry.source),
                        location=location,
                        code="stale_pin",
                    )
                else:
                    module = cast(ModuleModel, module_entry.model)
                    if module.spec.source.chart_name is None:
                        diagnostics.warning(
                            f"chart pin '{name}' targets a module that is not chart-based "
                            "(no spec.source.chart_name) — the pin cannot apply",
                            source=str(entry.source),
                            location=location,
                            code="pin_not_applicable",
                        )

            elif category == "remotes":
                remote = remotes_by_name.get(name)
                if remote is None:
                    diagnostics.warning(
                        f"remote pin '{name}' matched no remote in the solution manifest",
                        source=str(entry.source),
                        location=location,
                        code="stale_pin",
                    )
                elif remote.fetch is RemoteFetch.EXTERNAL:
                    diagnostics.error(
                        f"remote pin '{name}' targets a fetch:external remote — CI places it before "
                        "strata runs, so this pin cannot take effect",
                        source=str(entry.source),
                        location=location,
                        code="pin_not_applicable",
                    )

    return diagnostics


def _known_service_names(index: DocumentIndex) -> set[str]:
    """Every `ModuleServiceModel.name` across every indexed Module document.

    Flat and global, matching the pin key's own scope (`VersionPinsModel`'s
    docstring: "Key = ModuleServiceModel.name", not module-qualified) — the
    same flat namespace v1's real production file used.
    """
    names: set[str] = set()
    for entry in index.all_of(PlatformKind.MODULE):
        module = cast(ModuleModel, entry.model)
        for service in module.spec.services or []:
            names.add(service.name)
    return names
