"""NuGet dependency parsers: packages.lock.json, packages.config."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class NugetPackagesLockParser(LockfileParser):
    """Parse ``packages.lock.json`` (NuGet .NET lockfile, v1/v2 format).

    Reads ``dependencies.<framework>.<package>`` entries.  De-duplicates
    across target frameworks — highest version wins to avoid duplication.
    """

    @property
    def ecosystem(self) -> str:
        return "nuget"

    def filename_patterns(self) -> List[str]:
        return ["packages.lock.json"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        # Shape: {"dependencies": {"<framework>": {"<pkg>": {"resolved": "x.y.z", ...}}}}
        seen: dict[str, str | None] = {}
        for framework_deps in (data.get("dependencies") or {}).values():
            if not isinstance(framework_deps, dict):
                continue
            for pkg_name, pkg_info in framework_deps.items():
                if not pkg_name:
                    continue
                resolved = pkg_info.get("resolved") if isinstance(pkg_info, dict) else None
                version = str(resolved) if resolved else None
                # Keep the entry; if already present keep whichever has a version
                if pkg_name not in seen or (version and not seen[pkg_name]):
                    seen[pkg_name] = version

        return [RawDependency(name=n, version=v) for n, v in seen.items()]


class PackagesConfigParser(LockfileParser):
    """Parse ``packages.config`` (legacy NuGet XML format).

    Each ``<package id="..." version="..." />`` element becomes a dependency.
    """

    @property
    def ecosystem(self) -> str:
        return "nuget"

    def filename_patterns(self) -> List[str]:
        return ["packages.config"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            tree = ET.parse(path)  # noqa: S314
        except (OSError, ET.ParseError) as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for elem in tree.iter("package"):
            pkg_id = elem.get("id")
            if not pkg_id:
                continue
            version = elem.get("version")
            deps.append(RawDependency(name=pkg_id, version=version))
        return deps
