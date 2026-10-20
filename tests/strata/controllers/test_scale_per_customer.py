#!/usr/bin/env python3
"""Per-customer scale (docs/work/remotes-at-scale.md, C6): ~300 customers, each one asset, one tenant and one
deployment, plus one version document pinning every customer.

Built on a copy of the example solution under `config/`. Absolute timings depend on the machine and on how
fast the OS opens freshly written files, so nothing here asserts a wall-clock budget for loading. What is
asserted is behaviour that must stay true at size: the solution still validates, a build touches only its
own customer, and validation/graph cost grows about linearly with the customer count (a per-reference scan
of the whole index once made both quadratic).
"""

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from strata.controllers.build_controller import build_run
from strata.controllers.graph_controller import build_reference_graph
from strata.controllers.solution_context import SolutionContext, open_solution
from strata.models.common_models import PlatformKind

SMALL = 100
LARGE = 300

#: Linear growth from SMALL to LARGE is 3x and quadratic is 9x. Best-of-three timings measure 2-3x on linear code;
#: the per-reference index scan this guards against measured 6x for the graph.
MAX_GROWTH = 4.5


def _repo_config_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "config"


pytestmark = pytest.mark.skipif(not _repo_config_dir().exists(), reason="example config not present")


def _code(i: int) -> str:
    return f"c{1000 + i}"


def _generate_customers(root: Path, customers: int) -> None:
    for folder in ("customers", "assets", "deployments", "versions"):
        (root / folder).mkdir(exist_ok=True)
    pins = []
    for i in range(customers):
        code = _code(i)
        (root / "customers" / f"{code}.yaml").write_text(
            f"apiVersion: strata.huybrechts.xyz/v2\nkind: tenant\nmeta:\n  name: {code}\nspec:\n"
            "  display_name: Customer\n  geographies: [europe]\n",
            encoding="utf-8",
        )
        (root / "assets" / f"{code}-api.yaml").write_text(
            f"apiVersion: strata.huybrechts.xyz/v2\nkind: asset\nmeta:\n  name: {code}-api\nspec:\n"
            f"  location:\n    url: https://git.example.com/customers/{code}-api.git\n    reference: v1.0.0\n"
            "    integration: git-customers\n  source_path: openapi\n",
            encoding="utf-8",
        )
        (root / "deployments" / f"prd-{code}.yaml").write_text(
            f"apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: prd-{code}-deployment\nspec:\n"
            f"  extends: deploy-base\n  environments: [prd]\n  tenant: {code}\n  version: prd-customers\n"
            f"  assets:\n    - slot: api\n      asset: {code}-api\n",
            encoding="utf-8",
        )
        pins.append(f"      {code}-api: v1.1.0\n")
    (root / "versions" / "prd-customers.yaml").write_text(
        "apiVersion: strata.huybrechts.xyz/v2\nkind: version\nmeta:\n  name: prd-customers\nspec:\n"
        "  pins:\n    assets:\n" + "".join(pins),
        encoding="utf-8",
    )


@dataclass
class _Scaled:
    root: Path
    customers: int
    context: SolutionContext


def _build(tmp_path_factory: pytest.TempPathFactory, customers: int) -> _Scaled:
    root = tmp_path_factory.mktemp(f"scale-{customers}") / "sln"
    shutil.copytree(_repo_config_dir(), root, ignore=shutil.ignore_patterns(".strata", "build"))
    _generate_customers(root, customers)
    for path in root.rglob("*.yaml"):  # a first read of a new file can be far slower than loading it
        path.read_bytes()
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()[:5]
    return _Scaled(root=root, customers=customers, context=context)


@pytest.fixture(scope="module")
def small(tmp_path_factory) -> _Scaled:
    return _build(tmp_path_factory, SMALL)


@pytest.fixture(scope="module")
def large(tmp_path_factory) -> _Scaled:
    return _build(tmp_path_factory, LARGE)


def _best_of(runs: int, action: Callable[[], object]) -> float:
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        action()
        best = min(best, time.perf_counter() - started)
    return best


def test_a_solution_with_hundreds_of_customers_validates_clean(large: _Scaled):
    findings = large.context.resolve()

    assert [d.message for d in findings.errors] == []
    assert len(large.context.controller.index.all_of(PlatformKind.ASSET)) >= LARGE
    # Only the example's own stale pins: nothing about a generated customer.
    assert not [d for d in findings.warnings if "c1" in d.message]


def test_every_customer_deployment_reaches_its_own_asset_in_the_graph(large: _Scaled):
    edges = build_reference_graph(large.context.controller.index)

    to_assets = {(e.source_name, e.target_name) for e in edges if e.target_kind == "asset" and e.resolved}
    for i in range(LARGE):
        assert (f"prd-{_code(i)}-deployment", f"{_code(i)}-api") in to_assets
    assert all(e.resolved for e in edges if e.source_name.startswith("prd-c1"))


def test_a_build_touches_only_its_own_customers_asset(large: _Scaled, tmp_path: Path):
    steps: list[str] = []
    middle = _code(LARGE // 2)

    diagnostics = build_run(
        large.context, f"prd-{middle}-deployment", tmp_path / "build", dry_run=True, on_step=steps.append
    )

    assert diagnostics.ok
    text = "\n".join(steps)
    assert f"asset '{middle}-api'" in text
    assert not [i for i in range(LARGE) if _code(i) != middle and f"'{_code(i)}-api'" in text]


def test_validation_cost_grows_about_linearly_with_the_customer_count(small: _Scaled, large: _Scaled):
    def validate(scaled: _Scaled) -> Callable[[], object]:
        return lambda: SolutionContext(
            controller=scaled.context.controller, diagnostics=type(scaled.context.diagnostics)()
        ).resolve()

    growth = _best_of(3, validate(large)) / _best_of(3, validate(small))

    assert growth < MAX_GROWTH, f"{SMALL} -> {LARGE} customers made validation {growth:.1f}x slower (linear is 3x)"


def test_the_reference_graph_grows_about_linearly_with_the_customer_count(small: _Scaled, large: _Scaled):
    small_time = _best_of(5, lambda: build_reference_graph(small.context.controller.index))
    large_time = _best_of(5, lambda: build_reference_graph(large.context.controller.index))

    growth = large_time / small_time
    assert growth < MAX_GROWTH, f"{SMALL} -> {LARGE} customers made the graph {growth:.1f}x slower (linear is 3x)"
