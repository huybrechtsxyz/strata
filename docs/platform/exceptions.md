# Custom Exception System

Structured error handling with error codes, details, and cause chaining. All exceptions extend `PlatformError`.

## Hierarchy

```text
PlatformError (base)
├── PlatformConfigurationError (invalid/missing config)
├── PlatformNotFoundError (resource not found)
│   ├── WorkspaceNotFoundError
│   ├── DeploymentNotFoundError
│   ├── ConfigurationNotFoundError
│   ├── ProviderNotFoundError
│   ├── ResourceTypeNotFoundError
│   └── PlatformFileNotFoundError (file not found)
├── PlatformValidationError (validation failures)
│   ├── ModelValidationError (Pydantic model validation)
│   ├── DuplicateNameError (duplicate entities)
│   ├── InvalidReferenceError (invalid cross-references)
│   ├── UnsupportedKindError (unsupported resource kind)
│   ├── SchemaVersionError (incompatible schema version)
│   └── PathValidationError (CLI/workspace path errors)
├── PlatformStateError (service state issues)
│   └── ServiceNotValidatedError (method called before validate())
├── ServiceNotAvailableError (required service unavailable)
└── ServiceLoadError (failed to load related services)
```

## Base Exception Features

**PlatformError** - All exceptions inherit:

- `error_code`: Machine-readable identifier (e.g., `"FILE_NOT_FOUND"`)
- `details`: Dict with contextual information
- `cause`: Original exception for chaining
- `to_dict()`: Convert to structured dict for logging/API

```python
from strata.exceptions import PlatformError

try:
    # operation
except Exception as e:
    raise PlatformError(
        message="Operation failed",
        error_code="OPERATION_FAILED",
        details={"operation": "load_data", "path": "/some/path"},
        cause=e
    )
```

## Common Usage

**Configuration errors:**

```python
from strata.exceptions import PlatformConfigurationError

if not path and not data:
    raise PlatformConfigurationError("Either path or data must be provided")
```

**Model validation:**

```python
from strata.exceptions import ModelValidationError

try:
    model = MyModel.model_validate(data)
except ValidationError as e:
    raise ModelValidationError(
        model_name="MyModel",
        validation_errors=e.errors(),
    ) from e
```

**Service state:**

```python
from strata.exceptions import ServiceNotValidatedError

def _ensure_validated(self):
    if not self._validated:
        raise ServiceNotValidatedError(self.__class__.__name__)
```

**Invalid references:**

```python
from strata.exceptions import InvalidReferenceError

raise InvalidReferenceError(
    source_type="Topology",
    source_name=name,
    reference_type="provider",
    reference_value=provider,
)
```

## Service Error Handling Pattern

```python
def validate(self):
    """Validate with structured error storage."""
    try:
        self.model = ModelClass.model_validate(self.data)
        self._validated = True
        return True, []
    except ValidationError as e:
        err = ModelValidationError(
            model_name=ModelClass.__name__,
            validation_errors=e.errors(),
        )
        self._errors.append(err.message)
        return False, self._errors
```

## Accessing Errors

**String errors (backward compatible):**

```python
service = DeploymentService(data=data)
is_valid, errors = service.validate()

if not is_valid:
    for error in service.get_validation_errors():
        print(f"Error: {error}")
```

**Structured errors (programmatic):**

```python
if not is_valid:
    for error in service.get_structured_errors():
        print(f"Code: {error.error_code}")
        print(f"Details: {error.details}")
        error_dict = error.to_dict()  # For API response
```

## Testing

```python
import pytest
from strata.exceptions import (
    PlatformConfigurationError,
    ServiceNotValidatedError,
    PlatformFileNotFoundError,
)

def test_initialization_error():
    with pytest.raises(PlatformConfigurationError, match="Either path or data"):
        MyService()

def test_validation_required():
    service = MyService(data=valid_data)
    with pytest.raises(ServiceNotValidatedError, match="must be validated"):
        service.get_kind()

def test_file_not_found():
    err = PlatformFileNotFoundError("/some/file.yaml", file_type="Configuration")
    assert err.error_code == "FILE_NOT_FOUND"
    assert err.details["path"] == "/some/file.yaml"
```

## Best Practices

- **Use specific exceptions:** Choose most specific class, create new types if needed
- **Provide context:** Use `details` dict with relevant info (names, paths, options)
- **Chain exceptions:** Use `cause` parameter for debugging
- **Store structured:** Add to `_structured_errors` + string to `_validation_errors`