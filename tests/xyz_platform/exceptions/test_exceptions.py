"""Tests for xyz_platform.exceptions — full hierarchy."""

import pytest

from xyz_platform.exceptions import (
    ConfigurationNotFoundError,
    DeploymentNotFoundError,
    DuplicateNameError,
    InvalidReferenceError,
    ModelValidationError,
    PathValidationError,
    PlatformConfigurationError,
    PlatformError,
    PlatformFileNotFoundError,
    PlatformNotFoundError,
    PlatformStateError,
    PlatformValidationError,
    ProviderNotFoundError,
    ResourceTypeNotFoundError,
    SchemaVersionError,
    ServiceLoadError,
    ServiceNotAvailableError,
    ServiceNotValidatedError,
    UnsupportedKindError,
    WorkspaceNotFoundError,
)

# ---------------------------------------------------------------------------
# PlatformError — base
# ---------------------------------------------------------------------------


class TestPlatformError:
    def test_is_exception(self):
        e = PlatformError("oops")
        assert isinstance(e, Exception)

    def test_message_stored(self):
        e = PlatformError("something went wrong")
        assert e.message == "something went wrong"
        assert str(e.args[0]) == "something went wrong"

    def test_default_error_code_is_class_name(self):
        e = PlatformError("msg")
        assert e.error_code == "PlatformError"

    def test_custom_error_code(self):
        e = PlatformError("msg", error_code="MY_CODE")
        assert e.error_code == "MY_CODE"

    def test_details_default_empty_dict(self):
        e = PlatformError("msg")
        assert e.details == {}

    def test_details_stored(self):
        e = PlatformError("msg", details={"key": "value"})
        assert e.details == {"key": "value"}

    def test_cause_default_none(self):
        e = PlatformError("msg")
        assert e.cause is None

    def test_cause_stored(self):
        cause = ValueError("original")
        e = PlatformError("msg", cause=cause)
        assert e.cause is cause

    def test_to_dict_minimal(self):
        e = PlatformError("msg")
        d = e.to_dict()
        assert d["error"] == "PlatformError"
        assert d["error_code"] == "PlatformError"
        assert d["message"] == "msg"
        assert d["details"] == {}
        assert "cause" not in d

    def test_to_dict_with_cause(self):
        e = PlatformError("msg", cause=ValueError("root"))
        d = e.to_dict()
        assert "cause" in d
        assert "root" in d["cause"]

    def test_str_minimal(self):
        e = PlatformError("msg")
        assert "msg" in str(e)

    def test_str_with_details(self):
        e = PlatformError("msg", details={"k": "v"})
        s = str(e)
        assert "k" in s
        assert "v" in s

    def test_str_with_cause(self):
        e = PlatformError("msg", cause=ValueError("root cause"))
        assert "root cause" in str(e)


# ---------------------------------------------------------------------------
# Base subclass hierarchy — isinstance checks
# ---------------------------------------------------------------------------


class TestBaseSubclasses:
    def test_configuration_error_is_platform_error(self):
        e = PlatformConfigurationError("cfg")
        assert isinstance(e, PlatformError)

    def test_not_found_error_is_platform_error(self):
        e = PlatformNotFoundError("nf")
        assert isinstance(e, PlatformError)

    def test_validation_error_is_platform_error(self):
        e = PlatformValidationError("val")
        assert isinstance(e, PlatformError)

    def test_state_error_is_platform_error(self):
        e = PlatformStateError("state")
        assert isinstance(e, PlatformError)


# ---------------------------------------------------------------------------
# ModelValidationError
# ---------------------------------------------------------------------------


class TestModelValidationError:
    def test_is_platform_validation_error(self):
        e = ModelValidationError("MyModel", [])
        assert isinstance(e, PlatformValidationError)

    def test_model_name_stored(self):
        e = ModelValidationError("MyModel", [{"loc": "field", "msg": "required"}])
        assert e.model_name == "MyModel"

    def test_validation_errors_stored(self):
        errors = [{"loc": "name", "msg": "field required"}]
        e = ModelValidationError("MyModel", errors)
        assert e.validation_errors == errors

    def test_default_message_includes_model_name(self):
        e = ModelValidationError("MyModel", [])
        assert "MyModel" in e.message

    def test_custom_message(self):
        e = ModelValidationError("MyModel", [], message="custom msg")
        assert e.message == "custom msg"

    def test_error_code(self):
        e = ModelValidationError("M", [])
        assert e.error_code == "MODEL_VALIDATION_ERROR"

    def test_details_contain_model_and_errors(self):
        errors = [{"loc": "x"}]
        e = ModelValidationError("M", errors)
        assert e.details["model"] == "M"
        assert e.details["errors"] == errors


# ---------------------------------------------------------------------------
# DuplicateNameError
# ---------------------------------------------------------------------------


class TestDuplicateNameError:
    def test_message_includes_name(self):
        e = DuplicateNameError("stage", "prod")
        assert "prod" in e.message

    def test_message_includes_location(self):
        e = DuplicateNameError("stage", "prod", location="deployment.yaml")
        assert "deployment.yaml" in e.message

    def test_no_location(self):
        e = DuplicateNameError("stage", "prod")
        assert e.details["location"] is None

    def test_error_code(self):
        e = DuplicateNameError("stage", "prod")
        assert e.error_code == "DUPLICATE_NAME"


# ---------------------------------------------------------------------------
# InvalidReferenceError
# ---------------------------------------------------------------------------


class TestInvalidReferenceError:
    def test_message_contains_all_parts(self):
        e = InvalidReferenceError("Stage", "deploy-prod", "workspace", "ws-dev")
        assert "Stage" in e.message
        assert "deploy-prod" in e.message
        assert "workspace" in e.message
        assert "ws-dev" in e.message

    def test_error_code(self):
        e = InvalidReferenceError("S", "n", "t", "v")
        assert e.error_code == "INVALID_REFERENCE"

    def test_details(self):
        e = InvalidReferenceError("Stage", "name", "ref_type", "ref_val")
        assert e.details["source_type"] == "Stage"
        assert e.details["source_name"] == "name"
        assert e.details["reference_type"] == "ref_type"
        assert e.details["reference_value"] == "ref_val"


# ---------------------------------------------------------------------------
# UnsupportedKindError
# ---------------------------------------------------------------------------


class TestUnsupportedKindError:
    def test_message_contains_kind(self):
        e = UnsupportedKindError("unknown-kind")
        assert "unknown-kind" in e.message

    def test_message_lists_supported_kinds(self):
        e = UnsupportedKindError("bad", supported_kinds=["deployment", "workspace"])
        assert "deployment" in e.message
        assert "workspace" in e.message

    def test_no_supported_kinds(self):
        e = UnsupportedKindError("bad")
        assert e.details["supported_kinds"] is None

    def test_error_code(self):
        e = UnsupportedKindError("bad")
        assert e.error_code == "UNSUPPORTED_KIND"


# ---------------------------------------------------------------------------
# SchemaVersionError
# ---------------------------------------------------------------------------


class TestSchemaVersionError:
    def test_message_contains_both_versions(self):
        e = SchemaVersionError("v2", "v1")
        assert "v2" in e.message
        assert "v1" in e.message

    def test_error_code(self):
        e = SchemaVersionError("v2", "v1")
        assert e.error_code == "SCHEMA_VERSION_MISMATCH"

    def test_details(self):
        e = SchemaVersionError("v2", "v1")
        assert e.details["actual"] == "v2"
        assert e.details["expected"] == "v1"


# ---------------------------------------------------------------------------
# PathValidationError
# ---------------------------------------------------------------------------


class TestPathValidationError:
    def test_default_message_includes_option_and_provided(self):
        e = PathValidationError("--work-path", "/bad/path", "must be a directory")
        assert "--work-path" in e.message
        assert "/bad/path" in e.message

    def test_custom_message(self):
        e = PathValidationError("--work-path", "/bad", "expected dir", message="custom")
        assert e.message == "custom"

    def test_error_code(self):
        e = PathValidationError("--opt", "/p", "exp")
        assert e.error_code == "PATH_VALIDATION_ERROR"

    def test_details(self):
        e = PathValidationError("--opt", "/p", "exp", resolved="/abs/p", work_path="/w")
        assert e.details["option"] == "--opt"
        assert e.details["provided"] == "/p"
        assert e.details["expected"] == "exp"
        assert e.details["resolved"] == "/abs/p"
        assert e.details["work_path"] == "/w"

    def test_is_platform_validation_error(self):
        e = PathValidationError("--opt", "/p", "exp")
        assert isinstance(e, PlatformValidationError)


# ---------------------------------------------------------------------------
# ServiceNotAvailableError
# ---------------------------------------------------------------------------


class TestServiceNotAvailableError:
    def test_message_contains_service_name(self):
        e = ServiceNotAvailableError("TerraformService")
        assert "TerraformService" in e.message

    def test_message_contains_reason(self):
        e = ServiceNotAvailableError("TerraformService", reason="binary missing")
        assert "binary missing" in e.message

    def test_no_reason(self):
        e = ServiceNotAvailableError("TerraformService")
        assert e.details["reason"] is None

    def test_error_code(self):
        e = ServiceNotAvailableError("S")
        assert e.error_code == "SERVICE_NOT_AVAILABLE"

    def test_is_platform_error(self):
        e = ServiceNotAvailableError("S")
        assert isinstance(e, PlatformError)


# ---------------------------------------------------------------------------
# ServiceNotValidatedError
# ---------------------------------------------------------------------------


class TestServiceNotValidatedError:
    def test_message_contains_service_name(self):
        e = ServiceNotValidatedError("WorkspaceService")
        assert "WorkspaceService" in e.message

    def test_message_hints_validate(self):
        e = ServiceNotValidatedError("WorkspaceService")
        assert "validate" in e.message.lower()

    def test_error_code(self):
        e = ServiceNotValidatedError("S")
        assert e.error_code == "SERVICE_NOT_VALIDATED"

    def test_is_platform_state_error(self):
        e = ServiceNotValidatedError("S")
        assert isinstance(e, PlatformStateError)


# ---------------------------------------------------------------------------
# ServiceLoadError
# ---------------------------------------------------------------------------


class TestServiceLoadError:
    def test_message_contains_service_and_reason(self):
        e = ServiceLoadError("DeploymentService", "file missing")
        assert "DeploymentService" in e.message
        assert "file missing" in e.message

    def test_with_cause(self):
        cause = FileNotFoundError("no file")
        e = ServiceLoadError("S", "reason", cause=cause)
        assert e.cause is cause

    def test_error_code(self):
        e = ServiceLoadError("S", "r")
        assert e.error_code == "SERVICE_LOAD_ERROR"


# ---------------------------------------------------------------------------
# "Not found" service exceptions
# ---------------------------------------------------------------------------


class TestWorkspaceNotFoundError:
    def test_message_contains_path(self):
        e = WorkspaceNotFoundError("/some/path/workspace.yaml")
        assert "/some/path/workspace.yaml" in e.message

    def test_error_code(self):
        e = WorkspaceNotFoundError("/p")
        assert e.error_code == "WORKSPACE_NOT_FOUND"

    def test_is_platform_not_found(self):
        e = WorkspaceNotFoundError("/p")
        assert isinstance(e, PlatformNotFoundError)


class TestDeploymentNotFoundError:
    def test_message_contains_path(self):
        e = DeploymentNotFoundError("/deploy.yaml")
        assert "/deploy.yaml" in e.message

    def test_error_code(self):
        e = DeploymentNotFoundError("/p")
        assert e.error_code == "DEPLOYMENT_NOT_FOUND"


class TestConfigurationNotFoundError:
    def test_default_message(self):
        e = ConfigurationNotFoundError()
        assert "configuration" in e.message.lower()

    def test_with_detail(self):
        e = ConfigurationNotFoundError(detail="no config.yaml found")
        assert "no config.yaml found" in e.message

    def test_error_code(self):
        e = ConfigurationNotFoundError()
        assert e.error_code == "CONFIGURATION_NOT_FOUND"


class TestProviderNotFoundError:
    def test_message_contains_provider_type(self):
        e = ProviderNotFoundError("azure")
        assert "azure" in e.message

    def test_message_lists_available(self):
        e = ProviderNotFoundError("gcp", available=["azure", "aws"])
        assert "azure" in e.message
        assert "aws" in e.message

    def test_error_code(self):
        e = ProviderNotFoundError("gcp")
        assert e.error_code == "PROVIDER_NOT_FOUND"


class TestResourceTypeNotFoundError:
    def test_message_contains_type_and_provider(self):
        e = ResourceTypeNotFoundError("vm", "azure")
        assert "vm" in e.message
        assert "azure" in e.message

    def test_message_lists_available(self):
        e = ResourceTypeNotFoundError("vm", "azure", available=["storage", "db"])
        assert "storage" in e.message

    def test_error_code(self):
        e = ResourceTypeNotFoundError("t", "p")
        assert e.error_code == "RESOURCE_TYPE_NOT_FOUND"


class TestPlatformFileNotFoundError:
    def test_message_contains_path(self):
        e = PlatformFileNotFoundError("/missing/file.yaml")
        assert "/missing/file.yaml" in e.message

    def test_message_with_file_type(self):
        e = PlatformFileNotFoundError("/file.yaml", file_type="deployment")
        assert "deployment" in e.message

    def test_error_code(self):
        e = PlatformFileNotFoundError("/f")
        assert e.error_code == "FILE_NOT_FOUND"

    def test_is_platform_not_found(self):
        e = PlatformFileNotFoundError("/f")
        assert isinstance(e, PlatformNotFoundError)


# ---------------------------------------------------------------------------
# Raise / catch semantics
# ---------------------------------------------------------------------------


class TestRaiseCatch:
    def test_catch_as_platform_error(self):
        with pytest.raises(PlatformError):
            raise ModelValidationError("M", [])

    def test_catch_as_platform_validation_error(self):
        with pytest.raises(PlatformValidationError):
            raise DuplicateNameError("stage", "prod")

    def test_catch_as_platform_not_found(self):
        with pytest.raises(PlatformNotFoundError):
            raise WorkspaceNotFoundError("/ws.yaml")

    def test_catch_as_exception(self):
        with pytest.raises(Exception):
            raise ServiceNotValidatedError("S")
