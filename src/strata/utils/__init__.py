"""Strata v2 utilities package.

Sits below `strata.models` in the layered architecture (ADR-0003) — pure,
model-independent code (no Pydantic dependency) that multiple model files
reuse. `models` may import from `utils`; `utils` never imports from `models`.
"""
