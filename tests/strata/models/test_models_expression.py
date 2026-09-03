"""Tests for strata.models.expression_model.ExpressionModel (ADR-0073)."""

import pytest
from pydantic import ValidationError

from strata.models.expression_model import ExpressionKind, ExpressionModel


class TestExpressionModelYamlKind:
    """kind=yaml — JMESPath query, compiled once at construction."""

    def test_valid_expression_compiles(self):
        model = ExpressionModel(kind=ExpressionKind.YAML, expression="zones[*].name")
        assert model.query({"zones": [{"name": "europe"}, {"name": "us"}]}) == ["europe", "us"]

    def test_invalid_expression_raises_at_construction(self):
        with pytest.raises(ValidationError, match="invalid JMESPath expression"):
            ExpressionModel(kind=ExpressionKind.YAML, expression="zones[*")

    def test_query_wrong_kind_raises(self):
        model = ExpressionModel(kind=ExpressionKind.PATH, expression="customers/{tenant}/tenant.yaml")
        with pytest.raises(ValueError, match="query\\(\\) requires kind=yaml"):
            model.query({})

    def test_no_match_returns_none(self):
        model = ExpressionModel(kind=ExpressionKind.YAML, expression="zones[*].name")
        assert model.query({"zones": []}) == []


class TestExpressionModelPathKind:
    """kind=path — no compilation needed; check_path() delegates to path_convention.py's
    evaluate_file_rule() for {segment} substitution + the existence check."""

    def test_construction_succeeds_without_compiling(self):
        model = ExpressionModel(kind=ExpressionKind.PATH, expression="customers/{tenant}/tenant.yaml")
        assert model.expression == "customers/{tenant}/tenant.yaml"
        assert model.kind == ExpressionKind.PATH

    def test_check_path_existing_file_returns_none(self, tmp_path):
        (tmp_path / "customers" / "acme").mkdir(parents=True)
        (tmp_path / "customers" / "acme" / "tenant.yaml").write_text("x")
        model = ExpressionModel(kind=ExpressionKind.PATH, expression="customers/{tenant}/tenant.yaml")

        assert model.check_path({"tenant": "acme"}, tmp_path) is None

    def test_check_path_missing_file_returns_violation(self, tmp_path):
        model = ExpressionModel(kind=ExpressionKind.PATH, expression="customers/{tenant}/tenant.yaml")

        result = model.check_path({"tenant": "acme"}, tmp_path)

        assert result is not None
        assert "does not exist" in result

    def test_check_path_wrong_kind_raises(self, tmp_path):
        model = ExpressionModel(kind=ExpressionKind.YAML, expression="zones[*].name")
        with pytest.raises(ValueError, match="check_path\\(\\) requires kind=path"):
            model.check_path({}, tmp_path)


class TestExpressionModelRegexKind:
    """kind=regex — fixed-shape pattern match, compiled once at construction."""

    def test_valid_regex_compiles_and_matches(self):
        model = ExpressionModel(kind=ExpressionKind.REGEX, expression=r"^[a-z][a-z0-9-]*$")
        assert model.matches("valid-name") is True
        assert model.matches("Invalid_Name") is False

    def test_invalid_regex_raises_at_construction(self):
        with pytest.raises(ValidationError, match="invalid regex"):
            ExpressionModel(kind=ExpressionKind.REGEX, expression="[unclosed")

    def test_matches_wrong_kind_raises(self):
        model = ExpressionModel(kind=ExpressionKind.YAML, expression="zones[*].name")
        with pytest.raises(ValueError, match="matches\\(\\) requires kind=regex"):
            model.matches("anything")


class TestExpressionModelJinjaKind:
    """kind=jinja — boolean/comparison expression, compiled once at construction."""

    def test_valid_expression_compiles_and_evaluates(self):
        model = ExpressionModel(kind=ExpressionKind.JINJA, expression="actual >= threshold")
        assert model.evaluate({"actual": 10, "threshold": 5}) is True
        assert model.evaluate({"actual": 1, "threshold": 5}) is False

    def test_invalid_expression_raises_at_construction(self):
        with pytest.raises(ValidationError, match="invalid jinja expression"):
            ExpressionModel(kind=ExpressionKind.JINJA, expression="actual >=")

    def test_evaluate_wrong_kind_raises(self):
        model = ExpressionModel(kind=ExpressionKind.YAML, expression="zones[*].name")
        with pytest.raises(ValueError, match="evaluate\\(\\) requires kind=jinja"):
            model.evaluate({})


class TestExpressionModelSchema:
    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            ExpressionModel(kind=ExpressionKind.PATH, expression="x", extra="not allowed")

    def test_kind_is_required(self):
        with pytest.raises(ValidationError):
            ExpressionModel(expression="x")

    def test_expression_is_required(self):
        with pytest.raises(ValidationError):
            ExpressionModel(kind=ExpressionKind.PATH)

    def test_compiled_excluded_from_serialization(self):
        model = ExpressionModel(kind=ExpressionKind.YAML, expression="zones[*].name")
        dumped = model.model_dump()
        assert dumped == {"kind": "yaml", "expression": "zones[*].name"}
