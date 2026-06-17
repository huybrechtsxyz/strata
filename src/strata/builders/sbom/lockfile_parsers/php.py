"""PHP dependency parser: composer.lock."""

import json
from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class ComposerLockParser(LockfileParser):
    """Parse ``composer.lock`` (PHP Composer lockfile).

    Reads ``packages`` and ``packages-dev`` arrays.  Each entry has ``name``
    and ``version`` fields.
    """

    @property
    def ecosystem(self) -> str:
        return "packagist"

    def filename_patterns(self) -> List[str]:
        return ["composer.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for section in ("packages", "packages-dev"):
            for pkg in data.get(section) or []:
                name = pkg.get("name")
                if not name:
                    continue
                version = pkg.get("version")
                deps.append(RawDependency(name=str(name), version=str(version) if version else None))
        return deps
