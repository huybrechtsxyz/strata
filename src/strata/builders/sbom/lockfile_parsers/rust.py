"""Rust dependency parser: Cargo.lock."""

import tomllib
from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class CargoLockParser(LockfileParser):
    """Parse ``Cargo.lock`` (Rust/Cargo lockfile, v3 TOML format).

    Reads ``[[package]]`` entries.  Each entry has ``name`` and ``version``.
    """

    @property
    def ecosystem(self) -> str:
        return "cargo"

    def filename_patterns(self) -> List[str]:
        return ["Cargo.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for pkg in data.get("package") or []:
            name = pkg.get("name")
            if not name:
                continue
            version = pkg.get("version")
            deps.append(RawDependency(name=str(name), version=str(version) if version else None))
        return deps
