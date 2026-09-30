"""Deterministic compilation (ADR-004).

A compile is a pure function of source bytes. This package holds the pieces:
segmentation, the Merkle manifest, and the compiler that joins them.
"""

from .reopen import manifest_matches_recorded, rebuild_manifest  # noqa: E402

__all__ = ["rebuild_manifest", "manifest_matches_recorded"]
