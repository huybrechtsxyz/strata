"""Ruby dependency parser: Gemfile.lock."""

import re
from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class GemfileLockParser(LockfileParser):
    """Parse ``Gemfile.lock`` (Bundler / Ruby lockfile).

    Reads the ``GEM`` and ``GIT`` sections.  Each ``    <name> (<version>)``
    line inside a ``specs:`` block becomes a dependency.
    """

    @property
    def ecosystem(self) -> str:
        return "gem"

    def filename_patterns(self) -> List[str]:
        return ["Gemfile.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        in_specs = False
        gem_sections = {"GEM", "GIT", "PATH"}
        current_section: str | None = None

        for line in lines:
            stripped = line.rstrip()
            # Detect section headers (no leading spaces)
            if stripped and not stripped[0].isspace():
                current_section = stripped.split()[0].rstrip(":")
                in_specs = False
                continue

            if current_section in gem_sections:
                if stripped.strip() == "specs:":
                    in_specs = True
                    continue
                if in_specs:
                    # Spec lines: "    name (version)" — exactly 4 spaces of indent
                    m = re.match(r"^    ([^\s(]+) \(([^)]+)\)$", stripped)
                    if m:
                        deps.append(RawDependency(name=m.group(1), version=m.group(2)))
                    elif stripped and not stripped[0] == " ":
                        in_specs = False

        return deps
