# Capabilities Integration

Purpose
- `capabilities.py` enumerates and exposes runtime capabilities (which integrations are available).

Usage
- Use `capabilities.is_available('docker')` or similar helper to gate features.

Implementation Notes
- Keep capability probes cheap — prefer lightweight checks (binary in PATH, simple API ping).
- Cache probe results for a short time to avoid repeated network calls.

Docs
- Check `capabilities.py` for available capability names and probe functions.
