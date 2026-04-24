# Base Integration (Developer Guide)

Purpose
- `base_integration.py` defines the abstract contract for integration implementations.

Key Points
- Extend the `BaseIntegration` class to implement `is_available()`, `run()`, and any helper methods.
- Use the integration factory (`factory.py`) to obtain instances rather than direct construction.
- Integrations should be idempotent and fail-fast with clear error messages.

Registration
- Register supported integrations in `registry.py` so the factory can discover them.

Testing
- Provide unit tests that mock external systems (use `requests-mock`, `moto`, or similar).

Docs
- See the individual integration modules for environment variables and usage examples.
