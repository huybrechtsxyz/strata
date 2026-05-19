"""Tests for ScaffoldTemplateModel."""

import pytest
from pydantic import ValidationError

from strata.models.scaffold_template_model import ScaffoldTemplateModel, ScaffoldTemplateVariable


class TestScaffoldTemplateModel:
    def test_minimal_valid(self):
        model = ScaffoldTemplateModel.model_validate({"name": "aks"})
        assert model.name == "aks"
        assert model.description == ""
        assert model.variables == []

    def test_full_valid(self):
        data = {
            "name": "aks",
            "description": "AKS starter",
            "variables": [
                {"name": "solution_name", "description": "Solution name", "default": "my-aks"},
                {"name": "region", "description": "Azure region", "default": "westeurope"},
            ],
        }
        model = ScaffoldTemplateModel.model_validate(data)
        assert model.name == "aks"
        assert len(model.variables) == 2
        assert model.variables[0].name == "solution_name"
        assert model.variables[0].default == "my-aks"

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            ScaffoldTemplateModel.model_validate({"description": "no name"})

    def test_get_default_variables(self):
        model = ScaffoldTemplateModel.model_validate(
            {
                "name": "aks",
                "variables": [
                    {"name": "solution_name", "default": "my-aks"},
                    {"name": "region", "default": "westeurope"},
                ],
            }
        )
        defaults = model.get_default_variables()
        assert defaults == {"solution_name": "my-aks", "region": "westeurope"}

    def test_get_variable_found(self):
        model = ScaffoldTemplateModel.model_validate(
            {
                "name": "aks",
                "variables": [{"name": "solution_name", "default": "x"}],
            }
        )
        var = model.get_variable("solution_name")
        assert var is not None
        assert var.default == "x"

    def test_get_variable_not_found(self):
        model = ScaffoldTemplateModel.model_validate({"name": "aks"})
        assert model.get_variable("nonexistent") is None

    def test_variable_defaults_to_empty_string(self):
        var = ScaffoldTemplateVariable.model_validate({"name": "x"})
        assert var.description == ""
        assert var.default == ""
