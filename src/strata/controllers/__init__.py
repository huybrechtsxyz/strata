"""Controllers — orchestration above the per-document service layer.

A service validates *one* document. A controller coordinates many: finding
the solution root, discovering documents, indexing them by identity, and
running the cross-document checks that no single service can do alone.

Sits above `strata.services` in the layered architecture (ADR-0003), matching
v1's `controllers` layer.
"""
