# Custom SBOM Plugin Examples

Ready-to-use example implementations for lockfile parsers and collectors.

---

## Example 1: Ruby Bundler Gemfile.lock Parser

A complete lockfile parser for Ruby Bundler manifests.

**File:** `.strata/lockfile_parsers/bundler_gemfile.py`

```python
"""Parse Ruby Bundler Gemfile.lock files."""

from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class BundlerGemdockParser(LockfileParser):
    """Parse Ruby Bundler's Gemfile.lock format.

    Extracts gem names and versions from the BUNDLED WITH section.
    Format:

        GEM
          remote: https://rubygems.org/
          specs:
            actioncable (7.0.0)
            actionmail (7.0.0)
            ...
        PLATFORMS
          ruby
        DEPENDENCIES
          rails (~> 7.0.0)
        BUNDLED WITH
           2.3.0
    """

    @property
    def ecosystem(self) -> str:
        return "gem"

    def filename_patterns(self) -> List[str]:
        return ["Gemfile.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        """Extract gem dependencies from Gemfile.lock."""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"Could not read {path.name}: {exc}") from exc

        deps: List[RawDependency] = []
        in_specs = False

        for line in lines:
            # Skip empty lines and comments
            if not line.strip() or line.strip().startswith("#"):
                continue

            # Enter specs section
            if line.strip() == "specs:":
                in_specs = True
                continue

            # Exit specs section
            if in_specs and line[0] not in (" ", "\t"):
                in_specs = False

            # Parse spec line: "    gem_name (version)"
            if in_specs and line.startswith(("  ", "\t")):
                spec_line = line.strip()
                if not spec_line or "(" not in spec_line:
                    continue

                # Extract gem name and version
                name_part, version_part = spec_line.rsplit("(", 1)
                name = name_part.strip()
                version = version_part.rstrip(")").strip()

                if name and version:
                    deps.append(RawDependency(name=name, version=version))

        return deps
```

**Usage:**

Drop this file into `.strata/lockfile_parsers/bundler_gemfile.py`, then run:

```bash
strata build sbom -f deploy.yaml
```

The Ruby gems from `Gemfile.lock` will be included in the SBOM.

---

## Example 2: Go go.mod Parser

Parse Go module dependencies.

**File:** `.strata/lockfile_parsers/golang_mod.py`

```python
"""Parse Go go.mod files."""

from pathlib import Path
from typing import List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class GoModParser(LockfileParser):
    """Parse Go go.mod files.

    Extracts module dependencies from the require section.
    Format:

        module github.com/example/myapp

        go 1.21

        require (
            github.com/google/uuid v1.5.0
            github.com/sirupsen/logrus v1.9.3
        )

        require github.com/stretchr/testify v1.8.4 // indirect
    """

    @property
    def ecosystem(self) -> str:
        return "golang"

    def filename_patterns(self) -> List[str]:
        return ["go.mod"]

    def parse(self, path: Path) -> List[RawDependency]:
        """Extract Go module dependencies from go.mod."""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError(f"Could not read {path.name}: {exc}") from exc

        deps: List[RawDependency] = []
        in_require_block = False

        for line in lines:
            stripped = line.strip()

            # Skip empty lines and comments
            if not stripped or stripped.startswith("//"):
                continue

            # Detect require block start
            if stripped == "require (":
                in_require_block = True
                continue

            # Exit require block
            if in_require_block and stripped == ")":
                in_require_block = False
                continue

            # Parse single-line require statements
            if stripped.startswith("require "):
                stripped = stripped[8:].strip()
                in_require_block = False

            # Parse require lines (inside or outside block)
            if stripped and not stripped.startswith("module ") and not stripped.startswith("go "):
                # Format: "github.com/user/pkg v1.2.3" or with "// indirect"
                parts = stripped.split()
                if len(parts) >= 2:
                    module_name = parts[0]
                    version = parts[1]
                    # Strip "// indirect" or similar comments
                    if version.startswith("//"):
                        continue
                    deps.append(RawDependency(name=module_name, version=version))

        return deps
```

---

## Example 3: Composer (PHP) Parser

Parse PHP Composer lock files.

**File:** `.strata/lockfile_parsers/php_composer.py`

```python
"""Parse PHP Composer lock files."""

import json
from pathlib import Path
from typing import Any, Dict, List

from strata.builders.sbom.lockfile_parsers._base import LockfileParser, RawDependency


class ComposerLockParser(LockfileParser):
    """Parse PHP Composer composer.lock files.

    Extracts package names and versions from the packages array.
    """

    @property
    def ecosystem(self) -> str:
        return "composer"

    def filename_patterns(self) -> List[str]:
        return ["composer.lock"]

    def parse(self, path: Path) -> List[RawDependency]:
        """Extract PHP packages from composer.lock."""
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to parse {path.name}: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")

        deps: List[RawDependency] = []

        # Parse packages array
        for section in ["packages", "packages-dev"]:
            packages: List[Dict[str, Any]] = data.get(section) or []
            for pkg in packages:
                if not isinstance(pkg, dict):
                    continue
                name = pkg.get("name")
                version = pkg.get("version")
                if name:
                    deps.append(RawDependency(name=str(name), version=version or None))

        return deps
```

---

## Example 4: Custom Collector — Database Schemas

A collector that extracts database schema versions from infrastructure code.

**File:** `.strata/collectors/database_schemas_collector.py`

```python
"""Collect database schema components from infrastructure code."""

from pathlib import Path
from typing import List

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.logger import get_logger
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel

logger = get_logger(__name__)


class DatabaseSchemasCollector(BaseSbomCollector):
    """Collect database and schema version components.

    This is an example collector that scans for database declarations
    in platform modules and produces SBOM components for them.

    In a real implementation, this might read from:
    - Database resource declarations (e.g., Azure SQL, RDS)
    - Migration versions
    - ORM configuration files
    """

    def get_collector_name(self) -> str:
        return "database_schemas"

    def collect(
        self,
        platform: PlatformArtifactModel,
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        """Extract database components from platform specification."""
        self._reset_warnings()
        components: List[SbomComponentModel] = []

        if not platform.spec or not platform.spec.modules:
            return components

        seen_databases: set[str] = set()

        for module in platform.spec.modules:
            # Example: assume modules have a 'databases' list
            # In real code, adapt this to your actual schema structure
            databases = getattr(module, "databases", None) or []

            for db in databases:
                db_name = getattr(db, "name", "")
                db_engine = getattr(db, "engine", "")  # e.g., "postgres", "mysql"
                db_version = getattr(db, "version", "")

                if not db_name:
                    continue

                purl_key = f"{db_engine}:{db_name}:{db_version}"
                if purl_key in seen_databases:
                    continue
                seen_databases.add(purl_key)

                # Build a meaningful PURL
                purl = f"pkg:generic/{db_engine}/{db_name}@{db_version or 'unknown'}"

                components.append(
                    SbomComponentModel(
                        component_type="framework",
                        name=f"{db_name} ({db_engine})",
                        version=db_version,
                        purl=purl,
                        properties={
                            "database_engine": db_engine,
                            "database_name": db_name,
                        },
                        source_collector=self.get_collector_name(),
                    )
                )

                logger.debug(
                    "Collected database schema",
                    name=db_name,
                    engine=db_engine,
                    version=db_version,
                )

        return components
```

**Register in `.strata/collectors.yaml`:**

```yaml
collectors:
  - name: database-schemas
    path: .strata/collectors/database_schemas_collector.py
    class: DatabaseSchemasCollector
    type: collector
```

---

## Example 5: Custom Collector — External APIs

A collector that documents external API dependencies.

**File:** `.strata/collectors/external_apis_collector.py`

```python
"""Collect external API dependencies."""

from pathlib import Path
from typing import List

from strata.builders.sbom.base_sbom_collector import BaseSbomCollector
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.models.sbom_model import SbomComponentModel


class ExternalApisCollector(BaseSbomCollector):
    """Document external APIs used by the application.

    This collector scans configuration files and infrastructure code
    to identify external API integrations and document them in the SBOM.

    Example components:
    - Stripe API v1.3
    - Slack Webhooks
    - Auth0 Management API
    """

    def get_collector_name(self) -> str:
        return "external_apis"

    def collect(
        self,
        platform: PlatformArtifactModel,
        work_path: Path,
        deployment_build_path: Path,
    ) -> List[SbomComponentModel]:
        """Extract external API components."""
        self._reset_warnings()
        components: List[SbomComponentModel] = []

        # Scan config files for API declarations
        config_dir = work_path / "config"
        apis_file = config_dir / "external-apis.yaml"

        if not apis_file.exists():
            self._warnings.append(f"External APIs config not found: {apis_file}")
            return components

        try:
            import yaml

            with apis_file.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception as exc:
            self._warnings.append(f"Failed to parse {apis_file}: {exc}")
            return components

        apis = data.get("external_apis") or []
        seen_purls: set[str] = set()

        for api_spec in apis:
            if not isinstance(api_spec, dict):
                continue

            api_name = api_spec.get("name", "")
            api_version = api_spec.get("version", "1.0")
            api_url = api_spec.get("url", "")
            api_status = api_spec.get("status", "active")

            if not api_name:
                continue

            purl = f"pkg:generic/api/{api_name}@{api_version}"
            if purl in seen_purls:
                continue
            seen_purls.add(purl)

            components.append(
                SbomComponentModel(
                    component_type="framework",
                    name=api_name,
                    version=api_version,
                    purl=purl,
                    properties={
                        "type": "external_api",
                        "url": api_url,
                        "status": api_status,
                    },
                    source_collector=self.get_collector_name(),
                )
            )

        return components
```

**Config file (`.strata/external-apis.yaml`):**

```yaml
external_apis:
  - name: Stripe
    version: v1.3
    url: https://api.stripe.com
    status: active

  - name: Slack
    version: 1.0
    url: https://hooks.slack.com
    status: active

  - name: Auth0
    version: 2.0
    url: https://auth0.com
    status: active
```

**Register in `.strata/collectors.yaml`:**

```yaml
collectors:
  - name: external-apis
    path: .strata/collectors/external_apis_collector.py
    class: ExternalApisCollector
    type: collector
```

---

## Testing Examples

### Test a Lockfile Parser

```python
# tests/sbom/test_bundler_parser.py

from pathlib import Path
import pytest
from strata.builders.sbom.lockfile_parsers.bundler_gemfile import (
    BundlerGemdockParser,
)


class TestBundlerParser:
    def test_parse_valid_gemfile_lock(self, tmp_path):
        """Parse a valid Gemfile.lock."""
        parser = BundlerGemdockParser()

        gemfile_lock = tmp_path / "Gemfile.lock"
        gemfile_lock.write_text("""
GEM
  remote: https://rubygems.org/
  specs:
    rails (7.0.0)
    bundler (2.3.0)

PLATFORMS
  ruby

DEPENDENCIES
  rails (~> 7.0.0)

BUNDLED WITH
   2.3.0
""")

        deps = parser.parse(gemfile_lock)
        assert len(deps) == 2
        assert deps[0].name == "rails"
        assert deps[0].version == "7.0.0"
        assert deps[1].name == "bundler"

    def test_ecosystem(self):
        """Parser identifies as gem ecosystem."""
        parser = BundlerGemdockParser()
        assert parser.ecosystem == "gem"

    def test_filename_patterns(self):
        """Parser matches Gemfile.lock."""
        parser = BundlerGemdockParser()
        assert "Gemfile.lock" in parser.filename_patterns()
```

### Test a Collector

```python
# tests/sbom/test_database_collector.py

from pathlib import Path
from unittest.mock import MagicMock
import pytest
from strata.builders.sbom.database_schemas_collector import (
    DatabaseSchemasCollector,
)
from strata.models.platform_artifact_model import PlatformArtifactModel


class TestDatabaseCollector:
    def test_collect_databases(self, tmp_path):
        """Collector extracts database components."""
        collector = DatabaseSchemasCollector()

        # Mock database objects
        db1 = MagicMock()
        db1.name = "users_db"
        db1.engine = "postgres"
        db1.version = "14.5"

        db2 = MagicMock()
        db2.name = "analytics_db"
        db2.engine = "mysql"
        db2.version = "8.0.32"

        # Mock module with databases
        module = MagicMock()
        module.databases = [db1, db2]

        # Mock platform artifact
        platform = MagicMock(spec=PlatformArtifactModel)
        platform.spec = MagicMock()
        platform.spec.modules = [module]

        components = collector.collect(platform, tmp_path, tmp_path / "build")

        assert len(components) == 2
        assert components[0].name == "users_db (postgres)"
        assert components[0].version == "14.5"
        assert components[1].name == "analytics_db (mysql)"

    def test_collect_deduplicates(self, tmp_path):
        """Collector deduplicates databases."""
        collector = DatabaseSchemasCollector()

        db = MagicMock()
        db.name = "shared_db"
        db.engine = "postgres"
        db.version = "14.5"

        # Same database in two modules
        module1 = MagicMock()
        module1.databases = [db]

        module2 = MagicMock()
        module2.databases = [db]

        platform = MagicMock(spec=PlatformArtifactModel)
        platform.spec = MagicMock()
        platform.spec.modules = [module1, module2]

        components = collector.collect(platform, tmp_path, tmp_path / "build")

        # Should have only one component due to deduplication
        assert len(components) == 1
```

---

## Integration Testing

Test end-to-end SBOM generation with custom plugins:

```bash
# Set up test workspace
mkdir -p test-workspace/.strata/lockfile_parsers
mkdir -p test-workspace/.strata/collectors
mkdir -p test-workspace/config

# Copy your plugins
cp bundler_gemfile.py test-workspace/.strata/lockfile_parsers/
cp database_schemas_collector.py test-workspace/.strata/collectors/

# Create collectors.yaml
cat > test-workspace/.strata/collectors.yaml <<EOF
collectors:
  - name: database-schemas
    path: .strata/collectors/database_schemas_collector.py
    class: DatabaseSchemasCollector
    type: collector

  - name: bundler
    path: .strata/lockfile_parsers/bundler_gemfile.py
    type: lockfile_parser
EOF

# Create a test deployment file
cat > test-workspace/deploy.yaml <<EOF
apiVersion: strata.huybrechtsxyz/v1
kind: deployment
meta:
  name: test-deployment
spec:
  modules:
    - name: api
      services:
        - name: web
          image: nginx:1.25.0
EOF

# Generate SBOM
cd test-workspace
strata build sbom -f deploy.yaml --output json

# Check the results
cat build/test-deployment-1.0.0/sbom.json | jq '.components | length'
```

---

## See Also

- [Extending strata SBOM](./extending-sbom-plugins.md) — Main guide
- [SBOM Plugin API](../platform/sbom-plugin-api.md) — API reference
- [Testing Patterns](../guides/testing-strata-extensions.md) — More test examples
