#!/usr/bin/env python3
"""Version pin checking — does a declared pin target anything real?

Applying a pin is not this module's job: `build run` overlays images, charts, remotes
and assets, and `store: artifact` resolves `artifacts` pins at deploy time. What
`strata validate` checks here is *existence and applicability*: does the pinned
target exist, and if it does, is pinning it even meaningful (a chart pin
against a git-mode module, a remote pin against one CI already placed)?

Checked once per **Version document**, not once per deployment that
references it — a pin is a fact about the version document, independent of
which deployment names it. Checking per-deployment would double-report a
version file shared by two deployments.

Two severities, both decided in ADR-0019:

- **Error** — a `remotes` pin naming a `fetch: external` remote. CI already
  placed that remote before strata ran; the pin cannot take effect, so
  resolution rejects it rather than silently ignoring it.
- **Error** — a pin whose value has the wrong shape: an `artifacts` pin that is
  not a tag, a digest or both (`artifact_tag_invalid`), an `images` pin that is
  not a whole image reference with a tag or digest (`image_pin_invalid`).
- **Warning** — everything else that does not resolve: "a stale pin for a
  deleted module is the likeliest real failure," and should be visible
  without failing the build over it.
"""

from typing import cast

from strata.controllers.solution_controller import DocumentIndex
from strata.logging.config import get_logger
from strata.models.asset_model import AssetModel
from strata.models.common_models import PlatformKind
from strata.models.module_model import ModuleModel, ModuleServiceModel
from strata.models.solution_model import RemoteFetch, SolutionModel
from strata.models.version_model import VersionModel
from strata.utils.diagnostics import Diagnostics
from strata.utils.sbom_utils import canonical_image_name, image_pin_problem, image_tag_problem, parse_image_ref

log = get_logger(__name__)


def log_pin_applied(category: str, name: str, declared: str | None, pinned: str, version_name: str) -> None:
    """Log one version pin actually taking effect (ADR-0019 decision 7: "every
    application must be logged").

    Called from each overlay point (`build_controller.py`'s `remotes`
    override, `workload_controller.py`'s `images`/`charts` override,
    `value_controller.py`'s `artifacts` lookup) whenever a pin's value wins
    over whatever the target itself declared — never for a pin that matched
    nothing (that case is `check_version_pins()`'s own "stale pin" warning,
    a schema-validation concern, not a build-time event).

    Args:
        category: One of `PIN_CATEGORIES` ('images', 'charts', 'remotes', 'artifacts').
        name: The pinned target's own name (service/module/remote/artifact name).
        declared: The value the target itself declared before the pin overrode
            it, or `None` when the target declared nothing at all (e.g. a
            module service with no `image` set of its own).
        pinned: The pin's own value — what the target now resolves to.
        version_name: The `Version` document's own `meta.name` the pin came from.
    """
    log.info(
        "version pin applied",
        category=category,
        name=name,
        declared=declared,
        pinned=pinned,
        version=version_name,
    )


def check_version_pins(index: DocumentIndex, solution: SolutionModel | None) -> Diagnostics:
    """Check every pin in every Version document against the solution's inventory.

    Args:
        index: The loaded `DocumentIndex`.
        solution: The manifest, for resolving remote names and their `fetch`
            mode. Remote pins are all reported as unresolved when absent.

    Returns:
        One warning per pin whose target does not exist or cannot be pinned
        this way, or an `images` pin naming another image than its service
        runs; one error per pin naming a `fetch: external` remote or holding
        a value of the wrong shape.
    """
    diagnostics = Diagnostics()
    services_by_name = _services_by_name(index)
    remotes_by_name = {remote.name: remote for remote in (solution.spec.remotes or [])} if solution else {}

    for entry in index.all_of(PlatformKind.VERSION):
        version = cast(VersionModel, entry.model)
        for category, name, pin in version.spec.pins.iter_pins():
            location = f"spec.pins.{category}.{name}"

            if category == "images":
                _check_image_pin(
                    name,
                    pin.version,
                    services_by_name.get(name, []),
                    source=str(entry.source),
                    location=location,
                    diagnostics=diagnostics,
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

            elif category == "artifacts":
                problem = image_tag_problem(pin.version) if pin.version else None
                if problem is not None:
                    diagnostics.error(
                        f"artifact pin '{name}': {problem}",
                        source=str(entry.source),
                        location=f"{location}.version",
                        code="artifact_tag_invalid",
                    )
                if index.get(PlatformKind.ARTIFACT, name) is None:
                    diagnostics.warning(
                        f"artifact pin '{name}' matched no artifact document",
                        source=str(entry.source),
                        location=location,
                        code="stale_pin",
                    )

            elif category == "assets":
                _check_asset_pin(index, name, source=str(entry.source), location=location, diagnostics=diagnostics)

    return diagnostics


def _check_asset_pin(index: DocumentIndex, name: str, *, source: str, location: str, diagnostics: Diagnostics) -> None:
    """One `assets` pin: the asset must exist, declare its own `location`, and be `fetch: strata`."""
    asset_entry = index.get(PlatformKind.ASSET, name)
    if asset_entry is None:
        diagnostics.warning(
            f"asset pin '{name}' matched no asset document", source=source, location=location, code="stale_pin"
        )
        return
    asset_location = cast(AssetModel, asset_entry.model).spec.location
    if asset_location is None:
        diagnostics.warning(
            f"asset pin '{name}' targets an asset with no spec.location — the pin cannot apply "
            "(an asset using a manifest remote is pinned under pins.remotes)",
            source=source,
            location=location,
            code="pin_not_applicable",
        )
    elif asset_location.fetch is RemoteFetch.EXTERNAL:
        diagnostics.error(
            f"asset pin '{name}' targets a fetch:external location — CI places it before strata runs, "
            "so this pin cannot take effect",
            source=source,
            location=location,
            code="pin_not_applicable",
        )


def _check_image_pin(
    name: str,
    value: str,
    services: list[tuple[str, ModuleServiceModel]],
    *,
    source: str,
    location: str,
    diagnostics: Diagnostics,
) -> None:
    """One `images` pin: a whole image reference with a tag or digest, for a service that has an `image` to replace,
    and naming the image that service runs (`docs/design/version-pin-overlay.md`).

    `services` holds every `(module name, service)` whose service is called `name`.
    """
    declared = [(module, service.image) for module, service in services if service.image]
    problem = image_pin_problem(value, name_hint=parse_image_ref(declared[0][1])[0] if declared else None)
    if problem is not None:
        diagnostics.error(
            f"image pin '{name}': {problem}", source=source, location=f"{location}.version", code="image_pin_invalid"
        )
    if not services:
        diagnostics.warning(
            f"image pin '{name}' matched no module service", source=source, location=location, code="stale_pin"
        )
        return
    if not declared:
        modules = ", ".join(f"'{module}'" for module, _ in services)
        hint = (
            " A service with an `artifact` is pinned under pins.artifacts."
            if any(service.artifact for _, service in services)
            else ""
        )
        diagnostics.warning(
            f"image pin '{name}' targets a service that declares no image (module {modules}), so the pin "
            f"changes nothing.{hint}",
            source=source,
            location=location,
            code="pin_not_applicable",
        )
        return
    if problem is not None:
        return
    pinned = canonical_image_name(parse_image_ref(value)[0])
    if all(canonical_image_name(parse_image_ref(image)[0]) != pinned for _, image in declared):
        runs = ", ".join(f"'{parse_image_ref(image)[0]}' (module '{module}')" for module, image in declared)
        diagnostics.warning(
            f"image pin '{name}' pulls '{parse_image_ref(value)[0]}', but service '{name}' runs {runs}. An image pin "
            "is meant to move the tag; to run another image, change the module's `image`.",
            source=source,
            location=f"{location}.version",
            code="image_pin_renamed",
        )


def _services_by_name(index: DocumentIndex) -> dict[str, list[tuple[str, ModuleServiceModel]]]:
    """Every `ModuleServiceModel` across every indexed Module document, by service name, with its module's name.

    Flat and global, matching the pin key's own scope (`VersionPinsModel`'s
    docstring: "Key = ModuleServiceModel.name", not module-qualified) — the
    same flat namespace v1's real production file used.
    """
    services: dict[str, list[tuple[str, ModuleServiceModel]]] = {}
    for entry in index.all_of(PlatformKind.MODULE):
        module = cast(ModuleModel, entry.model)
        for service in module.spec.services or []:
            services.setdefault(service.name, []).append((module.meta.name, service))
    return services
