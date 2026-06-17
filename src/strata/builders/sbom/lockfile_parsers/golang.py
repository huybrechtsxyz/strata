"""Go dependency parser: go.sum."""

from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class GoSumParser(LockfileParser):
    """Parse ``go.sum`` — line-by-line ``module version h1:...`` format.

    Skips ``/go.mod`` entries (checksums for module descriptors).
    De-duplicates by module path — first occurrence wins.
    """

    @property
    def ecosystem(self) -> str:
        return "golang"

    def filename_patterns(self) -> List[str]:
        return ["go.sum"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(str(exc)) from exc

        seen: dict[str, str] = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            module_path = parts[0]
            version_str = parts[1]
            # Skip /go.mod checksum entries
            if version_str.endswith("/go.mod"):
                continue
            if module_path not in seen:
                seen[module_path] = version_str

        return [RawDependency(name=mod, version=ver) for mod, ver in seen.items()]
