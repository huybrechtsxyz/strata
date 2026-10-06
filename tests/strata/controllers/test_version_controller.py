#!/usr/bin/env python3
"""Tests for `version_controller.py` (`strata version new`/`update`/`set`)."""

from pathlib import Path

import pytest
import yaml

from strata.controllers.path_controller import get_path
from strata.controllers.solution_context import open_solution
from strata.controllers.version_controller import reconcile_version, scaffold_version, set_version_pin
from strata.models.common_models import PlatformKind
from strata.models.version_model import VersionModel, VersionPinStatus
from strata.utils.errors import UsageError

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec:
  remotes:
    - name: charts
      type: oci
      url: oci://ghcr.io/org/charts
      reference: v1.2.3
    - name: pinned-bundle
      type: oci
      url: oci://ghcr.io/org/bundle
      fetch: external
"""

PROVIDER = """apiVersion: strata.huybrechts.xyz/v2
kind: provider
meta:
  name: azure-main
spec:
  properties:
    type: azure
    region: westeurope
"""

WORKSPACE = """apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
"""

MODULE = """apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: web
spec:
  source:
    source_path: modules/web
  services:
    - name: web
      image: nginx:1.27-alpine
    - name: sidecar
"""

CHART_MODULE = """apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: authentik
spec:
  source:
    remote: charts
    chart_name: authentik
    chart_version: "2026.5.2"
"""

ARTIFACT = """apiVersion: strata.huybrechts.xyz/v2
kind: artifact
meta:
  name: dspapi_container
spec:
  image_name: int-docker-test/src/acme.dispatcher.api
  image_tag: "1.0.0"
"""


def _solution(tmp_path: Path, *, extra: dict[str, str] | None = None) -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "provider.yaml").write_text(PROVIDER, encoding="utf-8")
    (root / "workspace.yaml").write_text(WORKSPACE, encoding="utf-8")
    (root / "module-web.yaml").write_text(MODULE, encoding="utf-8")
    (root / "module-authentik.yaml").write_text(CHART_MODULE, encoding="utf-8")
    (root / "artifact.yaml").write_text(ARTIFACT, encoding="utf-8")
    for relative, content in (extra or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _version_doc(*, workspace: str | None = None, pins: str = "  pins: {}\n") -> str:
    workspace_line = f"  workspace: {workspace}\n" if workspace else ""
    return f"apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd\nspec:\n{workspace_line}{pins}"


# ---------------------------------------------------------------------------
# reconcile_version
# ---------------------------------------------------------------------------


def test_reconcile_reports_add_candidates_for_unpinned_real_targets(tmp_path):
    root = _solution(tmp_path, extra={"version.yaml": _version_doc()})
    context = open_solution(root)
    entry = context.controller.index.get(PlatformKind.VERSION, "prd")

    rows = reconcile_version(context.controller.index, context.controller.solution, entry.model)

    by_key = {(r.category, r.name): r for r in rows}
    assert by_key[("images", "web")].action == "add"
    assert by_key[("images", "web")].seed_value == "nginx:1.27-alpine"
    assert by_key[("charts", "authentik")].action == "add"
    assert by_key[("charts", "authentik")].seed_value == "2026.5.2"
    assert by_key[("artifacts", "dspapi_container")].action == "add"
    assert by_key[("artifacts", "dspapi_container")].seed_value == "1.0.0"


def test_reconcile_never_offers_add_for_a_target_with_no_current_value(tmp_path):
    """'sidecar' declares no image of its own — nothing honest to seed it with."""
    root = _solution(tmp_path, extra={"version.yaml": _version_doc()})
    context = open_solution(root)
    entry = context.controller.index.get(PlatformKind.VERSION, "prd")

    rows = reconcile_version(context.controller.index, context.controller.solution, entry.model)

    assert not any(r.category == "images" and r.name == "sidecar" for r in rows)


def test_reconcile_never_offers_add_for_a_fetch_external_remote(tmp_path):
    """A fetch: external remote can never take effect as a pin — never offered."""
    root = _solution(tmp_path, extra={"version.yaml": _version_doc()})
    context = open_solution(root)
    entry = context.controller.index.get(PlatformKind.VERSION, "prd")

    rows = reconcile_version(context.controller.index, context.controller.solution, entry.model)

    assert not any(r.category == "remotes" and r.name == "pinned-bundle" for r in rows)
    assert any(r.category == "remotes" and r.name == "charts" for r in rows)


def test_reconcile_reports_remove_candidates_for_stale_pins(tmp_path):
    pins = "  pins:\n    images:\n      ghost: 1.0.0\n"
    root = _solution(tmp_path, extra={"version.yaml": _version_doc(pins=pins)})
    context = open_solution(root)
    entry = context.controller.index.get(PlatformKind.VERSION, "prd")

    rows = reconcile_version(context.controller.index, context.controller.solution, entry.model)

    ghost_row = next(r for r in rows if r.category == "images" and r.name == "ghost")
    assert ghost_row.action == "remove"
    assert ghost_row.seed_value is None


def test_reconcile_never_mutates_the_document_or_disk(tmp_path):
    """Read-only: a proposal, never an auto-apply."""
    root = _solution(tmp_path, extra={"version.yaml": _version_doc()})
    before = (root / "version.yaml").read_text(encoding="utf-8")
    context = open_solution(root)
    entry = context.controller.index.get(PlatformKind.VERSION, "prd")

    reconcile_version(context.controller.index, context.controller.solution, entry.model)

    assert (root / "version.yaml").read_text(encoding="utf-8") == before


def test_fully_reconciled_document_has_no_candidates(tmp_path):
    pins = (
        "  pins:\n"
        "    images:\n"
        "      web: nginx:1.27-alpine\n"
        "    charts:\n"
        "      authentik: '2026.5.2'\n"
        "    remotes:\n"
        "      charts: v1.2.3\n"
        "    artifacts:\n"
        "      dspapi_container: '1.0.0'\n"
    )
    root = _solution(tmp_path, extra={"version.yaml": _version_doc(pins=pins)})
    context = open_solution(root)
    entry = context.controller.index.get(PlatformKind.VERSION, "prd")

    rows = reconcile_version(context.controller.index, context.controller.solution, entry.model)

    assert rows == []


# ---------------------------------------------------------------------------
# scaffold_version
# ---------------------------------------------------------------------------


def test_scaffold_creates_a_minimal_document(tmp_path):
    root = _solution(tmp_path)
    context = open_solution(root)

    destination = scaffold_version(
        root, context.controller.index, context.controller.solution, "dev", workspace="main", from_name=None
    )

    assert destination == root / "versions" / "dev.yaml"
    data = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert data["kind"] == "version"
    assert data["meta"]["name"] == "dev"
    assert data["spec"]["workspace"] == "main"
    assert data["spec"]["pins"] == {}
    # Round-trips through the real model.
    VersionModel.model_validate(data)


def test_scaffold_rejects_a_colliding_name(tmp_path):
    root = _solution(tmp_path, extra={"version.yaml": _version_doc()})
    context = open_solution(root)

    with pytest.raises(UsageError, match="already exists"):
        scaffold_version(
            root, context.controller.index, context.controller.solution, "prd", workspace="main", from_name=None
        )


def test_scaffold_rejects_an_unknown_workspace(tmp_path):
    root = _solution(tmp_path)
    context = open_solution(root)

    with pytest.raises(UsageError, match="unknown workspace"):
        scaffold_version(
            root, context.controller.index, context.controller.solution, "dev", workspace="ghost", from_name=None
        )


def test_scaffold_rejects_an_unknown_from_name(tmp_path):
    root = _solution(tmp_path)
    context = open_solution(root)

    with pytest.raises(UsageError, match="unknown version document"):
        scaffold_version(
            root, context.controller.index, context.controller.solution, "dev", workspace="main", from_name="ghost"
        )


def test_scaffold_rejects_a_name_that_would_not_be_schema_valid(tmp_path):
    """Caught before writing — not left for the next `strata validate` to
    silently discover a permanently-broken file."""
    root = _solution(tmp_path)
    context = open_solution(root)

    with pytest.raises(UsageError, match="not be a valid version document"):
        scaffold_version(
            root,
            context.controller.index,
            context.controller.solution,
            "Invalid Name",
            workspace="main",
            from_name=None,
        )

    assert not (root / "versions" / "Invalid Name.yaml").exists()


def test_scaffold_from_clones_keys_seeded_with_current_values_not_source_values(tmp_path):
    pins = "  pins:\n    images:\n      web: some-stale-pinned-tag\n"
    root = _solution(tmp_path, extra={"version.yaml": _version_doc(workspace="main", pins=pins)})
    context = open_solution(root)

    destination = scaffold_version(
        root, context.controller.index, context.controller.solution, "dev", workspace="main", from_name="prd"
    )

    data = yaml.safe_load(destination.read_text(encoding="utf-8"))
    # Seeded with 'web's REAL current image, not the source document's stale pin.
    assert data["spec"]["pins"]["images"]["web"] == "nginx:1.27-alpine"


def test_scaffold_from_drops_keys_with_no_current_value_to_seed(tmp_path):
    pins = "  pins:\n    images:\n      sidecar: anything\n"
    root = _solution(tmp_path, extra={"version.yaml": _version_doc(workspace="main", pins=pins)})
    context = open_solution(root)

    destination = scaffold_version(
        root, context.controller.index, context.controller.solution, "dev", workspace="main", from_name="prd"
    )

    data = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert "sidecar" not in data["spec"]["pins"].get("images", {})


def test_scaffold_produces_a_real_file_discoverable_via_path_get(tmp_path):
    """Round-trip through path_controller — the same resolution
    `version set` (Phase 4) will reuse internally."""
    root = _solution(tmp_path)
    context = open_solution(root)

    scaffold_version(
        root, context.controller.index, context.controller.solution, "dev", workspace="main", from_name=None
    )

    result = get_path(context.controller.index, PlatformKind.VERSION, "dev")
    # Freshly created — not indexed by this already-loaded context, but the
    # file itself must be on disk where get_path's own path convention expects it.
    assert result.exists is False  # not re-indexed without a fresh open_solution()
    assert (root / "versions" / "dev.yaml").is_file()


# ---------------------------------------------------------------------------
# set_version_pin
# ---------------------------------------------------------------------------

PIN_DOCUMENT = """apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: prd
spec:
  # Rationale lives in the schema, not in comments.
  pins:
    images:
      # Shorthand — nothing is being held back.
      caddy: caddy:2-alpine
      # Structured — deliberately behind upstream, and it says so.
      db:
        version: docker.io/library/postgres:16-alpine
        status: held
        available: 18.6-alpine
        reason: "postgres majors need pg_upgrade/dump-restore"
        reviewed: 2026-09-08
    charts:
      gatus:
        version: "1.0.0"
        status: unverified
        reason: "chart version could not be confirmed upstream"
        reviewed: 2026-09-08
"""


def _pin_file(tmp_path: Path) -> Path:
    path = tmp_path / "version.yaml"
    path.write_text(PIN_DOCUMENT, encoding="utf-8")
    return path


def test_set_shorthand_pin_changes_only_that_value(tmp_path):
    path = _pin_file(tmp_path)
    before = path.read_text(encoding="utf-8")

    updated = set_version_pin(path, "images", "caddy", "caddy:2.1-alpine", available=None, force=False)

    assert updated.spec.pins.images["caddy"].version == "caddy:2.1-alpine"
    after = path.read_text(encoding="utf-8")
    # Every other line is untouched, including comments.
    assert after == before.replace("caddy:2-alpine", "caddy:2.1-alpine")
    assert "# Shorthand" in after
    assert "# Rationale lives in the schema" in after


def test_set_refuses_held_pin_without_force(tmp_path):
    path = _pin_file(tmp_path)

    with pytest.raises(UsageError, match="held.*--force"):
        set_version_pin(path, "images", "db", "17.0-alpine", available=None, force=False)

    # Untouched on refusal.
    assert "docker.io/library/postgres:16-alpine" in path.read_text(encoding="utf-8")


def test_set_held_pin_with_force_changes_version_only(tmp_path):
    path = _pin_file(tmp_path)

    updated = set_version_pin(path, "images", "db", "17.0-alpine", available=None, force=True)

    pin = updated.spec.pins.images["db"]
    assert pin.version == "17.0-alpine"
    assert pin.status is VersionPinStatus.HELD
    assert pin.reason == "postgres majors need pg_upgrade/dump-restore"
    assert pin.reviewed is not None


def test_set_available_only_is_allowed_on_an_unverified_pin_without_force(tmp_path):
    path = _pin_file(tmp_path)

    updated = set_version_pin(path, "charts", "gatus", None, available="1.0.1", force=False)

    pin = updated.spec.pins.charts["gatus"]
    assert pin.available == "1.0.1"
    assert pin.version == "1.0.0"  # version itself untouched
    assert pin.status is VersionPinStatus.UNVERIFIED


def test_set_never_touches_reason_or_reviewed(tmp_path):
    path = _pin_file(tmp_path)

    updated = set_version_pin(path, "images", "db", "17.0-alpine", available="19.0-alpine", force=True)

    pin = updated.spec.pins.images["db"]
    assert pin.reason == "postgres majors need pg_upgrade/dump-restore"
    assert str(pin.reviewed) == "2026-09-08"


def test_set_creates_a_brand_new_pin(tmp_path):
    path = _pin_file(tmp_path)

    updated = set_version_pin(path, "images", "redis", "redis:7-alpine", available=None, force=False)

    pin = updated.spec.pins.images["redis"]
    assert pin.version == "redis:7-alpine"
    assert pin.status is VersionPinStatus.CURRENT


def test_set_rejects_creating_a_pin_with_only_available(tmp_path):
    path = _pin_file(tmp_path)

    with pytest.raises(UsageError, match="does not exist yet"):
        set_version_pin(path, "images", "redis", None, available="redis:7-alpine", force=False)


def test_set_rejects_an_unknown_category(tmp_path):
    path = _pin_file(tmp_path)

    with pytest.raises(UsageError, match="Unknown pin category"):
        set_version_pin(path, "tools", "redis", "1.0.0", available=None, force=False)


def test_set_rejects_nothing_to_set(tmp_path):
    path = _pin_file(tmp_path)

    with pytest.raises(UsageError, match="nothing to set"):
        set_version_pin(path, "images", "caddy", None, available=None, force=False)


def test_set_rejects_an_empty_value(tmp_path):
    path = _pin_file(tmp_path)

    with pytest.raises(UsageError, match="must not be empty"):
        set_version_pin(path, "images", "caddy", "  ", available=None, force=False)


def test_set_upgrades_shorthand_to_structured_when_available_given(tmp_path):
    path = _pin_file(tmp_path)

    updated = set_version_pin(path, "images", "caddy", None, available="2.2-alpine", force=False)

    pin = updated.spec.pins.images["caddy"]
    assert pin.version == "caddy:2-alpine"
    assert pin.available == "2.2-alpine"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["spec"]["pins"]["images"]["caddy"] == {"version": "caddy:2-alpine", "available": "2.2-alpine"}
