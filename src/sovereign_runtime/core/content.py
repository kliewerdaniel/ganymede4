"""Content addressing.

A record's identity is the hash of its canonical bytes. This makes idempotence
a property of the schema rather than a rule someone must remember: inserting an
identical record twice cannot produce a second row, because the primary key is
the content.

``VOLATILE_FIELDS`` is the exclusion list. Timestamps and run metadata must not
participate in a content hash, or every recompile produces a new identity and
determinism is unachievable. This is the discipline lifted from
``ganymede3/engine/store_evidence.py``, where the hash deliberately excludes
``_meta``, ``created_at``, ``updated_at``, ``content_hash``, and ``fetched_at``.

The distinction this module preserves: **identity** (what the record says) is
hashed; **provenance** (when it was made, by what run) is stored beside it but
never hashed.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from .canonical import canonical_bytes

__all__ = [
    "VOLATILE_FIELDS",
    "digest",
    "content_id",
    "strip_volatile",
    "fingerprint",
]

#: Fields excluded from every content hash. Timestamps and run metadata describe
#: *when a record was produced*, not *what it says*.
VOLATILE_FIELDS: frozenset[str] = frozenset(
    {
        "_meta",
        "created_at",
        "updated_at",
        "content_hash",
        "fetched_at",
        "compiled_at",
        "run_id",
        "compiler_version",
        "policy_version",
    }
)

_DOMAIN = "sovereign-runtime/v1"


def strip_volatile(value: Any) -> Any:
    """Recursively drop ``VOLATILE_FIELDS`` keys from a nested structure.

    Recursive because provenance metadata is nested — a record's ``_meta`` block
    carries its own timestamps, and a shallow strip would leak them.
    """
    if isinstance(value, Mapping):
        return {k: strip_volatile(v) for k, v in value.items() if k not in VOLATILE_FIELDS}
    if isinstance(value, (list, tuple)):
        return [strip_volatile(v) for v in value]
    return value


def digest(data: bytes) -> str:
    """Raw sha256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def content_id(record: Mapping[str, Any], *, prefix: str = "") -> str:
    """Return the content-addressed id for ``record``.

    The hash covers the canonical form of the record with volatile fields
    removed. A domain-separation prefix is hashed in so that an id from one
    namespace can never collide with an id from another — a claim and an
    evidence span with identical field names must not be interchangeable.
    """
    payload = canonical_bytes(strip_volatile(dict(record)))
    h = hashlib.sha256()
    h.update(_DOMAIN.encode("utf-8"))
    h.update(b"\x00")
    h.update(payload)
    hexdigest = h.hexdigest()
    return f"{prefix}{hexdigest}" if prefix else hexdigest


def fingerprint(parts: Iterable[str]) -> str:
    """Order-independent fingerprint over a set of ids.

    Used for corpus-level identity: a corpus fingerprint must not depend on the
    order records happened to be discovered in, only on the set of them.
    """
    h = hashlib.sha256()
    for part in sorted(parts):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()
