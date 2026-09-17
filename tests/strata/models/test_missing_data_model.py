#!/usr/bin/env python3
"""Unit tests for the shared missing-data mechanism (ADR-0082 Phase 1)."""

from strata.models.missing_data_model import (
    MissingDataOutcome,
    MissingDataPolicy,
    resolve_missing_data,
)

_REASON = "cost.json not found"


class TestResolveMissingData:
    def test_skip_does_not_block_and_has_no_warning(self):
        outcome = resolve_missing_data(MissingDataPolicy.SKIP, _REASON)
        assert outcome.blocked is False
        assert outcome.warning is None
        assert outcome.reason == _REASON

    def test_warn_does_not_block_but_surfaces_warning(self):
        outcome = resolve_missing_data(MissingDataPolicy.WARN, _REASON)
        assert outcome.blocked is False
        assert outcome.warning == _REASON
        assert outcome.reason == _REASON

    def test_block_blocks_and_has_no_redundant_warning(self):
        outcome = resolve_missing_data(MissingDataPolicy.BLOCK, _REASON)
        assert outcome.blocked is True
        assert outcome.warning is None
        assert outcome.reason == _REASON

    def test_reason_always_echoed_regardless_of_mode(self):
        for mode in MissingDataPolicy:
            outcome = resolve_missing_data(mode, _REASON)
            assert outcome.reason == _REASON


class TestMissingDataOutcomeImmutability:
    def test_outcome_is_frozen(self):
        outcome = resolve_missing_data(MissingDataPolicy.SKIP, _REASON)
        try:
            outcome.blocked = True  # type: ignore[misc]
        except (AttributeError, TypeError) as exc:
            assert "frozen" in str(exc) or "cannot assign" in str(exc).lower() or "immutable" in str(exc).lower()
        else:
            raise AssertionError("MissingDataOutcome must be immutable (frozen dataclass)")

    def test_outcome_constructible_directly(self):
        """Confirms the dataclass shape itself, independent of resolve_missing_data."""
        outcome = MissingDataOutcome(blocked=False, warning="w", reason="r")
        assert outcome.blocked is False
        assert outcome.warning == "w"
        assert outcome.reason == "r"


class TestMissingDataPolicyEnum:
    def test_is_str_enum_for_yaml_pydantic_round_tripping(self):
        """Matches the existing `enforcement: deny | warn | audit` str-enum
        convention already used elsewhere in PolicyModel — no new YAML-parsing
        behavior to design."""
        assert isinstance(MissingDataPolicy.SKIP, str)
        assert MissingDataPolicy.SKIP == "skip"
        assert MissingDataPolicy.WARN == "warn"
        assert MissingDataPolicy.BLOCK == "block"

    def test_has_exactly_three_values(self):
        assert {m.value for m in MissingDataPolicy} == {"skip", "warn", "block"}


class TestNoEngineCoupling:
    def test_module_does_not_import_policy_or_gate_engines(self):
        """Phase 1 acceptance criterion: resolve_missing_data must have zero
        dependency on either engine's types, so it's safely importable from both
        strata.validators.policies.base_policy and strata.controllers.gate_controller
        without either depending on the other.

        Checks actual import statements only — the module's docstring legitimately
        *mentions* those modules by name as prose, which is fine; it must never
        `import`/`from` them.
        """
        import ast

        import strata.models.missing_data_model as module

        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content)
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        assert not any(m.startswith("strata.validators") for m in imported_modules)
        assert not any(m.startswith("strata.controllers") for m in imported_modules)
