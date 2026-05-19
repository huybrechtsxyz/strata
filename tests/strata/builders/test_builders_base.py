"""Unit tests for BaseBuilder."""

import pytest

from strata.builders.base_builder import BaseBuilder


class _ConcreteBuilder(BaseBuilder):
    """Minimal concrete implementation for testing the abstract base."""

    def build(self, deployment_service, work_path, build_path, dry_run=False):
        return True

    def before_build(self, deployment_service, work_path, build_path):
        return True

    def after_build(self, deployment_service, work_path, build_path, dry_run=False):
        return True


class TestBaseBuilderAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseBuilder()  # type: ignore


class TestBaseBuilderInit:
    def test_default_verbose_false(self):
        builder = _ConcreteBuilder()
        assert builder.verbose is False

    def test_verbose_true(self):
        builder = _ConcreteBuilder(verbose=True)
        assert builder.verbose is True

    def test_messages_empty_on_init(self):
        builder = _ConcreteBuilder()
        assert builder._messages == []

    def test_errors_empty_on_init(self):
        builder = _ConcreteBuilder()
        assert builder._errors == []

    def test_has_errors_false_initially(self):
        builder = _ConcreteBuilder()
        assert builder.has_errors() is False

    def test_has_messages_false_initially(self):
        builder = _ConcreteBuilder()
        assert builder.has_messages() is False


class TestBaseBuilderHelpers:
    def test_get_errors_empty(self):
        builder = _ConcreteBuilder()
        assert builder.get_errors() == []

    def test_get_messages_empty(self):
        builder = _ConcreteBuilder()
        assert builder.get_messages() == []

    def test_has_errors_after_append(self):
        builder = _ConcreteBuilder()
        builder._errors.append("something broke")
        assert builder.has_errors() is True

    def test_has_messages_after_append(self):
        builder = _ConcreteBuilder()
        builder._messages.append("progress")
        assert builder.has_messages() is True

    def test_get_errors_returns_list(self):
        builder = _ConcreteBuilder()
        builder._errors.extend(["e1", "e2"])
        assert builder.get_errors() == ["e1", "e2"]

    def test_get_messages_returns_list(self):
        builder = _ConcreteBuilder()
        builder._messages.extend(["m1", "m2"])
        assert builder.get_messages() == ["m1", "m2"]

    def test_instances_are_independent(self):
        b1 = _ConcreteBuilder()
        b2 = _ConcreteBuilder()
        b1._errors.append("only b1")
        assert b2.has_errors() is False
        assert b1.get_errors() == ["only b1"]
