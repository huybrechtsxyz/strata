"""Node.js dependency parser: package-lock.json."""

import json
from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class PackageLockJsonParser(LockfileParser):
    """Parse ``package-lock.json`` (npm v2/v3 lockfile format).

    Reads the ``packages`` dict.  The root package (empty string key) is
    skipped.  Scoped packages (``@scope/name``) are kept as-is.
    """

    @property
    def ecosystem(self) -> str:
        return "npm"

    def filename_patterns(self) -> List[str]:
        return ["package-lock.json"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        packages = data.get("packages") or {}
        deps: List[RawDependency] = []
        for key, pkg_data in packages.items():
            if not key:  # skip root ""
                continue
            # Key: "node_modules/pkgname" or "node_modules/@scope/pkgname"
            name = key.removeprefix("node_modules/")
            version = pkg_data.get("version") if isinstance(pkg_data, dict) else None
            deps.append(RawDependency(name=name, version=str(version) if version else None))
        return deps
