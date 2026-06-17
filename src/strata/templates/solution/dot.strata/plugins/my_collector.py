"""
Custom SBOM collector template for strata.

Copy / rename this file and implement the class to add a new component source
to the SBOM pipeline.  Declare it in .strata/collectors.yaml so the builder
picks it up:

    collectors:
      - name: my-collector
        path: .strata/plugins/my_collector.py
        class: MyCollector
        type: collector

The collector is appended after all built-in collectors.  No changes to strata
core code are required.

Minimal checklist
-----------------
1. Rename the class and update get_collector_name().
2. Implement collect() — return a list of SbomComponentModel instances.
3. Use self._add_warning(msg) for non-fatal issues (unreadable files, missing
   versions, etc.).  Never raise from collect().
4. Build PURLs using the pkg:{ecosystem}/{name}@{version} format.
   See https://github.com/package-url/purl-spec for the spec.
5. Update .strata/collectors.yaml with the correct class name.

How it works
------------
CollectorPluginLoader reads .strata/collectors.yaml at build time and imports
each declared file.  For type=collector entries it instantiates the named
class and appends it to SbomBuilder's collector list.  Collectors run in
declaration order, after all built-ins.

The collect() method receives:
  - platform   PlatformArtifactModel  the in-memory build artifact (platform.json)
  - work_path  Path                   workspace root (.strata/ lives here)
  - deployment_build_path  Path       directory where build outputs land

Return an empty list if no components are found — that is always valid.

Example YAML (after renaming this file and the class inside)
------------------------------------------------------------
  collectors:
    - name: cargo
      path: .strata/plugins/my_collector.py
      class: CargoCollector
      type: collector
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.models.sbom_model import SbomComponentModel

# from strata.models.platform_artifact_model import PlatformArtifactModel  # type hint only


class MyCollector(BaseSbomCollector):
    """Replace this docstring with a description of what this collector scans."""

    # ------------------------------------------------------------------ #
    # Identity                                                             #
    # ------------------------------------------------------------------ #

    def get_collector_name(self) -> str:
        """Short snake_case identifier used in SbomComponentModel.source_collector.

        Must be unique across all active collectors.  Examples: "cargo", "maven".
        """
        return "my_collector"

    # ------------------------------------------------------------------ #
    # Collection                                                           #
    # ------------------------------------------------------------------ #

    def collect(
        self,
        platform,  # PlatformArtifactModel
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        """Discover components and return them as SbomComponentModel instances.

        Guidelines:
        - Use self._add_warning(msg) for non-fatal issues (missing files,
          unparseable entries, absent version pins).
        - Never raise — return an empty list if nothing can be collected.
        - Deduplicate by purl before returning if your source may have repeats.
        """
        components: List[SbomComponentModel] = []

        # ----------------------------------------------------------------
        # Example: scan all Cargo.lock files under work_path
        # ----------------------------------------------------------------
        # for lock_file in work_path.rglob("Cargo.lock"):
        #     try:
        #         import tomllib
        #         with lock_file.open("rb") as fh:
        #             data = tomllib.load(fh)
        #         for pkg in data.get("package", []):
        #             name = pkg.get("name", "")
        #             version = pkg.get("version")
        #             if not name:
        #                 continue
        #             purl = f"pkg:cargo/{name}@{version}" if version else f"pkg:cargo/{name}"
        #             components.append(SbomComponentModel(
        #                 name=name,
        #                 version=version,
        #                 purl=purl,
        #                 source_collector=self.get_collector_name(),
        #             ))
        #     except Exception as exc:
        #         self._add_warning(f"Could not parse {lock_file}: {exc}")

        return components
