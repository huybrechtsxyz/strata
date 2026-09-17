"""Tests for strata.utils.resolved_values.parse_bool (ADR-0083 Phase 1).

``parse_bool`` was extracted from two byte-identical copies inside
``BaseBuilder._build_template_context``. The parity class below pins the exact
behaviour those copies had, so the extraction is provably a no-op refactor.
"""

from strata.utils.resolved_values import FALSE_TOKENS, parse_bool


class TestParseBoolParity:
    """Behaviour must match the pre-extraction feature-flag logic exactly.

    Original (base_builder.py)::

        if isinstance(raw, bool):   features[k] = raw
        elif isinstance(raw, str):  features[k] = raw.lower() not in ("false", "0", "no", "")
        else:                       features[k] = bool(raw)
    """

    def test_false_tokens_are_exactly_the_original_four(self):
        assert FALSE_TOKENS == {"false", "0", "no", ""}

    def test_bool_passes_through_unchanged(self):
        assert parse_bool(True) is True
        assert parse_bool(False) is False

    def test_original_false_tokens_are_false(self):
        for token in ("false", "0", "no", ""):
            assert parse_bool(token) is False, token

    def test_token_matching_is_case_insensitive(self):
        for token in ("FALSE", "False", "No", "NO"):
            assert parse_bool(token) is False, token

    def test_any_other_string_is_true(self):
        for token in ("true", "True", "yes", "1", "on", "enabled", "anything"):
            assert parse_bool(token) is True, token

    def test_non_bool_non_str_falls_back_to_bool(self):
        assert parse_bool(1) is True
        assert parse_bool(0) is False
        assert parse_bool([1]) is True
        assert parse_bool([]) is False
        assert parse_bool(None) is False


class TestParseBoolWhitespace:
    """Whitespace stripping is the one deliberate improvement over the originals.

    The old copies compared ``raw.lower()`` without stripping, so ``" false"``
    read as True. Both call sites feed values from YAML or environment variables
    where stray whitespace is an author slip, never a meaningful value.
    """

    def test_surrounding_whitespace_is_stripped(self):
        assert parse_bool("  false  ") is False
        assert parse_bool("\tno\n") is False

    def test_whitespace_only_string_is_false(self):
        assert parse_bool("   ") is False
