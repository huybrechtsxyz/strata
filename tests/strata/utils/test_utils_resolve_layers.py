"""Tests for resolve_layers() — ADR-0072 layer/segment resolution.

Focus: the three LayerResolution states (resolved / pass-through / failed) and the
detection that stops a misconfigured convention from silently producing an empty
artifact path.
"""

from strata.models.configuration_model import ConfigurationLayerModel, PathConventionModel
from strata.models.deployment_model import LayersModel
from strata.utils.path_convention import resolve_layers

REL = "zones/europe/customers/acme/deploy.yaml"


def _conv(name="zone-tenant", pattern="zones/{zone}/customers/{customer}", resolves="layers", segments=None):
    if segments is None:
        segments = [ConfigurationLayerModel(name="zone"), ConfigurationLayerModel(name="customer")]
    return PathConventionModel(
        name=name,
        scope="zones/**",
        pattern=pattern,
        resolves=resolves,
        segments=segments if resolves == "layers" else None,
    )


DECLARED = LayersModel(segments={"zone": "europe", "customer": "acme"})


class TestLevel1ConventionSelection:
    def test_auto_detects_matching_convention(self):
        r = resolve_layers(REL, LayersModel(), [_conv()])
        assert r.convention is not None
        assert r.convention.name == "zone-tenant"
        assert r.error is None

    def test_explicit_follows_wins(self):
        r = resolve_layers(REL, LayersModel(follows="zone-tenant"), [_conv()])
        assert r.convention.name == "zone-tenant"
        assert r.error is None

    def test_unknown_follows_name_is_an_error(self):
        r = resolve_layers(REL, LayersModel(follows="nope"), [_conv()])
        assert r.convention is None
        assert r.error is not None and "nope" in r.error

    def test_ambiguous_match_is_an_error_naming_both(self):
        a = _conv(name="family-a")
        b = _conv(name="family-b")
        r = resolve_layers(REL, LayersModel(), [a, b])
        assert r.convention is None
        assert r.error is not None
        assert "family-a" in r.error and "family-b" in r.error


class TestLevel2SegmentValues:
    def test_derives_values_from_path(self):
        r = resolve_layers(REL, LayersModel(), [_conv()])
        assert r.values == {"zone": "europe", "customer": "acme"}

    def test_explicit_value_beats_derived(self):
        r = resolve_layers(REL, LayersModel(segments={"zone": "override"}), [_conv()])
        assert r.values["zone"] == "override"
        assert r.values["customer"] == "acme"  # still derived

    def test_default_used_when_neither_explicit_nor_derivable(self):
        conv = _conv(
            pattern="zones/{zone}",
            segments=[
                ConfigurationLayerModel(name="zone"),
                ConfigurationLayerModel(name="ring", default="dev"),
            ],
        )
        r = resolve_layers("zones/europe/deploy.yaml", LayersModel(), [conv])
        assert r.values == {"zone": "europe", "ring": "dev"}

    def test_unresolvable_segment_is_omitted_not_an_error(self):
        conv = _conv(
            pattern="zones/{zone}",
            segments=[ConfigurationLayerModel(name="zone"), ConfigurationLayerModel(name="ring")],
        )
        r = resolve_layers("zones/europe/deploy.yaml", LayersModel(), [conv])
        assert r.values == {"zone": "europe"}
        assert r.error is None  # "not applicable", not a failure


class TestSilentNoOpDetection:
    """A convention whose pattern stopped matching must not fail silently.

    Regression guard: the declared values still pass through, so nothing *looks*
    wrong — but with no convention there is no segment order, so the artifact path
    silently becomes empty and the build lands in the wrong place.
    """

    def test_declared_layers_claimed_by_no_convention_is_an_error(self):
        broken = _conv(pattern="zoneZZ/{zone}/customers/{customer}")  # typo
        r = resolve_layers(REL, DECLARED, [broken])
        assert r.convention is None
        assert r.error is not None
        assert "no resolves: layers convention claims it" in r.error
        assert "zone-tenant" in r.error  # names what was tried
        # values still pass through so callers can degrade gracefully
        assert r.values == {"zone": "europe", "customer": "acme"}

    def test_deployment_outside_every_family_scope_is_an_error(self):
        r = resolve_layers("other/x.yaml", DECLARED, [_conv()])
        assert r.error is not None

    def test_no_layers_declared_stays_silent(self):
        """Nothing claims to be in a hierarchy — nothing to contradict."""
        broken = _conv(pattern="zoneZZ/{zone}")
        assert resolve_layers(REL, LayersModel(), [broken]).error is None
        assert resolve_layers(REL, None, [broken]).error is None

    def test_no_layers_convention_declared_stays_silent(self):
        """Workspace simply isn't using layering — must not start erroring."""
        layout_only = _conv(resolves=None)
        r = resolve_layers(REL, DECLARED, [layout_only])
        assert r.error is None
        assert resolve_layers(REL, DECLARED, []).error is None

    def test_pass_through_preserves_values_for_templates(self):
        """Sync templates read `layers.environment` — blanking it would silently
        retarget GitOps resources to the fallback namespace."""
        r = resolve_layers(REL, LayersModel(segments={"environment": "prd"}), [])
        assert r.convention is None and r.error is None
        assert r.values == {"environment": "prd"}
