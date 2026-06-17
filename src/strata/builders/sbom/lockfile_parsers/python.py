"""Python dependency parsers: requirements.txt, pyproject.toml, uv.lock."""

import re
import tomllib
from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class RequirementsTxtParser(LockfileParser):
    """Parse ``requirements*.txt`` files (pip format).

    Only strict pin (``==``) produces a versioned ``RawDependency``.
    Loose constraints (``>=``, ``~=``, etc.) produce ``version=None``.
    Skips blank lines, comments (``#``), options (``-r``, ``-e``, ``--``),
    and URL-based installs.
    """

    @property
    def ecosystem(self) -> str:
        return "pypi"

    def filename_patterns(self) -> List[str]:
        return ["requirements*.txt"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for raw_line in lines:
            line = raw_line.strip()
            # Skip blanks, comments, options, editable/URL installs
            if not line or line.startswith(("#", "-", "http")):
                continue
            # Strip inline comments
            line = line.split(" #")[0].split("\t#")[0].strip()
            if not line:
                continue
            # Strict pin
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s,;\[\\]+)", line)
            if m:
                deps.append(RawDependency(name=m.group(1), version=m.group(2)))
                continue
            # Unpinned — extract name only
            m2 = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if m2:
                deps.append(RawDependency(name=m2.group(1), version=None))
        return deps


class PyprojectTomlParser(LockfileParser):
    """Parse ``pyproject.toml`` — reads ``[project.dependencies]`` (PEP 508).

    Only ``==`` pins produce a versioned ``RawDependency``.
    """

    @property
    def ecosystem(self) -> str:
        return "pypi"

    def filename_patterns(self) -> List[str]:
        return ["pyproject.toml"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(str(exc)) from exc

        raw_deps = (data.get("project") or {}).get("dependencies") or []
        deps: List[RawDependency] = []
        for dep_str in raw_deps:
            if not isinstance(dep_str, str):
                continue
            dep_str = dep_str.strip()
            # Name is everything up to the first version spec, extras marker, or env marker
            name_m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", dep_str)
            if not name_m:
                continue
            name = name_m.group(1)
            pin_m = re.search(r"==([^\s,;\[\\]+)", dep_str)
            version = pin_m.group(1) if pin_m else None
            deps.append(RawDependency(name=name, version=version))
        return deps


class UvLockParser(LockfileParser):
    """Parse ``uv.lock`` — reads ``[[package]]`` TOML entries (uv lockfile format).

    Every entry in a uv lockfile has an exact ``version`` — no version=None.
    """

    @property
    def ecosystem(self) -> str:
        return "pypi"

    def filename_patterns(self) -> List[str]:
        return ["uv.lock"]

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
