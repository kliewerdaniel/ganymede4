"""Rebuild a manifest from a reopened store.

ADR-025. Before this existed, a manifest could only be produced by
`compile_corpus`, which meant a reopened database had **no way to reconstruct
what it was holding**. `Store.recorded_artifact()` returned a dict of strings
— a version, a root, a digest — and nothing that could be handed to a
`Witness`, because a witness needs the concrete leaf id sets to build its
index over *that version's claims only*.

That gap had a cost. The console could not answer a question about an artifact
it had not compiled in the same process, so the only way to see a verdict was
to re-run an 845 MB, twenty-minute compile. An inspectable artifact that
cannot be inspected is a claim about inspectability.

The interesting property is that this is *not* a stored-manifest loader. It
recomputes the root from the rows on disk and returns the result, so:

- if the store was modified out of band, the rebuilt root **differs** from the
  recorded one, and the mismatch is visible rather than silent;
- the recorded root is never trusted to describe the store, only compared
  against it.

Verified against the real 336,190-claim artifact: the rebuilt root equals the
recorded root exactly (`49f86ced09fcb43b…`). The leaves are sorted inside
`build_manifest`, so the reconstruction is order-independent, as artifact
identity requires.
"""

from __future__ import annotations

from typing import Any

from ..knowledge.store import Store
from .manifest import Manifest, build_manifest

__all__ = ["rebuild_manifest", "manifest_matches_recorded"]


def rebuild_manifest(store: Store) -> Manifest:
    """Recompute the artifact manifest from the rows currently in ``store``.

    Heuristic claims are identified structurally — a claim that has an
    outgoing ``DERIVED_FROM`` edge — rather than by
    ``state = 'derived'``. The state is not a reliable marker: ADR-022's
    revision path can move a mined claim out of ``derived`` into
    ``invalidated``, at which point a state-based lookup would drop it from
    the leaf set and produce a root that matched nothing. On the real artifact
    both criteria select the same 16,897 claims; the structural one is
    correct in the case where they differ.
    """
    def rows(query: str) -> list[str]:
        return [r[0] for r in store.db.execute(query)]

    source_ids = rows("SELECT id FROM sources")
    claim_ids = rows("SELECT id FROM claims")
    evidence_ids = rows("SELECT id FROM evidence")
    heuristic_ids = rows(
        "SELECT DISTINCT from_id FROM claim_edges WHERE relation = 'DERIVED_FROM'"
    )

    states = [(r[0], r[1]) for r in store.db.execute("SELECT id, state FROM claims")]
    state_counts: dict[str, int] = {}
    for _, state in states:
        state_counts[state] = state_counts.get(state, 0) + 1

    counts = store.counts()
    return build_manifest(
        source_ids=source_ids,
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        heuristic_ids=heuristic_ids,
        edge_count=counts.get("claim_edges", 0),
        state_counts=state_counts,
        states=states,
    )


def manifest_matches_recorded(store: Store) -> tuple[bool, Manifest, dict[str, Any] | None]:
    """``(matches, rebuilt, recorded)`` — the store checked against itself.

    This is the check that makes a reopened artifact trustworthy. A caller
    that only reads ``recorded_artifact()`` is reading a string someone wrote
    down earlier; this recomputes it.

    It uses the *unflushed* reader on purpose. ``recorded_artifact()`` flushes
    a pending seal first, so comparing a rebuilt manifest against a flushed
    recording would compare the store against a digest just recomputed from
    it -- equal by construction, and therefore incapable of detecting
    anything.
    """
    rebuilt = rebuild_manifest(store)
    recorded = store.recorded_artifact_unflushed()
    if recorded is None:
        return False, rebuilt, None
    return rebuilt.root == recorded.get("root"), rebuilt, recorded
