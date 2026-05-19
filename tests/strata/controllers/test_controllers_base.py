"""Tests for BaseController."""

from strata.controllers.base_controller import BaseController


class TestBaseControllerInit:
    def test_errors_empty_on_init(self):
        ctrl = BaseController()
        assert ctrl._errors == []

    def test_messages_empty_on_init(self):
        ctrl = BaseController()
        assert ctrl._messages == []

    def test_logger_is_set(self):
        ctrl = BaseController()
        assert ctrl.logger is not None


class TestBaseControllerErrors:
    def test_has_errors_false_when_empty(self):
        ctrl = BaseController()
        assert ctrl.has_errors() is False

    def test_has_errors_true_after_append(self):
        ctrl = BaseController()
        ctrl._errors.append("something went wrong")
        assert ctrl.has_errors() is True

    def test_get_errors_returns_copy(self):
        ctrl = BaseController()
        ctrl._errors.append("e1")
        result = ctrl.get_errors()
        assert result == ["e1"]
        result.append("e2")
        assert ctrl.get_errors() == ["e1"]

    def test_clear_errors_removes_all(self):
        ctrl = BaseController()
        ctrl._errors.append("e1")
        ctrl._errors.append("e2")
        ctrl.clear_errors()
        assert ctrl.has_errors() is False
        assert ctrl.get_errors() == []

    def test_get_errors_empty_returns_empty_list(self):
        ctrl = BaseController()
        assert ctrl.get_errors() == []


class TestBaseControllerMessages:
    def test_has_messages_false_when_empty(self):
        ctrl = BaseController()
        assert ctrl.has_messages() is False

    def test_has_messages_true_after_append(self):
        ctrl = BaseController()
        ctrl._messages.append("hello")
        assert ctrl.has_messages() is True

    def test_get_messages_returns_copy(self):
        ctrl = BaseController()
        ctrl._messages.append("m1")
        result = ctrl.get_messages()
        assert result == ["m1"]
        result.append("m2")
        assert ctrl.get_messages() == ["m1"]

    def test_clear_messages_removes_all(self):
        ctrl = BaseController()
        ctrl._messages.append("m1")
        ctrl._messages.append("m2")
        ctrl.clear_messages()
        assert ctrl.has_messages() is False
        assert ctrl.get_messages() == []

    def test_get_messages_empty_returns_empty_list(self):
        ctrl = BaseController()
        assert ctrl.get_messages() == []


class TestBaseControllerIndependence:
    def test_errors_and_messages_are_independent(self):
        ctrl = BaseController()
        ctrl._errors.append("err")
        ctrl._messages.append("msg")
        ctrl.clear_errors()
        assert ctrl.has_errors() is False
        assert ctrl.has_messages() is True

    def test_two_instances_have_separate_state(self):
        c1 = BaseController()
        c2 = BaseController()
        c1._errors.append("only in c1")
        assert c2.has_errors() is False
