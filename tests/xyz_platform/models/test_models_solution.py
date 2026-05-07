"""Tests for SolutionModel and related Pydantic models."""

from xyz_platform.models.solution_model import SolutionSpecModel


def _make_spec(**kwargs) -> SolutionSpecModel:
    return SolutionSpecModel(solution_id="00000000-0000-0000-0000-000000000001", **kwargs)


class TestSolutionSpecContext:
    def test_solution_spec_context_optional(self):
        """spec.context is optional — defaults to None."""
        spec = _make_spec()
        assert spec.context is None

    def test_solution_spec_context_set(self):
        """spec.context accepts a dict of strings."""
        spec = _make_spec(context={"owner": "team", "version": "2.0.0"})
        assert spec.context == {"owner": "team", "version": "2.0.0"}

    def test_solution_spec_context_empty_dict(self):
        """spec.context accepts an empty dict."""
        spec = _make_spec(context={})
        assert spec.context == {}

    def test_solution_spec_context_roundtrip(self):
        """spec.context survives a Pydantic model_dump / model_validate round-trip."""
        spec = _make_spec(context={"owner": "acme"})
        dumped = spec.model_dump()
        restored = SolutionSpecModel.model_validate(dumped)
        assert restored.context == {"owner": "acme"}
