#!/usr/bin/env python3
"""Tests for `build_promotion_view()`/`validate_promotions()` (docs/work/promotion.md Phase 2).

Follows `test_graph_controller.py`'s/`test_version_pins.py`'s pattern: real YAML
documents through `open_solution(...)`, then exercise either the builder
directly (shape assertions) or the full `.resolve()` pipeline (proving the
`strata validate` wiring end to end, not just that the function works in
isolation).
"""

from datetime import date
from pathlib import Path

import pytest
import yaml

from strata.controllers.promotion_controller import apply_promotion, build_promotion_view, build_status_rows
from strata.controllers.solution_context import open_solution
from strata.models.version_model import VersionModel
from strata.utils.errors import UsageError

MANIFEST = """apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: test-solution
spec: {}
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
  name: dspapi
spec:
  providers: [azure-main]
  provisioners:
    - name: tf
      tool: terraform
      source: {source_path: terraform/main}
"""


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _base(tmp_path: Path) -> Path:
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(root, "provider.yaml", PROVIDER)
    _write(root, "workspace.yaml", WORKSPACE)
    return root


def _version(name: str, *, workspace: str = "dspapi", promotion: str | None = None, pins: str = "  pins: {}\n") -> str:
    promotion_yaml = f"  promotion:\n{promotion}\n" if promotion else ""
    return f"""apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: {name}
spec:
  workspace: {workspace}
{promotion_yaml}{pins}"""


def _deployment(name: str, *, version: str, workspace: str = "dspapi") -> str:
    return f"""apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: {name}
spec:
  partial: true
  workspace: {workspace}
  version: {version}
"""


def _resolve(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()  # Phase 1 must be clean first
    context.resolve()
    return context


def _load_version(path: Path) -> VersionModel:
    return VersionModel.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Untagged documents opt out entirely
# ---------------------------------------------------------------------------


def test_untagged_version_document_is_excluded_from_the_view(tmp_path):
    """Worked example A: no `spec.promotion` at all — not an error, simply
    excluded from grouping (opting into rollout visibility is per-document)."""
    root = _base(tmp_path)
    _write(root, "version.yaml", _version("dspapi-prd"))
    _write(root, "deployment.yaml", _deployment("dspapi-prd-dep", version="dspapi-prd"))

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()

    view, diagnostics = build_promotion_view("dspapi", context.controller.index)
    assert view.rings == []
    assert len(diagnostics) == 0


def test_tagged_document_with_no_declared_workspace_is_excluded(tmp_path):
    """No `spec.workspace` means no workspace to group by — skip, don't guess."""
    root = _base(tmp_path)
    _write(
        root,
        "version.yaml",
        """apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: dspapi-prd
spec:
  promotion:
    ring: prd
    order: 3
  pins: {}
""",
    )

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()

    view, diagnostics = build_promotion_view("dspapi", context.controller.index)
    assert view.rings == []
    assert len(diagnostics) == 0


# ---------------------------------------------------------------------------
# Worked example B shape: multi-ring, wave divergence
# ---------------------------------------------------------------------------


def test_multi_ring_with_wave_divergence_groups_and_sorts_correctly(tmp_path):
    root = _base(tmp_path)
    _write(root, "version-dev.yaml", _version("dspapi-dev", promotion="    ring: dev\n    order: 1"))
    _write(root, "version-qas.yaml", _version("dspapi-qas", promotion="    ring: qas\n    order: 2"))
    _write(
        root,
        "version-prd-canary.yaml",
        _version("dspapi-prd-canary", promotion="    ring: prd\n    order: 3\n    wave: canary"),
    )
    _write(
        root,
        "version-prd-general.yaml",
        _version("dspapi-prd", promotion="    ring: prd\n    order: 3\n    wave: general"),
    )
    _write(root, "deployment-dev.yaml", _deployment("dev-dep", version="dspapi-dev"))
    _write(root, "deployment-qas.yaml", _deployment("qas-dep", version="dspapi-qas"))
    _write(root, "deployment-canary.yaml", _deployment("canary-dep", version="dspapi-prd-canary"))
    _write(root, "deployment-general.yaml", _deployment("general-dep", version="dspapi-prd"))

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()

    view, diagnostics = build_promotion_view("dspapi", context.controller.index)
    assert len(diagnostics) == 0
    assert [r.order for r in view.rings] == [1, 2, 3]
    assert [r.ring for r in view.rings] == ["dev", "qas", "prd"]

    dev_ring, qas_ring, prd_ring = view.rings
    assert len(dev_ring.waves) == 1
    assert dev_ring.waves[0].wave is None
    assert dev_ring.waves[0].version.meta.name == "dspapi-dev"

    assert len(qas_ring.waves) == 1
    assert qas_ring.waves[0].wave is None

    assert len(prd_ring.waves) == 2
    assert [w.wave for w in prd_ring.waves] == ["canary", "general"]
    assert prd_ring.waves[0].version.meta.name == "dspapi-prd-canary"
    assert prd_ring.waves[1].version.meta.name == "dspapi-prd"


# ---------------------------------------------------------------------------
# Phase 2 check 1: ring <-> order must biject
# ---------------------------------------------------------------------------


def test_same_ring_tagged_with_two_different_orders_is_caught(tmp_path):
    root = _base(tmp_path)
    _write(root, "version-a.yaml", _version("dspapi-a", promotion="    ring: prd\n    order: 3"))
    _write(root, "version-b.yaml", _version("dspapi-b", promotion="    ring: prd\n    order: 4"))
    _write(root, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(root, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "promotion_ring_order_not_bijective" for d in context.diagnostics.errors)


def test_same_order_tagged_with_two_different_rings_is_caught(tmp_path):
    """The one that actually catches real typos — 'prd'/'prod' both claiming order 3."""
    root = _base(tmp_path)
    _write(root, "version-a.yaml", _version("dspapi-a", promotion="    ring: prd\n    order: 3"))
    _write(root, "version-b.yaml", _version("dspapi-b", promotion="    ring: prod\n    order: 3"))
    _write(root, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(root, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "promotion_ring_order_not_bijective" for d in context.diagnostics.errors)
    assert any("prd" in d.message and "prod" in d.message for d in context.diagnostics.errors)


# ---------------------------------------------------------------------------
# Phase 2 check 2: (ring, order, wave) must be unique
# ---------------------------------------------------------------------------


def test_duplicate_waveless_slot_is_caught(tmp_path):
    root = _base(tmp_path)
    _write(root, "version-a.yaml", _version("dspapi-a", promotion="    ring: prd\n    order: 3"))
    _write(root, "version-b.yaml", _version("dspapi-b", promotion="    ring: prd\n    order: 3"))
    _write(root, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(root, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "promotion_slot_duplicate" for d in context.diagnostics.errors)


def test_duplicate_waved_slot_is_caught(tmp_path):
    root = _base(tmp_path)
    _write(root, "version-a.yaml", _version("dspapi-a", promotion="    ring: prd\n    order: 3\n    wave: canary"))
    _write(root, "version-b.yaml", _version("dspapi-b", promotion="    ring: prd\n    order: 3\n    wave: canary"))
    _write(root, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(root, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "promotion_slot_duplicate" for d in context.diagnostics.errors)


# ---------------------------------------------------------------------------
# Phase 2 check 3: no mixing a waveless document with waved ones
# ---------------------------------------------------------------------------


def test_mixing_waveless_and_waved_at_the_same_slot_is_caught(tmp_path):
    root = _base(tmp_path)
    _write(root, "version-a.yaml", _version("dspapi-a", promotion="    ring: prd\n    order: 3"))
    _write(root, "version-b.yaml", _version("dspapi-b", promotion="    ring: prd\n    order: 3\n    wave: canary"))
    _write(root, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(root, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))

    context = _resolve(root)
    assert not context.ok
    assert any(d.code == "promotion_mixed_waveless" for d in context.diagnostics.errors)


# ---------------------------------------------------------------------------
# Orphan ring/wave: a warning, not an error
# ---------------------------------------------------------------------------


def test_orphan_ring_with_no_referencing_deployment_is_a_warning(tmp_path):
    root = _base(tmp_path)
    _write(root, "version.yaml", _version("dspapi-prd", promotion="    ring: prd\n    order: 3"))
    # No deployment references 'dspapi-prd' at all.

    context = _resolve(root)
    assert context.ok  # a warning alone must not fail validate
    warning = context.diagnostics.warnings[0]
    assert warning.code == "promotion_orphan_ring"
    assert "dspapi-prd" in warning.message


def test_referenced_ring_produces_no_orphan_warning(tmp_path):
    root = _base(tmp_path)
    _write(root, "version.yaml", _version("dspapi-prd", promotion="    ring: prd\n    order: 3"))
    _write(root, "deployment.yaml", _deployment("dep", version="dspapi-prd"))

    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()
    assert not any(d.code == "promotion_orphan_ring" for d in context.diagnostics.warnings)


# ---------------------------------------------------------------------------
# build_status_rows() (docs/work/promotion.md Phase 3)
# ---------------------------------------------------------------------------


def test_status_rows_empty_wave_still_produces_one_placeholder_row(tmp_path):
    root = _base(tmp_path)
    _write(root, "version.yaml", _version("dspapi-prd", promotion="    ring: prd\n    order: 3"))
    _write(root, "deployment.yaml", _deployment("dep", version="dspapi-prd"))

    context = _resolve(root)
    view, _diagnostics = build_promotion_view("dspapi", context.controller.index)
    rows = build_status_rows(view)

    assert len(rows) == 1
    row = rows[0]
    assert row.ring == "prd" and row.order == 3 and row.wave is None
    assert row.category is None and row.target is None and row.value is None and row.status is None
    assert row.behind is False


def test_status_rows_one_row_per_pin_on_a_multi_pin_document(tmp_path):
    """Real evidence (version_model.py's own docstring): a production
    document pins many targets at once — a row is per-pin, not per-document."""
    root = _base(tmp_path)
    _write(
        root,
        "version.yaml",
        _version(
            "dspapi-prd",
            promotion="    ring: prd\n    order: 3",
            pins='  pins:\n    images:\n      dspapi: "1.0.0"\n    charts:\n      dspapi: "2.0.0"\n',
        ),
    )
    _write(root, "deployment.yaml", _deployment("dep", version="dspapi-prd"))

    context = _resolve(root)
    view, _diagnostics = build_promotion_view("dspapi", context.controller.index)
    rows = build_status_rows(view)

    assert len(rows) == 2
    targets = {(r.category, r.target, r.value) for r in rows}
    assert targets == {("images", "dspapi", "1.0.0"), ("charts", "dspapi", "2.0.0")}
    assert all(r.status == "current" for r in rows)
    assert all(r.behind is False for r in rows)


def test_status_rows_marks_the_lower_semver_sibling_as_behind(tmp_path):
    root = _base(tmp_path)
    _write(
        root,
        "version-canary.yaml",
        _version(
            "dspapi-canary",
            promotion="    ring: prd\n    order: 3\n    wave: canary",
            pins='  pins:\n    images:\n      dspapi: "1.1.0"\n',
        ),
    )
    _write(
        root,
        "version-general.yaml",
        _version(
            "dspapi-general",
            promotion="    ring: prd\n    order: 3\n    wave: general",
            pins='  pins:\n    images:\n      dspapi: "1.0.0"\n',
        ),
    )
    _write(root, "deployment-canary.yaml", _deployment("canary-dep", version="dspapi-canary"))
    _write(root, "deployment-general.yaml", _deployment("general-dep", version="dspapi-general"))

    context = _resolve(root)
    view, _diagnostics = build_promotion_view("dspapi", context.controller.index)
    rows = build_status_rows(view)

    canary_row = next(r for r in rows if r.wave == "canary")
    general_row = next(r for r in rows if r.wave == "general")
    assert canary_row.behind is False
    assert general_row.behind is True


def test_status_rows_does_not_guess_behind_for_non_semver_values(tmp_path):
    """A git ref/commit-ish pin value cannot be safely ordered — skip the
    marker entirely rather than falling back to a string comparison."""
    root = _base(tmp_path)
    _write(
        root,
        "version-canary.yaml",
        _version(
            "dspapi-canary",
            promotion="    ring: prd\n    order: 3\n    wave: canary",
            pins='  pins:\n    remotes:\n      infra: "feature-branch"\n',
        ),
    )
    _write(
        root,
        "version-general.yaml",
        _version(
            "dspapi-general",
            promotion="    ring: prd\n    order: 3\n    wave: general",
            pins='  pins:\n    remotes:\n      infra: "main"\n',
        ),
    )
    _write(root, "deployment-canary.yaml", _deployment("canary-dep", version="dspapi-canary"))
    _write(root, "deployment-general.yaml", _deployment("general-dep", version="dspapi-general"))

    context = _resolve(root)
    view, _diagnostics = build_promotion_view("dspapi", context.controller.index)
    rows = build_status_rows(view)

    assert all(r.behind is False for r in rows)


def test_status_rows_single_wave_never_marked_behind(tmp_path):
    """Nothing to be behind *of* with only one occupant at a slot."""
    root = _base(tmp_path)
    _write(
        root,
        "version.yaml",
        _version(
            "dspapi-prd", promotion="    ring: prd\n    order: 3", pins='  pins:\n    images:\n      dspapi: "1.0.0"\n'
        ),
    )
    _write(root, "deployment.yaml", _deployment("dep", version="dspapi-prd"))

    context = _resolve(root)
    view, _diagnostics = build_promotion_view("dspapi", context.controller.index)
    rows = build_status_rows(view)

    assert len(rows) == 1
    assert rows[0].behind is False


# ---------------------------------------------------------------------------
# apply_promotion() (docs/work/promotion.md Phase 5)
# ---------------------------------------------------------------------------


def _worked_example_b_for_apply(root: Path, *, general_pins: str) -> None:
    """dev(order 1, waveless) -> qas(order 2, waveless) -> prd(order 3, split
    into canary/general) — qas and canary share `images.dspapi`; `general`'s
    own pins are supplied by the caller so a test can set up either a
    held/unverified source pin (to prove the reset) or a non-overlapping
    key (to prove "common keys only")."""
    _write(root, "version-dev.yaml", _version("dspapi-dev", promotion="    ring: dev\n    order: 1"))
    _write(
        root,
        "version-qas.yaml",
        _version(
            "dspapi-qas", promotion="    ring: qas\n    order: 2", pins='  pins:\n    images:\n      dspapi: "1.2.0"\n'
        ),
    )
    _write(
        root,
        "version-prd-canary.yaml",
        _version(
            "dspapi-prd-canary",
            promotion="    ring: prd\n    order: 3\n    wave: canary",
            pins='  pins:\n    images:\n      dspapi: "1.1.0"\n',
        ),
    )
    _write(
        root,
        "version-prd-general.yaml",
        _version("dspapi-prd", promotion="    ring: prd\n    order: 3\n    wave: general", pins=general_pins),
    )
    _write(root, "deployment-dev.yaml", _deployment("dev-dep", version="dspapi-dev"))
    _write(root, "deployment-qas.yaml", _deployment("qas-dep", version="dspapi-qas"))
    _write(root, "deployment-canary.yaml", _deployment("canary-dep", version="dspapi-prd-canary"))
    _write(root, "deployment-general.yaml", _deployment("general-dep", version="dspapi-prd"))


def test_apply_copies_common_keys_and_resets_status_and_reviewed(tmp_path):
    """Source is the preceding *order* (qas) — not the sibling `canary` wave
    at the same order. Waves have no ordering field of their own (only
    `(ring, order)` is sortable; `wave` is a disambiguating label, "no
    inference from name or file order" per this doc's own Phase 1 rule),
    so a same-order sibling could never be chosen as "the" source in
    general once a ring is split into more than two waves — the preceding
    distinct order is the only generically well-defined source."""
    root = _base(tmp_path)
    _worked_example_b_for_apply(
        root,
        general_pins=(
            '  pins:\n    images:\n      dspapi:\n        version: "1.0.0"\n        status: held\n'
            '        reason: "waiting on canary bake time"\n        reviewed: 2026-01-01\n'
        ),
    )
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()

    result = apply_promotion("dspapi", "prd", context.controller.index, wave="general")

    assert result.source_version == "dspapi-qas"
    assert result.target_version == "dspapi-prd"
    assert result.copied == [("images", "dspapi", "1.2.0")]
    assert result.ring == "prd" and result.order == 3 and result.wave == "general"

    written = _load_version(result.path)
    pin = written.spec.pins.images["dspapi"]
    assert pin.version == "1.2.0"
    assert pin.status.value == "current"
    assert pin.reviewed == date.today()
    assert pin.reason is None


def test_apply_only_copies_keys_common_to_both_documents(tmp_path):
    """A key only the source declares is never created on the target as a
    side effect — that's `version update`'s job, not apply's."""
    root = _base(tmp_path)
    _worked_example_b_for_apply(root, general_pins='  pins:\n    charts:\n      unrelated: "5.0.0"\n')
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()

    result = apply_promotion("dspapi", "prd", context.controller.index, wave="general")

    assert result.copied == []  # no common key between canary (images.dspapi) and general (charts.unrelated)
    written = _load_version(result.path)
    assert "dspapi" not in (written.spec.pins.images or {})
    assert written.spec.pins.charts["unrelated"].version == "5.0.0"  # untouched


def test_apply_requires_wave_when_target_ring_has_multiple_waves(tmp_path):
    root = _base(tmp_path)
    _worked_example_b_for_apply(root, general_pins='  pins:\n    images:\n      dspapi: "1.0.0"\n')
    context = _resolve(root)

    with pytest.raises(UsageError, match="more than one wave"):
        apply_promotion("dspapi", "prd", context.controller.index)


def test_apply_rejects_an_unknown_wave_for_the_target(tmp_path):
    root = _base(tmp_path)
    _worked_example_b_for_apply(root, general_pins='  pins:\n    images:\n      dspapi: "1.0.0"\n')
    context = _resolve(root)

    with pytest.raises(UsageError, match="no wave named 'ghost'"):
        apply_promotion("dspapi", "prd", context.controller.index, wave="ghost")


def test_apply_rejects_a_wave_for_a_single_wave_target_ring(tmp_path):
    """`_select_target_wave()`'s other strict branch: a ring with exactly
    one occupant still rejects a `--wave` that doesn't match it — no
    silent "closest match" guessing."""
    root = _base(tmp_path)
    _worked_example_b_for_apply(root, general_pins='  pins:\n    images:\n      dspapi: "1.0.0"\n')
    context = _resolve(root)

    with pytest.raises(UsageError, match="no wave named 'ghost'"):
        apply_promotion("dspapi", "qas", context.controller.index, wave="ghost")


def test_apply_from_a_waveless_source_ignores_the_targets_wave_name(tmp_path):
    """qas (the preceding order) is waveless — it is still the correct
    source for prd's split canary/general waves (worked example B's own
    shape); no wave-name match is required from a single-occupant ring."""
    root = _base(tmp_path)
    _worked_example_b_for_apply(root, general_pins='  pins:\n    images:\n      dspapi: "1.0.0"\n')
    context = _resolve(root)

    result = apply_promotion("dspapi", "qas", context.controller.index)  # qas is waveless; no --wave needed

    assert result.source_version == "dspapi-dev"
    assert result.wave is None


def test_apply_raises_when_the_ring_has_no_preceding_order(tmp_path):
    root = _base(tmp_path)
    _worked_example_b_for_apply(root, general_pins='  pins:\n    images:\n      dspapi: "1.0.0"\n')
    context = _resolve(root)

    with pytest.raises(UsageError, match="no preceding order"):
        apply_promotion("dspapi", "dev", context.controller.index)


def test_apply_raises_for_an_unknown_ring(tmp_path):
    root = _base(tmp_path)
    _worked_example_b_for_apply(root, general_pins='  pins:\n    images:\n      dspapi: "1.0.0"\n')
    context = _resolve(root)

    with pytest.raises(UsageError, match="No ring named 'ghost'"):
        apply_promotion("dspapi", "ghost", context.controller.index)


def test_apply_refuses_when_the_workspace_has_an_unresolved_phase2_error(tmp_path):
    """A real file mutation needs a higher bar than `promote status`'s own
    read-only "display it anyway" behaviour — refuses outright rather than
    silently picking the first-seen occupant of an ambiguous slot."""
    root = _base(tmp_path)
    _write(root, "version-a.yaml", _version("dspapi-a", promotion="    ring: prd\n    order: 3"))
    _write(root, "version-b.yaml", _version("dspapi-b", promotion="    ring: prod\n    order: 3"))
    _write(root, "deployment-a.yaml", _deployment("dep-a", version="dspapi-a"))
    _write(root, "deployment-b.yaml", _deployment("dep-b", version="dspapi-b"))
    context = _resolve(root)
    assert not context.ok  # the Phase 2 bijection error is real

    with pytest.raises(UsageError, match="unresolved promotion inconsistencies"):
        apply_promotion("dspapi", "prd", context.controller.index)


def test_apply_raises_when_no_tagged_documents_exist_at_all(tmp_path):
    root = _base(tmp_path)
    _write(root, "version.yaml", _version("dspapi-prd"))  # untagged
    context = _resolve(root)

    with pytest.raises(UsageError, match="No ring-tagged version documents"):
        apply_promotion("dspapi", "prd", context.controller.index)


def test_apply_raises_when_the_source_ring_is_split_and_none_of_its_waves_match(tmp_path):
    """`_select_source_wave()`'s own strict branch: a single-occupant source
    is lenient (worked example B's qas->canary/general), but a *split*
    source ring has no principled way to pick an occupant unless one of
    its own waves happens to share the target's wave name."""
    root = _base(tmp_path)
    _write(root, "version-dev-x.yaml", _version("dspapi-dev-x", promotion="    ring: dev\n    order: 1\n    wave: x"))
    _write(root, "version-dev-y.yaml", _version("dspapi-dev-y", promotion="    ring: dev\n    order: 1\n    wave: y"))
    _write(root, "version-qas.yaml", _version("dspapi-qas", promotion="    ring: qas\n    order: 2\n    wave: z"))
    _write(root, "deployment-dev-x.yaml", _deployment("dev-x-dep", version="dspapi-dev-x"))
    _write(root, "deployment-dev-y.yaml", _deployment("dev-y-dep", version="dspapi-dev-y"))
    _write(root, "deployment-qas.yaml", _deployment("qas-dep", version="dspapi-qas"))
    context = _resolve(root)
    assert context.ok, context.diagnostics.messages()

    with pytest.raises(UsageError, match="Preceding ring 'dev'.*none is named 'z'"):
        apply_promotion("dspapi", "qas", context.controller.index, wave="z")
