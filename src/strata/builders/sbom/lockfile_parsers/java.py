"""Java/JVM dependency parsers: pom.xml, gradle.lockfile."""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class MavenPomParser(LockfileParser):
    """Parse ``pom.xml`` (Apache Maven project descriptor).

    Reads ``<dependencies><dependency>`` elements at the top level.
    Handles the default XML namespace ``http://maven.apache.org/POM/4.0.0``.
    Reports ``groupId:artifactId`` as the package name and ``version`` when
    present (property interpolation is intentionally not resolved).
    """

    @property
    def ecosystem(self) -> str:
        return "maven"

    def filename_patterns(self) -> List[str]:
        return ["pom.xml"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            tree = ET.parse(path)  # noqa: S314
        except (OSError, ET.ParseError) as exc:
            raise ValueError(str(exc)) from exc

        root = tree.getroot()
        # pom.xml may or may not carry the namespace
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        deps: List[RawDependency] = []
        for dep in root.iter(f"{ns}dependency"):
            group_id = dep.findtext(f"{ns}groupId") or ""
            artifact_id = dep.findtext(f"{ns}artifactId") or ""
            if not artifact_id:
                continue
            name = f"{group_id}:{artifact_id}" if group_id else artifact_id
            version_text = dep.findtext(f"{ns}version")
            # Skip property references like ${project.version}
            version = version_text if version_text and not version_text.startswith("$") else None
            deps.append(RawDependency(name=name, version=version))
        return deps


class GradleLockParser(LockfileParser):
    """Parse ``gradle.lockfile`` (Gradle dependency locking output).

    Format: ``group:artifact:version=<configurations...>``  (one per line).
    Lines starting with ``#`` or ``empty=`` are skipped.
    """

    @property
    def ecosystem(self) -> str:
        return "maven"

    def filename_patterns(self) -> List[str]:
        return ["gradle.lockfile"]

    def parse(self, path: Path) -> List[RawDependency]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(str(exc)) from exc

        deps: List[RawDependency] = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("empty="):
                continue
            # format: group:artifact:version=config1,config2
            coord, _, _ = line.partition("=")
            parts = coord.split(":")
            if len(parts) < 3:
                continue
            group, artifact, version = parts[0], parts[1], parts[2]
            name = f"{group}:{artifact}" if group else artifact
            deps.append(RawDependency(name=name, version=version or None))
        return deps
