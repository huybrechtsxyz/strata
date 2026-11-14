#!/usr/bin/env python3
"""Tests for version composition: a deployment lists several version documents (docs/work/version-composition.md).

The fixture is the design's Lifecycle: two products, DataHub and Staging, each in its own workspace, sharing a
`redis` image pinned once in an untagged `prd-core`, and each with its own tagged product document. The
deployments get `prd-core` from a partial base and add their own document.
"""

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from strata.commands.cli import cli
from strata.controllers import pinnable_targets as pinnable_targets_module
from strata.controllers.build_controller import build_run, write_effective_version
from strata.controllers.deployment_resolution import resolve_deployment_chains
from strata.controllers.pinnable_targets import TargetCache, pinnable_targets
from strata.controllers.promotion_controller import apply_promotion, build_promotion_view, build_status_rows
from strata.controllers.solution_context import SolutionContext, open_solution
from strata.controllers.value_controller import resolve_deployment, resolve_version
from strata.controllers.version_composition import VersionMatrix, compose_versions
from strata.models.version_model import ComposedVersionModel, pin_owner, version_names
from strata.utils.errors import UsageError

_HEADER = "apiVersion: strata.huybrechts.xyz/v2\nkind: {kind}\nmeta:\n  name: {name}\nspec:\n"


def _write(root: Path, relative: str, kind: str, name: str, spec: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER.format(kind=kind, name=name) + spec, encoding="utf-8")


def _version(root: Path, name: str, pins: dict, *, ring: str | None = None) -> None:
    spec: dict = {"pins": pins}
    if ring is not None:
        spec["promotion"] = {"ring": ring, "order": {"qas": 2, "prd": 3}[ring]}
    text = yaml.safe_dump(spec, sort_keys=False)
    _write(root, f"versions/{name}.yaml", "version", name, "".join(f"  {line}\n" for line in text.splitlines()))


def _deployment(root: Path, name: str, spec: str) -> None:
    _write(root, f"deployments/{name}.yaml", "deployment", name, spec)


def _solution(tmp_path: Path) -> Path:
    root = tmp_path / "sln"
    root.mkdir()
    (root / "strata.yaml").write_text(_HEADER.format(kind="solution", name="s") + "  {}\n", encoding="utf-8")
    (root / "infra").mkdir()
    (root / "infra" / "main.tf").write_text("# root module\n", encoding="utf-8")
    _write(root, "provider.yaml", "provider", "p1", "  type: local\n  region: local\n")
    _write(root, "environment.yaml", "environment", "prd", "  {}\n")
    _write(
        root,
        "resource.yaml",
        "resource",
        "r1",
        "  provider_type: local\n  resource_type: server\n  category: compute\n",
    )
    for product in ("datahub", "staging"):
        (root / "services" / product).mkdir(parents=True)
        (root / "services" / product / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        _write(
            root,
            f"{product}/workspace.yaml",
            "workspace",
            product,
            f"  providers:\n    - p1\n  namespaces:\n    - {product}-apps\n"
            "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n"
            "  execution:\n    - name: infra\n      provisioner: tf\n      resources:\n        - r1\n"
            "  resources:\n    - name: r1\n      resource: r1\n",
        )
        _write(
            root,
            f"{product}/module.yaml",
            "module",
            f"{product}-stack",
            f"  source:\n    source_path: services/{product}\n  type: compose\n"
            f"  services:\n    - name: {product}-api\n      image: acme/{product}:1.0.0\n"
            "    - name: redis\n      image: redis:7\n",
        )
        _write(
            root,
            f"{product}/namespace.yaml",
            "namespace",
            f"{product}-apps",
            f"  modules:\n    - name: {product}-stack\n      module: {product}-stack\n",
        )
    _version(root, "prd-core", {"images": {"redis": "redis:7.2"}})
    _version(root, "prd-datahub", {"images": {"datahub-api": "acme/datahub:1.4.0"}}, ring="prd")
    _version(root, "prd-staging", {"images": {"staging-api": "acme/staging:1.2.0"}}, ring="prd")
    _deployment(root, "base-prd", "  partial: true\n  version: prd-core\n  environments:\n    - prd\n")
    _deployment(root, "datahub-prd", "  extends: base-prd\n  workspace: datahub\n  version: prd-datahub\n")
    _deployment(root, "staging-prd", "  extends: base-prd\n  workspace: staging\n  version: [prd-staging]\n")
    return root


def _resolve(root: Path) -> SolutionContext:
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    context.resolve()
    return context


def _codes(context: SolutionContext) -> list[str]:
    return [d.code for d in context.diagnostics.items if d.code and d.code.startswith("version_")]


# ---------------------------------------------------------------------------
# The shared solution validates, and each deployment gets the union
# ---------------------------------------------------------------------------


def test_a_shared_document_and_a_product_document_validate(tmp_path: Path):
    context = _resolve(_solution(tmp_path))

    assert context.ok, context.diagnostics.messages()
    assert _codes(context) == []


def test_extends_appends_the_version_lists_base_first(tmp_path: Path):
    context = _resolve(_solution(tmp_path))

    deployment = resolve_deployment(context, "datahub-prd")

    assert deployment.spec.version == ["prd-core", "prd-datahub"]


def test_the_composed_version_holds_the_union_and_each_pins_owner(tmp_path: Path):
    context = _resolve(_solution(tmp_path))

    version = resolve_version(context, resolve_deployment(context, "datahub-prd"))

    assert isinstance(version, ComposedVersionModel)
    assert version.meta.name == "datahub-prd"
    assert version_names(version) == ["prd-core", "prd-datahub"]
    assert set((version.spec.pins.images or {}).keys()) == {"redis", "datahub-api"}
    assert pin_owner(version, "images", "redis") == "prd-core"
    assert pin_owner(version, "images", "datahub-api") == "prd-datahub"
    assert version.spec.promotion is not None and version.spec.promotion.ring == "prd"


def test_one_listed_document_is_returned_unchanged(tmp_path: Path):
    """R9: a single document keeps its own name, so logs and outputs are as before."""
    context = _resolve(_solution(tmp_path))

    version, found = compose_versions(context.controller.index, ["prd-core"], label="x")

    assert version is not None and not isinstance(version, ComposedVersionModel)
    assert version.meta.name == "prd-core"
    assert found.items == []


# ---------------------------------------------------------------------------
# The matrix checks
# ---------------------------------------------------------------------------


def test_two_listed_documents_pinning_one_target_is_a_conflict_even_with_equal_values(tmp_path: Path):
    root = _solution(tmp_path)
    _version(root, "prd-datahub", {"images": {"datahub-api": "acme/datahub:1.4.0", "redis": "redis:7.2"}}, ring="prd")

    context = _resolve(root)

    assert not context.ok
    [error] = [d for d in context.diagnostics.errors if d.code == "version_pin_conflict"]
    assert "images/redis is pinned by both 'prd-core' and 'prd-datahub' (the same value)" in error.message
    assert error.source is not None and error.source.endswith("datahub-prd.yaml")
    assert error.hint is not None and error.hint.startswith("Remove images/redis from one of the two documents")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (
            lambda root: _version(root, "prd-datahub", {"images": {"redis": "redis:7.2"}}, ring="prd"),
            "version_pin_conflict",
        ),
        (
            lambda root: _deployment(
                root,
                "datahub-prd",
                "  extends: base-prd\n  workspace: datahub\n  version: [prd-datahub, prd-datahub]\n",
            ),
            "version_listed_twice",
        ),
        (
            lambda root: _deployment(
                root, "datahub-prd", "  extends: base-prd\n  workspace: datahub\n  version: prd-staging\n"
            ),
            "version_document_unused",
        ),
        (
            lambda root: _version(
                root, "prd-datahub", {"images": {"datahub-api": "a/b:1.0", "x": "a/x:1.0"}}, ring="prd"
            ),
            "version_pin_unused",
        ),
        (
            lambda root: _deployment(
                root, "datahub-lab", "  workspace: datahub\n  version: prd-core\n  environments: [prd]\n"
            ),
            "version_ring_none",
        ),
    ],
)
def test_every_version_finding_says_what_to_do(tmp_path: Path, change, code):
    """docs/work/diagnostic-guidance.md: each new finding carries a `hint`, separate from its message."""
    root = _solution(tmp_path)
    change(root)

    [finding] = [d for d in _resolve(root).diagnostics.items if d.code == code]

    assert finding.hint, finding


def test_a_name_listed_twice_in_one_list_is_a_warning(tmp_path: Path):
    root = _solution(tmp_path)
    _deployment(
        root, "datahub-prd", "  extends: base-prd\n  workspace: datahub\n  version: [prd-datahub, prd-datahub]\n"
    )

    context = _resolve(root)

    assert context.ok
    assert _codes(context) == ["version_listed_twice"]


def test_repeating_a_base_document_is_not_a_finding(tmp_path: Path):
    """V2: a deployment listing a document its base already lists is de-duplicated silently."""
    root = _solution(tmp_path)
    _deployment(root, "datahub-prd", "  extends: base-prd\n  workspace: datahub\n  version: [prd-core, prd-datahub]\n")

    assert _codes(_resolve(root)) == []


def test_tagged_documents_of_different_rings_are_a_ring_conflict(tmp_path: Path):
    root = _solution(tmp_path)
    _version(root, "qas-extra", {}, ring="qas")
    _deployment(root, "datahub-prd", "  extends: base-prd\n  workspace: datahub\n  version: [prd-datahub, qas-extra]\n")

    context = _resolve(root)

    [error] = [d for d in context.diagnostics.errors if d.code == "version_ring_conflict"]
    assert "'prd-datahub' (ring prd, order 3) and 'qas-extra' (ring qas, order 2)" in error.message


def test_listing_another_products_document_is_document_unused(tmp_path: Path):
    """V5: the check that replaces `Version.spec.workspace`'s limit."""
    root = _solution(tmp_path)
    _deployment(root, "datahub-prd", "  extends: base-prd\n  workspace: datahub\n  version: prd-staging\n")

    context = _resolve(root)

    [warning] = [d for d in context.diagnostics.warnings if d.code == "version_document_unused"]
    assert "'datahub-prd' lists version document 'prd-staging', which pins nothing" in warning.message


def test_an_inherited_document_is_not_judged_for_document_unused(tmp_path: Path):
    """A base giving every product `prd-core` must not warn for a product that uses nothing in it."""
    root = _solution(tmp_path)
    _version(root, "prd-core", {"images": {"staging-api": "acme/staging:9.9.9"}})
    _version(root, "prd-staging", {}, ring="prd")

    assert "version_document_unused" not in _codes(_resolve(root))


def test_a_pin_no_listing_deployment_uses_is_information(tmp_path: Path):
    root = _solution(tmp_path)
    _version(
        root, "prd-datahub", {"images": {"datahub-api": "acme/datahub:1.4.0", "staging-api": "x/y:1.0"}}, ring="prd"
    )

    context = _resolve(root)

    [info] = [d for d in context.diagnostics.infos if d.code == "version_pin_unused"]
    assert "Version 'prd-datahub': images/staging-api is pinned" in info.message
    assert context.ok


def test_a_version_document_no_deployment_lists_is_information(tmp_path: Path):
    """Untagged and listed by nobody: maybe used through --pin in CI, which strata cannot see."""
    root = _solution(tmp_path)
    _version(root, "spare", {})

    context = _resolve(root)

    [info] = [d for d in context.diagnostics.infos if d.code == "version_document_unlisted"]
    assert info.message == "Version 'spare' is listed by no deployment."
    assert info.hint is not None and "--pin spare" in info.hint
    assert context.ok


def test_a_deployment_outside_its_workspaces_rollout_is_information(tmp_path: Path):
    root = _solution(tmp_path)
    _deployment(root, "datahub-lab", "  workspace: datahub\n  version: prd-core\n  environments:\n    - prd\n")

    context = _resolve(root)

    [info] = [d for d in context.diagnostics.infos if d.code == "version_ring_none"]
    assert "Deployment 'datahub-lab' is in no ring" in info.message


def test_a_shared_key_at_two_values_in_one_ring_is_drift(tmp_path: Path):
    root = _solution(tmp_path)
    _version(root, "prd-core-old", {"images": {"redis": "redis:6"}})
    _deployment(
        root, "staging-prd", "  workspace: staging\n  version: [prd-core-old, prd-staging]\n  environments: [prd]\n"
    )

    context = _resolve(root)

    [warning] = [d for d in context.diagnostics.warnings if d.code == "version_ring_drift"]
    assert "Ring 'prd': the shared pin images/redis" in warning.message
    assert "'redis:6' (prd-core-old); 'redis:7.2' (prd-core)" in warning.message


def test_a_shared_document_with_only_an_order_still_counts_for_drift(tmp_path: Path):
    """Tagged means a ring (V9): `promotion: {order: 1}` without a ring does not make a shared document a product one."""
    root = _solution(tmp_path)
    _write(
        root,
        "versions/prd-core-old.yaml",
        "version",
        "prd-core-old",
        "  promotion:\n    order: 1\n  pins:\n    images:\n      redis: redis:6\n",
    )
    _deployment(
        root, "staging-prd", "  workspace: staging\n  version: [prd-core-old, prd-staging]\n  environments: [prd]\n"
    )

    assert "version_ring_drift" in _codes(_resolve(root))


def test_version_show_with_an_order_but_no_ring(tmp_path: Path):
    """`order` may be set without `ring`; the console line must not assume both."""
    root = _solution(tmp_path)
    _write(root, "versions/odd.yaml", "version", "odd", "  promotion:\n    order: 3\n  pins: {}\n")

    result = CliRunner().invoke(cli, ["version", "show", "odd", "--path", str(root)])

    assert result.exit_code == 0, result.output
    assert "odd  (ring -, order 3)" in result.output


def test_a_build_without_a_version_removes_an_earlier_version_file(tmp_path: Path):
    """A build with `clean=False` must not leave a stale `version.yaml` for `deploy run` to judge against."""
    build_path = tmp_path / "build"
    build_path.mkdir()
    (build_path / "version.yaml").write_text("stale\n", encoding="utf-8")

    assert write_effective_version(build_path, "app", None, None) is None
    assert not (build_path / "version.yaml").exists()


def test_a_customer_document_differing_from_its_product_document_is_not_drift(tmp_path: Path):
    """V12: only untagged (shared) owners are judged; a customer document is the point of R6."""
    root = _solution(tmp_path)
    _version(root, "prd-datahub-fabrikam", {"images": {"datahub-api": "acme/datahub:1.4.2"}})
    _deployment(
        root,
        "fabrikam-prd",
        "  extends: base-prd\n  workspace: datahub\n  version: [prd-datahub-fabrikam, prd-datahub]\n",
    )
    _version(root, "prd-datahub", {}, ring="prd")

    assert "version_ring_drift" not in _codes(_resolve(root))


# ---------------------------------------------------------------------------
# Promotion from the deployments' matrices (V10, V11)
# ---------------------------------------------------------------------------


def _rollout(tmp_path: Path) -> Path:
    """The solution plus a `qas` ring ahead of `prd`, with newer values."""
    root = _solution(tmp_path)
    _version(root, "qas-core", {"images": {"redis": "redis:8.0"}})
    _version(root, "qas-datahub", {"images": {"datahub-api": "acme/datahub:1.5.0"}}, ring="qas")
    _version(root, "qas-staging", {"images": {"staging-api": "acme/staging:1.3.0"}}, ring="qas")
    _deployment(root, "base-qas", "  partial: true\n  version: qas-core\n  environments:\n    - prd\n")
    _deployment(root, "datahub-qas", "  extends: base-qas\n  workspace: datahub\n  version: qas-datahub\n")
    _deployment(root, "staging-qas", "  extends: base-qas\n  workspace: staging\n  version: qas-staging\n")
    return root


def _pins(root: Path, name: str) -> dict:
    return yaml.safe_load((root / "versions" / f"{name}.yaml").read_text(encoding="utf-8"))["spec"]["pins"]


def test_promote_status_rows_come_from_the_deployments_with_their_owner(tmp_path: Path):
    context = _resolve(_rollout(tmp_path))

    view, found = build_promotion_view("datahub", context.controller.index)
    rows = {(row.ring, f"{row.category}/{row.target}"): row for row in build_status_rows(view)}

    assert found.items == []
    assert [ring.ring for ring in view.rings] == ["qas", "prd"]
    assert (rows[("prd", "images/redis")].owner, rows[("prd", "images/redis")].shared) == ("prd-core", True)
    assert (rows[("prd", "images/datahub-api")].owner, rows[("prd", "images/datahub-api")].shared) == (
        "prd-datahub",
        False,
    )
    assert rows[("qas", "images/datahub-api")].value == "acme/datahub:1.5.0"
    assert ("prd", "images/staging-api") not in rows


def test_promote_status_without_a_workspace_shows_a_whole_ring(tmp_path: Path):
    """`promote status --ring prd`: every workspace in the ring, one table, a workspace per row."""
    root = _rollout(tmp_path)

    data = _cli("promote", "status", "--ring", "prd", "--path", str(root))

    assert data["workspace"] is None
    assert {row["ring"] for row in data["rows"]} == {"prd"}
    assert {(row["workspace"], row["target"], row["owner"]) for row in data["rows"]} == {
        ("datahub", "datahub-api", "prd-datahub"),
        ("datahub", "redis", "prd-core"),
        ("staging", "staging-api", "prd-staging"),
        ("staging", "redis", "prd-core"),
    }


def test_promote_status_console_has_a_workspace_column_without_a_workspace(tmp_path: Path):
    root = _rollout(tmp_path)

    result = CliRunner().invoke(cli, ["promote", "status", "--ring", "qas", "--path", str(root)])

    assert result.exit_code == 0, result.output
    header = next(line for line in result.output.splitlines() if line.strip().startswith("ring"))
    assert header.split()[:3] == ["ring", "order", "workspace"]


def test_promote_apply_writes_the_owner_and_skips_a_shared_key(tmp_path: Path):
    root = _rollout(tmp_path)
    context = _resolve(root)

    result = apply_promotion("datahub", "prd", context.controller.index)

    assert result.written == [("images", "datahub-api", "acme/datahub:1.5.0", "prd-datahub")]
    assert result.skipped == [("images/redis", "prd-core", "shared")]
    assert _pins(root, "prd-datahub")["images"]["datahub-api"]["version"] == "acme/datahub:1.5.0"
    assert _pins(root, "prd-core")["images"]["redis"] == "redis:7.2"


def test_promote_apply_shared_also_moves_the_shared_document(tmp_path: Path):
    root = _rollout(tmp_path)
    context = _resolve(root)

    result = apply_promotion("datahub", "prd", context.controller.index, shared=True)

    assert ("images", "redis", "redis:8.0", "prd-core") in result.written
    assert result.skipped == []
    assert _pins(root, "prd-core")["images"]["redis"]["version"] == "redis:8.0"


def test_promote_apply_never_writes_a_customers_own_document(tmp_path: Path):
    root = _rollout(tmp_path)
    _version(root, "prd-fabrikam", {"images": {"datahub-api": "acme/datahub:1.4.2"}})
    _version(root, "prd-datahub", {}, ring="prd")
    _deployment(
        root, "datahub-prd", "  extends: base-prd\n  workspace: datahub\n  version: [prd-datahub, prd-fabrikam]\n"
    )
    _deployment(root, "datahub-prd-2", "  extends: base-prd\n  workspace: datahub\n  version: prd-datahub\n")
    context = _resolve(root)

    result = apply_promotion("datahub", "prd", context.controller.index)

    assert ("images/datahub-api", "prd-fabrikam", "own document") in result.skipped
    assert _pins(root, "prd-fabrikam")["images"]["datahub-api"] == "acme/datahub:1.4.2"


def test_promote_apply_reports_a_key_the_source_runs_only_from_customers_own_documents(tmp_path: Path):
    """Two qas deployments run datahub-api from their own documents at different values: there is no one value to
    promote, so the key is skipped and reported, and the rest of the promotion still goes ahead."""
    root = _rollout(tmp_path)
    _version(root, "qas-datahub", {}, ring="qas")
    _version(root, "qas-a", {"images": {"datahub-api": "acme/datahub:1.5.0"}})
    _version(root, "qas-b", {"images": {"datahub-api": "acme/datahub:1.6.0"}})
    _deployment(root, "datahub-qas", "  extends: base-qas\n  workspace: datahub\n  version: [qas-datahub, qas-a]\n")
    _deployment(root, "datahub-qas-2", "  extends: base-qas\n  workspace: datahub\n  version: [qas-datahub, qas-b]\n")
    context = _resolve(root)

    result = apply_promotion("datahub", "prd", context.controller.index, shared=True)

    assert ("images/datahub-api", "prd-datahub", "source own documents") in result.skipped
    assert ("images", "redis", "redis:8.0", "prd-core") in result.written
    assert _pins(root, "prd-datahub")["images"]["datahub-api"] == "acme/datahub:1.4.0"


def test_promote_apply_writes_every_owner_of_a_key_not_only_the_first(tmp_path: Path):
    """Two prd deployments get datahub-api from two customers' own documents: both are reported, none dropped."""
    root = _rollout(tmp_path)
    _version(root, "prd-datahub", {}, ring="prd")
    _version(root, "prd-cust-1", {"images": {"datahub-api": "acme/datahub:1.4.1"}})
    _version(root, "prd-cust-2", {"images": {"datahub-api": "acme/datahub:1.4.2"}})
    _deployment(
        root, "datahub-prd", "  extends: base-prd\n  workspace: datahub\n  version: [prd-datahub, prd-cust-1]\n"
    )
    _deployment(
        root, "datahub-prd-2", "  extends: base-prd\n  workspace: datahub\n  version: [prd-datahub, prd-cust-2]\n"
    )
    context = _resolve(root)

    result = apply_promotion("datahub", "prd", context.controller.index)

    owners = {owner for target, owner, reason in result.skipped if target == "images/datahub-api"}
    assert owners == {"prd-cust-1", "prd-cust-2"}


# ---------------------------------------------------------------------------
# Tooling: version update and version show (V13)
# ---------------------------------------------------------------------------


def _cli(*args: str) -> dict:
    result = CliRunner().invoke(cli, [*args, "--output", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"]


def test_version_update_offers_only_what_the_listing_deployments_use_and_no_one_else_pins(tmp_path: Path):
    root = _solution(tmp_path)
    _version(root, "prd-datahub", {}, ring="prd")

    data = _cli("version", "update", "prd-datahub", "--path", str(root))

    # redis is pinned by prd-core, staging-api is another workspace's: only datahub-api is offered.
    assert [(row["category"], row["name"]) for row in data["candidates"]] == [("images", "datahub-api")]


def test_version_update_on_an_unlisted_document_offers_the_whole_solution(tmp_path: Path):
    root = _solution(tmp_path)
    _version(root, "spare", {})

    data = _cli("version", "update", "spare", "--path", str(root))

    assert {row["name"] for row in data["candidates"]} == {"datahub-api", "staging-api", "redis"}


def test_version_show_lists_the_pins_and_the_deployments_listing_the_document(tmp_path: Path):
    root = _solution(tmp_path)

    data = _cli("version", "show", "prd-core", "--path", str(root))

    assert data["pins"] == [{"category": "images", "target": "redis", "version": "redis:7.2", "status": "current"}]
    assert data["deployments"] == ["base-prd", "datahub-prd", "staging-prd"]
    assert data["promotion"] is None
    assert _cli("version", "show", "prd-datahub", "--path", str(root))["promotion"] == {"ring": "prd", "order": 3}


# ---------------------------------------------------------------------------
# pinnable_targets()
# ---------------------------------------------------------------------------


def test_pinnable_targets_are_the_reached_modules_services(tmp_path: Path):
    context = _resolve(_solution(tmp_path))

    targets = pinnable_targets(context.controller.index, resolve_deployment(context, "datahub-prd"))

    assert targets == {("images", "datahub-api"), ("images", "redis")}


def test_pinnable_targets_cover_charts_remotes_and_artifacts(tmp_path: Path):
    root = _solution(tmp_path)
    _write(
        root,
        "datahub/chart.yaml",
        "module",
        "datahub-chart",
        "  type: helm\n  source:\n    chart_name: datahub\n    chart_repository: oci://charts.example.com\n"
        "    chart_version: 1.0.0\n"
        "  configuration:\n    image:\n      tag: ${artifact:datahub-image.image_tag}\n",
    )
    _write(root, "datahub/artifact.yaml", "artifact", "datahub-image", "  image_name: acme/datahub\n")
    _write(root, "datahub/other.yaml", "artifact", "datahub-tools", "  image_name: acme/tools\n")
    _write(
        root,
        "datahub/namespace.yaml",
        "namespace",
        "datahub-apps",
        "  modules:\n    - name: datahub-stack\n      module: datahub-stack\n"
        "    - name: datahub-chart\n      module: datahub-chart\n",
    )
    _write(
        root,
        "environment.yaml",
        "environment",
        "prd",
        "  variables:\n    - key: TOOLS\n      store: artifact\n      value: datahub-tools\n      field: image_tag\n",
    )
    context = _resolve(root)

    targets = pinnable_targets(context.controller.index, resolve_deployment(context, "datahub-prd"))

    assert {("charts", "datahub-chart"), ("artifacts", "datahub-image"), ("artifacts", "datahub-tools")} <= targets


def _with_asset(root: Path, asset_spec: str) -> None:
    """Give the datahub workspace an asset slot `api`, selected by its execution step."""
    _write(root, "datahub/asset.yaml", "asset", "api-files", asset_spec)
    _write(
        root,
        "datahub/workspace.yaml",
        "workspace",
        "datahub",
        "  providers:\n    - p1\n  namespaces:\n    - datahub-apps\n"
        "  assets:\n    - slot: api\n      asset: api-files\n"
        "  provisioners:\n    - name: tf\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n    - name: infra\n      provisioner: tf\n      resources:\n        - r1\n      assets: [api]\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )


def test_pinnable_targets_cover_a_remote_reached_only_through_an_asset(tmp_path: Path):
    root = _solution(tmp_path)
    (root / "strata.yaml").write_text(
        _HEADER.format(kind="solution", name="s")
        + "  remotes:\n    - name: specs\n      type: git\n      url: https://example.com/specs.git\n"
        "      reference: main\n      fetch: strata\n",
        encoding="utf-8",
    )
    _with_asset(root, "  source_path: openapi\n  remote: specs\n")
    context = _resolve(root)

    targets = pinnable_targets(context.controller.index, resolve_deployment(context, "datahub-prd"))

    assert ("remotes", "specs") in targets
    assert ("assets", "api-files") not in targets  # pinned through `remotes`, not `assets`


def test_pinnable_targets_cover_an_asset_with_its_own_location(tmp_path: Path):
    root = _solution(tmp_path)
    _with_asset(
        root,
        "  source_path: openapi\n  location:\n    type: git\n    url: https://example.com/api.git\n"
        "    reference: main\n    fetch: strata\n",
    )
    context = _resolve(root)

    assert ("assets", "api-files") in pinnable_targets(
        context.controller.index, resolve_deployment(context, "datahub-prd")
    )


def test_a_template_deployment_can_read_its_version_list_from_a_parameter_set(tmp_path: Path):
    """`spec.version` filled from `${param:}`: the leaf lists its own document, and the base's is appended."""
    root = _solution(tmp_path)
    _write(root, "params.yaml", "parameterset", "datahub-params", "  values:\n    version: prd-datahub\n")
    _write(
        root,
        "deployments/datahub-prd.yaml",
        "deployment",
        "datahub-prd",
        "  extends: base-prd\n  workspace: datahub\n  version: ${param:p.version}\n",
    )
    path = root / "deployments" / "datahub-prd.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "  name: datahub-prd\n", "  name: datahub-prd\n  params:\n    p: datahub-params\n"
        ),
        encoding="utf-8",
    )
    context = _resolve(root)

    assert context.ok, context.diagnostics.messages()
    assert resolve_deployment(context, "datahub-prd").spec.version == ["prd-core", "prd-datahub"]
    assert _codes(context) == []


# ---------------------------------------------------------------------------
# Cost: shared documents scanned once, each deployment walked once per run (finding 9)
# ---------------------------------------------------------------------------


def _more_datahub_deployments(root: Path, count: int) -> None:
    for i in range(count):
        _deployment(root, f"datahub-prd-{i}", "  extends: base-prd\n  workspace: datahub\n  version: prd-datahub\n")


def test_a_shared_document_is_scanned_once_however_many_deployments_reach_it(tmp_path: Path):
    """Only each deployment's own document is scanned per deployment; the workspace, namespace, module, provider and
    environment they share are scanned once (`TargetCache`)."""
    few, many = tmp_path / "few", tmp_path / "many"
    few.mkdir()
    many.mkdir()
    counts = []
    for base, extra in ((few, 0), (many, 20)):
        root = _solution(base)
        _more_datahub_deployments(root, extra)
        context = _resolve(root)
        cache = TargetCache()
        VersionMatrix.build(context.controller.index, resolve_deployment_chains(context.controller.index)[0], cache)
        counts.append(cache.computed)

    assert counts[1] - counts[0] == 20  # one more scan per added deployment: its own document, nothing shared


def test_validate_walks_each_deployment_once_with_a_rollout(tmp_path: Path, monkeypatch):
    """With rings, the matrix checks and the promotion checks share one `VersionMatrix`: one walk per deployment."""
    root = _rollout(tmp_path)
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    # Counted inside `pinnable_targets()` itself, so a caller importing it directly cannot slip past.
    calls: list[str] = []
    original = pinnable_targets_module.documents_reached_by_deployment

    def counting(index, deployment, source, *, enabled_only=False):
        calls.append(deployment.meta.name)
        return original(index, deployment, source, enabled_only=enabled_only)

    monkeypatch.setattr(pinnable_targets_module, "documents_reached_by_deployment", counting)

    context.resolve()

    assert sorted(calls) == ["datahub-prd", "datahub-qas", "staging-prd", "staging-qas"]


# ---------------------------------------------------------------------------
# --pin as a list
# ---------------------------------------------------------------------------


def test_pin_replaces_the_whole_list(tmp_path: Path):
    context = _resolve(_solution(tmp_path))

    deployment = resolve_deployment(context, "datahub-prd", version_pin=("prd-core", "prd-staging"))

    assert deployment.spec.version == ["prd-core", "prd-staging"]


def test_a_single_pin_stays_a_plain_name(tmp_path: Path):
    context = _resolve(_solution(tmp_path))

    assert resolve_deployment(context, "datahub-prd", version_pin=("prd-core",)).spec.version == "prd-core"


def test_pin_documents_that_conflict_stop_the_run(tmp_path: Path):
    root = _solution(tmp_path)
    _version(root, "prd-core-2", {"images": {"redis": "redis:8"}})
    context = _resolve(root)

    with pytest.raises(UsageError, match="--pin: .*images/redis is pinned by both 'prd-core' and 'prd-core-2'"):
        resolve_deployment(context, "datahub-prd", version_pin=("prd-core", "prd-core-2"))


def test_an_unknown_pin_document_is_named(tmp_path: Path):
    context = _resolve(_solution(tmp_path))

    with pytest.raises(UsageError, match="--pin names an unknown version document: 'nope'"):
        resolve_deployment(context, "datahub-prd", version_pin=("prd-core", "nope"))


# ---------------------------------------------------------------------------
# The build's version.yaml and resolved.yaml
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_the_build_writes_the_pins_it_used_and_where_they_came_from(tmp_path: Path):
    root = _solution(tmp_path)
    _version(
        root, "prd-datahub", {"images": {"datahub-api": "acme/datahub:1.4.0", "staging-api": "x/y:1.0"}}, ring="prd"
    )
    context = _resolve(root)
    build_path = tmp_path / "build"

    diagnostics = build_run(context, "datahub-prd", build_path)

    assert diagnostics.ok, diagnostics.messages()
    version = yaml.safe_load((build_path / "version.yaml").read_text(encoding="utf-8"))
    assert version["kind"] == "version"
    assert version["meta"] == {"name": "datahub-prd"}
    assert "promotion" not in version["spec"]
    assert version["spec"]["pins"] == {
        "images": {"datahub-api": {"version": "acme/datahub:1.4.0"}, "redis": {"version": "redis:7.2"}}
    }
    resolved = yaml.safe_load((build_path / "resolved.yaml").read_text(encoding="utf-8"))
    assert resolved["version"] == ["prd-core", "prd-datahub"]
    assert resolved["pinned_by"] == "spec.version"
    assert resolved["pin_sources"] == {"images/datahub-api": "prd-datahub", "images/redis": "prd-core"}
    assert resolved["promotion"] == {
        "workspace": "datahub",
        "ring": "prd",
        "order": 3,
        "version": ["prd-core", "prd-datahub"],
    }


@pytest.mark.slow
def test_a_pin_list_that_leaves_a_document_out_warns(tmp_path: Path):
    context = _resolve(_solution(tmp_path))

    diagnostics = build_run(context, "datahub-prd", tmp_path / "build", pin=("prd-datahub",))

    [warning] = [d for d in diagnostics.warnings if d.code == "version_pin_dropped"]
    assert warning.message.startswith("images/redis is 'redis:7.2' under the deployment's own versions (prd-core)")
    resolved = yaml.safe_load((tmp_path / "build" / "resolved.yaml").read_text(encoding="utf-8"))
    assert (resolved["version"], resolved["pinned_by"]) == ("prd-datahub", "--pin")
