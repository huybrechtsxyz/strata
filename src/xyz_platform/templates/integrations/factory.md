# Integration Factory

Purpose
- `factory.py` provides `IntegrationFactory.create(config)` to instantiate integrations.

Usage
- Call `IntegrationFactory.create(config)` with the integration config (type, credentials, options).
- The factory resolves the correct integration class from `registry.py`.

Extensibility
- To add a new integration: implement the class, register it in `registry.py`, and ensure the factory can construct it.

Docs
- See `factory.py` and `registry.py` for wiring examples.
