"""Tests for PathConventionPolicy and path_convention utility functions.

Covers:
- match_pattern: capture, literal mismatch, shallow path, no captures
- evaluate_file_rule: file exists, file missing, placeholder expansion
- evaluate_conventions: scope miss, pattern miss, spec rule pass/fail,
  file rule pass/fail, multiple conventions
- PathConventionPolicy.evaluate: skip (no file_path), skip (no conventions),
  inline convention, spec.paths source, convention filter, enforce deny/warn
- PathConventionModel validation: key not in pattern → ValueError

Note: kind=yaml (JMESPath) rule resolution itself is unit-tested in
test_models_expression.py (ExpressionModel.query()) — this file only tests the
dispatch/integration around it (evaluate_conventions(), PathConventionPolicy).
"""

from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

from strata.models.configuration_model import (
    ConfigurationMetaModel,
    ConfigurationModel,
    ConfigurationSpecModel,
    ConfigurationZoneModel,
    PathConventionModel,
)
from strata.models.expression_model import ExpressionKind, ExpressionModel
from strata.models.policy_model import PolicyModel
from strata.utils.path_convention import evaluate_conventions, evaluate_file_rule, match_pattern
from strata.validators.policies.base_policy import PolicyContext
from strata.validators.policies.path_convention_policy import PathConventionPolicy

# ===========================================================================
# Fixtures & helpers
# ===========================================================================


def _yaml_rule(expression: str) -> ExpressionModel:
    return ExpressionModel(kind=ExpressionKind.YAML, expression=expression)


def _path_rule(expression: str) -> ExpressionModel:
    return ExpressionModel(kind=ExpressionKind.PATH, expression=expression)


def _make_policy(enforcement: str = "deny", configuration: Optional[dict] = None) -> "PolicyModel":
    return PolicyModel(
        name="test_path_convention",
        type="path_convention",
        phase="validate",
        enforcement=enforcement,
        configuration=configuration,
    )


def _make_convention(
    name: str = "zone-tree",
    scope: str = "zones/**",
    pattern: str = "zones/{zone}/customers/{tenant}/{env}",
    validate: Optional[dict] = None,
) -> "PathConventionModel":
    return PathConventionModel(name=name, scope=scope, pattern=pattern, rules=validate)


def _make_context(
    work_path: Optional[Path] = None,
    file_path: Optional[Path] = None,
    config_model=None,
) -> "PolicyContext":
    cfg_svc = None
    if config_model is not None:
        cfg_svc = MagicMock()
        cfg_svc.model = config_model
    return PolicyContext(
        phase="validate",
        work_path=work_path,
        configuration_service=cfg_svc,
        file_path=file_path,
    )


def _make_config_model(zones=None, paths=None) -> ConfigurationModel:
    """Return a real, minimal ConfigurationModel with spec.zones (and optionally spec.paths).

    Must be a real model (not a mock) — kind=yaml rules run JMESPath against
    ``model_dump()``, which needs a genuine Pydantic instance to produce a real dict.
    """
    return ConfigurationModel(
        meta=ConfigurationMetaModel(name="test-config"),
        spec=ConfigurationSpecModel(zones=zones or [], paths=paths),
    )


def _zone(name: str) -> ConfigurationZoneModel:
    # Region must be unique per zone within one ConfigurationSpecModel — derive
    # a distinct region from the zone name so multiple zones can coexist.
    return ConfigurationZoneModel(name=name, regions=[f"region-{name}"])


# ===========================================================================
# match_pattern
# ===========================================================================


class TestMatchPattern:
    def test_all_captures(self):
        result = match_pattern(
            "zones/europe/customers/contoso/prd/deploy.yaml",
            "zones/{zone}/customers/{tenant}/{env}",
        )
        assert result == {"zone": "europe", "tenant": "contoso", "env": "prd"}

    def test_trailing_parts_ignored(self):
        """Pattern matches even if file has more parts after the pattern end."""
        result = match_pattern(
            "customers/contoso/subdir/tenant.yaml",
            "customers/{tenant}",
        )
        assert result == {"tenant": "contoso"}

    def test_literal_mismatch_returns_none(self):
        result = match_pattern(
            "zones/europe/shared/base.yaml",
            "zones/{zone}/customers/{tenant}/{env}",
        )
        assert result is None

    def test_path_shallower_than_pattern_returns_none(self):
        result = match_pattern("zones/europe", "zones/{zone}/customers/{tenant}/{env}")
        assert result is None

    def test_no_captures_all_literals(self):
        result = match_pattern("customers/contoso/tenant.yaml", "customers/contoso")
        assert result == {}

    def test_single_segment_file(self):
        result = match_pattern("shared.yaml", "{name}")
        assert result == {"name": "shared.yaml"}

    def test_exact_depth_match(self):
        result = match_pattern("landscape/platform/landscape.yaml", "landscape/{landscape}")
        assert result == {"landscape": "platform"}

    def test_leading_slash_stripped(self):
        """Patterns with extra leading separators should still match."""
        result = match_pattern("zones/eu/customers/acme/dev", "zones/{zone}/customers/{tenant}/{env}")
        assert result == {"zone": "eu", "tenant": "acme", "env": "dev"}


# ===========================================================================
# evaluate_file_rule
# ===========================================================================


class TestEvaluateFileRule:
    def test_file_exists(self, tmp_path: Path):
        (tmp_path / "customers" / "contoso").mkdir(parents=True)
        (tmp_path / "customers" / "contoso" / "tenant.yaml").write_text("apiVersion: v1")
        result = evaluate_file_rule(
            "customers/{tenant}/tenant.yaml",
            {"tenant": "contoso"},
            tmp_path,
        )
        assert result is None  # no violation

    def test_file_missing(self, tmp_path: Path):
        result = evaluate_file_rule(
            "customers/{tenant}/tenant.yaml",
            {"tenant": "unknown-co"},
            tmp_path,
        )
        assert result is not None
        assert "unknown-co" in result

    def test_placeholder_expansion(self, tmp_path: Path):
        (tmp_path / "landscape" / "platform").mkdir(parents=True)
        (tmp_path / "landscape" / "platform" / "landscape.yaml").write_text("")
        result = evaluate_file_rule(
            "landscape/{landscape}/landscape.yaml",
            {"landscape": "platform"},
            tmp_path,
        )
        assert result is None

    def test_no_placeholders(self, tmp_path: Path):
        (tmp_path / "fixed" / "file.yaml").parent.mkdir(parents=True)
        (tmp_path / "fixed" / "file.yaml").write_text("")
        result = evaluate_file_rule("fixed/file.yaml", {}, tmp_path)
        assert result is None

    def test_missing_no_placeholder(self, tmp_path: Path):
        result = evaluate_file_rule("fixed/missing.yaml", {}, tmp_path)
        assert result is not None


# ===========================================================================
# evaluate_conventions
# ===========================================================================


class TestEvaluateConventions:
    def test_scope_miss_no_violations(self, tmp_path: Path):
        conv = _make_convention(scope="landscape/**")
        violations = evaluate_conventions("zones/europe/deploy.yaml", [conv], tmp_path)
        assert violations == []

    def test_pattern_miss_no_violations(self, tmp_path: Path):
        """File in scope but shallower than pattern → skipped."""
        conv = _make_convention(scope="zones/**", pattern="zones/{zone}/customers/{tenant}/{env}")
        violations = evaluate_conventions("zones/europe/deploy.yaml", [conv], tmp_path)
        assert violations == []

    def test_no_validate_rules_no_violations(self, tmp_path: Path):
        conv = _make_convention(scope="zones/**", pattern="zones/{zone}", validate=None)
        violations = evaluate_conventions("zones/europe/deploy.yaml", [conv], tmp_path)
        assert violations == []

    def test_spec_rule_pass(self, tmp_path: Path):
        conv = _make_convention(
            scope="zones/**",
            pattern="zones/{zone}",
            validate={"zone": _yaml_rule("spec.zones[*].name")},
        )
        model = _make_config_model(zones=[_zone("europe"), _zone("us-east")])
        violations = evaluate_conventions("zones/europe/deploy.yaml", [conv], tmp_path, model)
        assert violations == []

    def test_spec_rule_fail(self, tmp_path: Path):
        conv = _make_convention(
            scope="zones/**",
            pattern="zones/{zone}",
            validate={"zone": _yaml_rule("spec.zones[*].name")},
        )
        model = _make_config_model(zones=[_zone("europe"), _zone("us-east")])
        violations = evaluate_conventions("zones/atlantis/deploy.yaml", [conv], tmp_path, model)
        assert len(violations) == 1
        assert "atlantis" in violations[0]
        assert "zone-tree" in violations[0]

    def test_spec_rule_no_config_model_skipped(self, tmp_path: Path):
        """Spec rules without a config model are skipped gracefully."""
        conv = _make_convention(
            scope="zones/**",
            pattern="zones/{zone}",
            validate={"zone": _yaml_rule("spec.zones[*].name")},
        )
        violations = evaluate_conventions("zones/atlantis/deploy.yaml", [conv], tmp_path, None)
        assert violations == []

    def test_file_rule_pass(self, tmp_path: Path):
        (tmp_path / "customers" / "contoso").mkdir(parents=True)
        (tmp_path / "customers" / "contoso" / "tenant.yaml").write_text("")
        conv = _make_convention(
            scope="zones/**",
            pattern="zones/{zone}/customers/{tenant}",
            validate={"tenant": _path_rule("customers/{tenant}/tenant.yaml")},
        )
        violations = evaluate_conventions("zones/europe/customers/contoso/deploy.yaml", [conv], tmp_path)
        assert violations == []

    def test_file_rule_fail(self, tmp_path: Path):
        conv = _make_convention(
            scope="zones/**",
            pattern="zones/{zone}/customers/{tenant}",
            validate={"tenant": _path_rule("customers/{tenant}/tenant.yaml")},
        )
        violations = evaluate_conventions("zones/europe/customers/unknown-co/deploy.yaml", [conv], tmp_path)
        assert len(violations) == 1
        assert "unknown-co" in violations[0]

    def test_multiple_conventions_evaluated_independently(self, tmp_path: Path):
        (tmp_path / "customers" / "contoso").mkdir(parents=True)
        (tmp_path / "customers" / "contoso" / "tenant.yaml").write_text("")

        conv_a = _make_convention(
            name="zones",
            scope="zones/**",
            pattern="zones/{zone}",
            validate={"zone": _yaml_rule("spec.zones[*].name")},
        )
        conv_b = _make_convention(
            name="customers",
            scope="zones/**",  # same scope, different pattern
            pattern="zones/{zone}/customers/{tenant}",
            validate={"tenant": _path_rule("customers/{tenant}/tenant.yaml")},
        )
        model = _make_config_model(zones=[_zone("europe")])
        # zones/atlantis fails zone check; tenant passes file existence
        violations = evaluate_conventions(
            "zones/atlantis/customers/contoso/deploy.yaml", [conv_a, conv_b], tmp_path, model
        )
        assert len(violations) == 1
        assert "atlantis" in violations[0]

    def test_multiple_violations_same_convention(self, tmp_path: Path):
        """Two segments in the same convention both fail independently — produces
        2 violations. Both validated against spec.zones (a real field); which real
        field is used doesn't matter for this test, only that both are wrong."""
        conv = _make_convention(
            scope="zones/**",
            pattern="zones/{zone}/customers/{tenant}/{env}",
            validate={
                "zone": _yaml_rule("spec.zones[*].name"),
                "env": _yaml_rule("spec.zones[*].name"),
            },
        )
        model = _make_config_model(zones=[_zone("europe")])
        violations = evaluate_conventions(
            "zones/atlantis/customers/contoso/staging/deploy.yaml",
            [conv],
            tmp_path,
            model,
        )
        assert len(violations) == 2  # both zone and env fail


# ===========================================================================
# PathConventionPolicy.evaluate
# ===========================================================================


class TestPathConventionPolicy:
    def test_skip_no_file_path(self, tmp_path: Path):
        policy = PathConventionPolicy(_make_policy())
        context = _make_context(work_path=tmp_path, file_path=None)
        result = policy.evaluate(context)
        assert result.passed
        assert "skipped" in (result.details or {})

    def test_skip_no_work_path(self, tmp_path: Path):
        policy = PathConventionPolicy(_make_policy())
        context = _make_context(work_path=None, file_path=tmp_path / "zones/eu/deploy.yaml")
        result = policy.evaluate(context)
        assert result.passed

    def test_skip_no_conventions(self, tmp_path: Path):
        policy = PathConventionPolicy(_make_policy())
        context = _make_context(work_path=tmp_path, file_path=tmp_path / "zones/eu/deploy.yaml")
        result = policy.evaluate(context)
        assert result.passed
        assert "skipped" in (result.details or {})

    def test_inline_convention_pass(self, tmp_path: Path):
        (tmp_path / "landscape" / "platform").mkdir(parents=True)
        (tmp_path / "landscape" / "platform" / "landscape.yaml").write_text("")
        policy = PathConventionPolicy(
            _make_policy(
                configuration={
                    "scope": "deploy/**",
                    "pattern": "deploy/{landscape}/{ring}",
                    "validate": {"landscape": {"kind": "path", "expression": "landscape/{landscape}/landscape.yaml"}},
                }
            )
        )
        context = _make_context(
            work_path=tmp_path,
            file_path=tmp_path / "deploy" / "platform" / "1" / "deploy.yaml",
        )
        result = policy.evaluate(context)
        assert result.passed
        assert result.violations == []

    def test_inline_convention_fail(self, tmp_path: Path):
        policy = PathConventionPolicy(
            _make_policy(
                configuration={
                    "scope": "deploy/**",
                    "pattern": "deploy/{landscape}/{ring}",
                    "validate": {"landscape": {"kind": "path", "expression": "landscape/{landscape}/landscape.yaml"}},
                }
            )
        )
        context = _make_context(
            work_path=tmp_path,
            file_path=tmp_path / "deploy" / "ghost" / "1" / "deploy.yaml",
        )
        result = policy.evaluate(context)
        assert not result.passed
        assert len(result.violations) == 1
        assert "ghost" in result.violations[0]

    def test_spec_paths_source(self, tmp_path: Path):
        model = _make_config_model(zones=[_zone("europe")])
        conv = _make_convention(
            scope="zones/**",
            pattern="zones/{zone}",
            validate={"zone": _yaml_rule("spec.zones[*].name")},
        )
        model.spec.paths = [conv]

        policy = PathConventionPolicy(_make_policy())
        context = _make_context(
            work_path=tmp_path,
            file_path=tmp_path / "zones" / "europe" / "deploy.yaml",
            config_model=model,
        )
        result = policy.evaluate(context)
        assert result.passed

    def test_spec_paths_source_fail(self, tmp_path: Path):
        model = _make_config_model(zones=[_zone("europe")])
        conv = _make_convention(
            scope="zones/**",
            pattern="zones/{zone}",
            validate={"zone": _yaml_rule("spec.zones[*].name")},
        )
        model.spec.paths = [conv]

        policy = PathConventionPolicy(_make_policy())
        context = _make_context(
            work_path=tmp_path,
            file_path=tmp_path / "zones" / "atlantis" / "deploy.yaml",
            config_model=model,
        )
        result = policy.evaluate(context)
        assert not result.passed
        assert len(result.violations) == 1
        assert "atlantis" in result.violations[0]

    def test_convention_filter_included(self, tmp_path: Path):
        model = _make_config_model(zones=[_zone("europe")])
        conv_a = _make_convention(
            name="zones", scope="zones/**", pattern="zones/{zone}", validate={"zone": _yaml_rule("spec.zones[*].name")}
        )
        conv_b = _make_convention(
            name="landscape",
            scope="zones/**",
            pattern="zones/{zone}",
            validate={"zone": _yaml_rule("spec.zones[*].name")},
        )
        model.spec.paths = [conv_a, conv_b]

        policy = PathConventionPolicy(_make_policy(configuration={"conventions": ["zones"]}))
        context = _make_context(
            work_path=tmp_path,
            file_path=tmp_path / "zones" / "atlantis" / "deploy.yaml",
            config_model=model,
        )
        result = policy.evaluate(context)
        assert not result.passed
        assert len(result.violations) == 1

    def test_convention_filter_excluded(self, tmp_path: Path):
        model = _make_config_model(zones=[_zone("europe")])
        conv = _make_convention(
            name="zones", scope="zones/**", pattern="zones/{zone}", validate={"zone": _yaml_rule("spec.zones[*].name")}
        )
        model.spec.paths = [conv]

        # Filter out the only convention → skip
        policy = PathConventionPolicy(_make_policy(configuration={"conventions": ["other"]}))
        context = _make_context(
            work_path=tmp_path,
            file_path=tmp_path / "zones" / "atlantis" / "deploy.yaml",
            config_model=model,
        )
        result = policy.evaluate(context)
        assert result.passed
        assert "skipped" in (result.details or {})

    def test_warn_enforcement_still_fails(self, tmp_path: Path):
        """warn enforcement: result.passed=False, enforcement='warn'."""
        model = _make_config_model(zones=[_zone("europe")])
        conv = _make_convention(
            scope="zones/**", pattern="zones/{zone}", validate={"zone": _yaml_rule("spec.zones[*].name")}
        )
        model.spec.paths = [conv]

        policy = PathConventionPolicy(_make_policy(enforcement="warn"))
        context = _make_context(
            work_path=tmp_path,
            file_path=tmp_path / "zones" / "atlantis" / "deploy.yaml",
            config_model=model,
        )
        result = policy.evaluate(context)
        assert not result.passed
        assert result.enforcement == "warn"

    def test_file_outside_scope_passes(self, tmp_path: Path):
        model = _make_config_model(zones=[_zone("europe")])
        conv = _make_convention(
            scope="zones/**", pattern="zones/{zone}", validate={"zone": _yaml_rule("spec.zones[*].name")}
        )
        model.spec.paths = [conv]

        policy = PathConventionPolicy(_make_policy())
        context = _make_context(
            work_path=tmp_path,
            file_path=tmp_path / "landscape" / "atlantis" / "deploy.yaml",
            config_model=model,
        )
        result = policy.evaluate(context)
        assert result.passed


# ===========================================================================
# PathConventionModel validation
# ===========================================================================


class TestPathConventionModel:
    def test_valid_model(self):
        m = PathConventionModel(
            name="test",
            scope="zones/**",
            pattern="zones/{zone}/{tenant}",
            rules={
                "zone": _yaml_rule("spec.zones[*].name"),
                "tenant": _path_rule("customers/{tenant}/tenant.yaml"),
            },
        )
        assert m.name == "test"

    def test_validate_key_not_in_pattern_raises(self):
        with pytest.raises(Exception, match="does not correspond"):
            PathConventionModel(
                name="bad",
                scope="zones/**",
                pattern="zones/{zone}",
                rules={"nonexistent": _yaml_rule("spec.zones[*].name")},
            )

    def test_no_validate_is_valid(self):
        m = PathConventionModel(name="simple", scope="**", pattern="{name}", rules=None)
        assert m.rules is None

    def test_empty_validate_is_valid(self):
        m = PathConventionModel(name="simple", scope="**", pattern="{name}", rules={})
        assert m.rules == {}

    def test_rules_kind_regex_rejected(self):
        """validate: only supports kind=yaml or kind=path — regex/jinja are for
        other, not-yet-wired call sites (ADR-0073)."""
        with pytest.raises(Exception, match="only support kind=yaml or kind=path"):
            PathConventionModel(
                name="bad-kind",
                scope="zones/**",
                pattern="zones/{zone}",
                rules={"zone": ExpressionModel(kind=ExpressionKind.REGEX, expression="^[a-z]+$")},
            )
