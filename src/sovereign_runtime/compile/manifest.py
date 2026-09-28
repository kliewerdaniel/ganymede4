"""The artifact manifest and its Merkle root (ADR-004 §4).

An artifact version is identified by a **Merkle root over sorted content-addressed
leaves**. It is deliberately not identified by a timestamp, a run counter, or an
ingestion order. Three properties follow, and all three are load-bearing:

1. **A recompile that changes nothing is not a new version.** If the content is
   the same, the version is the same. Version numbers that increment on every run
   train reviewers to ignore them, which destroys the signal the version exists
   to carry.

2. **Ingestion order cannot change the version.** Leaves are sorted before
   hashing, so a corpus compiled in a different order is the same artifact. This
   is what makes "reproducible" mean reproducible rather than "same if you were
   lucky".

3. **A diff is computable.** Because each leaf is a content hash, comparing two
   versions is a set difference of leaves, and the root is verifiable against any
   independently held leaf set.

The manifest carries *provenance* — when it was compiled, by which run, from
which inputs — that is **excluded from the root**. This is the same
identity-versus-provenance split as ``core/content.py``, lifted from the record
to the artifact. If the timestamp were hashed, no two compiles could ever agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..core.canonical import canonical_bytes
from ..core.content import digest, fingerprint

__all__ = ["Manifest", "merkle_root", "MANIFEST_SCHEMA_VERSION"]

#: Bumped when the manifest's *meaning* changes. A different version produces
#: a different root even for identical leaves, so roots are comparable only
#: within a schema version.
MANIFEST_SCHEMA_VERSION = 1

_DOMAIN = b"sovereign-runtime/manifest/v1"
_LEAF_DOMAIN = b"sovereign-runtime/leaf/v1"


def _leaf_hash(content_id: str) -> bytes:
    h = _LEAF_DOMAIN + b"\x00" + content_id.encode("utf-8")
    return bytes.fromhex(digest(h)[:64])  # first 32 bytes


def merkle_root(leaf_ids: Iterable[str]) -> str:
    """Compute the Merkle root over a set of content ids.

    Order-independent by construction: leaves are sorted and de-duplicated
    before the tree is built, so the caller cannot perturb the result by
    iterating in a different order or passing a duplicate.

    Odd nodes at any level are promoted unchanged rather than duplicated. This
    differs from Bitcoin's convention and the difference is deliberate: it
    means the tree shape is a function of the *leaf count* alone, so two runs
    with the same leaves always produce the same internal structure and a
    hand-verified root in a document can be reproduced.
    """
    leaves = sorted({_leaf_hash(cid) for cid in leaf_ids})
    if not leaves:
        # The empty corpus is a real case, not an error. It gets a
        # domain-separated constant so an empty artifact is distinguishable
        # from a missing one.
        return digest(_DOMAIN + b"\x00empty")

    level = leaves
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            nxt.append(bytes.fromhex(digest(_DOMAIN + left + right)[:64]))
        level = nxt
    return level[0].hex()


@dataclass(frozen=True)
class Manifest:
    """The identity and provenance of one compiled artifact.

    ``root`` is the version. Everything else is either an input to it or a
    record about how it was produced, and those two categories must never be
    confused.
    """

    root: str
    schema_version: int
    source_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    heuristic_ids: tuple[str, ...] = ()
    edge_count: int = 0
    state_counts: Mapping[str, int] = field(default_factory=dict)
    #: Deliberately outside the root.
    compiled_at: str | None = None
    run_id: str | None = None
    corpus_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the manifest, separating rooted and provenance fields."""
        return {
            "root": self.root,
            "schema_version": self.schema_version,
            "rooted": {
                "source_ids": list(self.source_ids),
                "claim_ids": list(self.claim_ids),
                "evidence_ids": list(self.evidence_ids),
                "heuristic_ids": list(self.heuristic_ids),
                "edge_count": self.edge_count,
                "state_counts": dict(self.state_counts),
            },
            "provenance": {
                "compiled_at": self.compiled_at,
                "run_id": self.run_id,
                "corpus_fingerprint": self.corpus_fingerprint,
            },
        }

    def to_bytes(self) -> bytes:
        """Canonical bytes for hashing or signature."""
        return canonical_bytes(self.to_dict())

    @property
    def version(self) -> str:
        """The artifact version string handed to a witness."""
        return f"v1-{self.root[:16]}"


def build_manifest(
    *,
    source_ids: Sequence[str],
    claim_ids: Sequence[str],
    evidence_ids: Sequence[str],
    heuristic_ids: Sequence[str] = (),
    edge_count: int = 0,
    state_counts: Mapping[str, int] | None = None,
    compiled_at: str | None = None,
    run_id: str | None = None,
) -> Manifest:
    """Assemble a manifest, computing the root over every content leaf.

    ``edge_count`` participates in the root. Claim ids alone do not capture
    relationships: two artifacts with identical claim sets but different
    SUPPORTED/CONTRADICTS edges are different artifacts, and a version id that
    could not tell them apart would be lying about what it identifies.
    """
    all_leaves: list[str] = [*source_ids, *claim_ids, *evidence_ids, *heuristic_ids]
    return Manifest(
        # schema_version is a leaf too: the same records under a different
        # manifest schema are a different artifact, and a root that ignored it
        # would make two incompatible versions compare as equal.
        root=merkle_root([f"schema:{MANIFEST_SCHEMA_VERSION}", f"edges:{edge_count}", *all_leaves]),
        schema_version=MANIFEST_SCHEMA_VERSION,
        source_ids=tuple(sorted(source_ids)),
        claim_ids=tuple(sorted(claim_ids)),
        evidence_ids=tuple(sorted(evidence_ids)),
        heuristic_ids=tuple(sorted(heuristic_ids)),
        edge_count=edge_count,
        state_counts=dict(state_counts or {}),
        compiled_at=compiled_at,
        run_id=run_id,
        corpus_fingerprint=fingerprint(all_leaves),
    )
