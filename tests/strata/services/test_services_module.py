#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_module.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : ModuleService test fixtures and utilities for strata CLI tests.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.module_model import ModuleModel
from strata.services.module_service import ModuleService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestModuleService:
    @pytest.fixture
    def get_module_service(self):
        return ModuleService(_data("modules/module-standard.yaml"))

    def test_get_model_class(self, get_module_service):
        service = get_module_service
        model_class = service._get_model_class()
        assert model_class == ModuleModel

    def test_validate_standard(self, get_module_service):
        service = get_module_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_module_service):
        service = get_module_service
        service.validate()
        assert service.get_kind() == "module"


class TestModuleServiceDynamicValidation:
    def _make_service(self, data: dict) -> ModuleService:
        svc = ModuleService(data=data)
        return svc

    def _base(self) -> dict:
        return {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "module",
            "meta": {"name": "my-app"},
            "spec": {
                "source": {
                    "repository": "platform-modules",
                    "source_path": "services/my-app",
                },
            },
        }

    def test_no_services_passes(self):
        svc = self._make_service(self._base())
        ok, errors = svc.validate()
        assert ok, errors

    def test_valid_depends_on(self):
        data = self._base()
        data["spec"]["services"] = [
            {"name": "web", "image": "nginx"},
            {"name": "db", "image": "postgres:15", "depends_on": ["web"]},
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert ok, errors

    def test_invalid_depends_on_unknown_service(self):
        data = self._base()
        data["spec"]["services"] = [
            {"name": "web", "image": "nginx", "depends_on": ["typo-service"]},
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert not ok
        assert any("depends_on" in e and "typo-service" in e for e in errors)

    def test_valid_env_secret_ref(self):
        data = self._base()
        data["spec"]["references"] = {"secrets": ["DB_PASSWORD"]}
        data["spec"]["services"] = [
            {
                "name": "web",
                "image": "nginx",
                "environment": [{"key": "DB_PASSWORD", "secret": "DB_PASSWORD"}],
            }
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert ok, errors

    def test_invalid_env_secret_not_declared(self):
        data = self._base()
        # No references declared
        data["spec"]["services"] = [
            {
                "name": "web",
                "image": "nginx",
                "environment": [{"key": "DB_PASSWORD", "secret": "DB_PASSWORD"}],
            }
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert not ok
        assert any("secret" in e and "DB_PASSWORD" in e for e in errors)

    def test_invalid_env_var_not_declared(self):
        data = self._base()
        data["spec"]["references"] = {"variables": ["APP_PORT"]}
        data["spec"]["services"] = [
            {
                "name": "web",
                "image": "nginx",
                "environment": [{"key": "MISSING_VAR", "var": "MISSING_VAR"}],
            }
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert not ok
        assert any("var" in e and "MISSING_VAR" in e for e in errors)

    def test_invalid_env_feature_not_declared(self):
        data = self._base()
        data["spec"]["services"] = [
            {
                "name": "web",
                "image": "nginx",
                "environment": [{"key": "ENABLE_X", "feature": "ENABLE_X"}],
            }
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert not ok
        assert any("feature" in e and "ENABLE_X" in e for e in errors)

    def test_plain_value_env_needs_no_ref(self):
        data = self._base()
        data["spec"]["services"] = [
            {
                "name": "web",
                "image": "nginx",
                "environment": [{"key": "TZ", "value": "UTC"}],
            }
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert ok, errors

    def test_cross_module_depends_on_syntax_accepted(self):
        """@module/service refs are accepted at Phase 1 (validated at build time)."""
        data = self._base()
        data["spec"]["services"] = [
            {"name": "web", "image": "nginx", "depends_on": ["@mod_auth/server"]},
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert ok, errors

    def test_cross_module_depends_on_shorthand_accepted(self):
        """@module shorthand (module == service) is accepted."""
        data = self._base()
        data["spec"]["services"] = [
            {"name": "web", "image": "nginx", "depends_on": ["@mod_auth"]},
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert ok, errors

    def test_cross_module_depends_on_empty_module_rejected(self):
        """@/service with empty module name is invalid syntax."""
        data = self._base()
        data["spec"]["services"] = [
            {"name": "web", "image": "nginx", "depends_on": ["@/server"]},
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert not ok
        assert any("invalid syntax" in e for e in errors)

    def test_cross_module_depends_on_empty_service_rejected(self):
        """@module/ with empty service name is invalid."""
        data = self._base()
        data["spec"]["services"] = [
            {"name": "web", "image": "nginx", "depends_on": ["@mod_auth/"]},
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert not ok
        assert any("empty service name" in e for e in errors)

    def test_mixed_intra_and_cross_module_depends_on(self):
        """Mixing local and @module/service refs works."""
        data = self._base()
        data["spec"]["services"] = [
            {"name": "db", "image": "postgres:16"},
            {"name": "web", "image": "nginx", "depends_on": ["db", "@mod_auth/server"]},
        ]
        svc = self._make_service(data)
        ok, errors = svc.validate()
        assert ok, errors
