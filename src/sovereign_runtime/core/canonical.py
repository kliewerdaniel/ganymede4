"""RFC 8785 JSON Canonicalization Scheme (JCS).

Content addressing is only trustworthy if serialization is canonical: the same
logical value must produce the same bytes on every platform, in every process,
in every language that implements the artifact format. Python's ``json.dumps``
does not do this — it preserves insertion order, and it emits non-ASCII as
``\\uXXXX`` escapes by default.

JCS is the cross-language answer: sort object keys by UTF-16 code unit, no
insignificant whitespace, UTF-8 output, and ECMAScript-compatible number
formatting. Implementations in TypeScript, Rust, and Go exist, which is what
makes the artifact hash portable rather than Python-specific.

Reference: https://www.rfc-editor.org/rfc/rfc8785
"""

from __future__ import annotations

import json
import math
from typing import Any

__all__ = ["canonicalize", "canonical_bytes", "JCSError"]


class JCSError(ValueError):
    """Raised when a value cannot be canonicalized.

    Fails loud and early. A silently-degraded canonicalization would produce
    content hashes that differ between implementations, which is worse than no
    hash at all: it looks like integrity protection while providing none.
    """


def _check_number(value: float) -> float:
    """Reject numbers JSON cannot represent exactly.

    JCS delegates to ECMAScript ``Number`` (IEEE-754 double). Python's ``int``
    is arbitrary-precision, so a value above 2**53 round-trips lossily. NaN and
    infinity are not JSON at all. These are rejected rather than coerced, because
    a hash over a rounded value is a hash over a lie.
    """
    if math.isnan(value) or math.isinf(value):
        raise JCSError(f"non-finite numbers are not canonicalizable: {value!r}")
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        raise JCSError(f"integer exceeds IEEE-754 double precision: {value}")
    return value


def _sort_key(key: Any) -> tuple[int, ...]:
    """Sort object keys by UTF-16 code unit, per RFC 8785 §3.2.3.

    Python compares ``str`` by code *point*. These orders differ for characters
    above the BMP: a surrogate pair (U+1F600) is two UTF-16 units but one code
    point, so code-point sorting can order it before U+E000 while UTF-16 sorting
    places it after. Encoding to ``utf-16-be`` and comparing the resulting bytes
    reproduces the UTF-16 code-unit order exactly.

    The type check happens *here*, not in the caller, because the sort runs
    before serialization: a non-string key must fail as a ``JCSError``, not as
    an ``AttributeError`` from deep inside the comparator.
    """
    if not isinstance(key, str):
        raise JCSError(f"object keys must be strings, got {type(key).__name__}")
    return tuple(key.encode("utf-16-be"))


def _serialize(value: Any, out: list[str], depth: int = 0) -> None:
    if depth > 100:
        raise JCSError("excessive nesting depth")

    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        # ensure_ascii=False keeps non-ASCII as UTF-8 rather than \uXXXX escapes.
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, (int, float)):
        _check_number(value)
        if isinstance(value, float) and value.is_integer():
            out.append(str(int(value)))
        else:
            out.append(repr(value))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _serialize(item, out, depth + 1)
        out.append("]")
    elif isinstance(value, dict):
        out.append("{")
        for i, key in enumerate(sorted(value, key=_sort_key)):
            if i:
                out.append(",")
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _serialize(value[key], out, depth + 1)
        out.append("}")
    else:
        raise JCSError(f"type is not canonicalizable: {type(value).__name__}")


def canonicalize(value: Any) -> str:
    """Return the RFC 8785 canonical JSON text for ``value``."""
    out: list[str] = []
    _serialize(value, out)
    return "".join(out)


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical UTF-8 bytes for ``value``."""
    return canonicalize(value).encode("utf-8")
