"""Crosswalk from the estate's four incompatible claim vocabularies onto the
ADR-002 spine.

Recorded explicitly rather than resolved silently. Each estate repo carries a
state enum that means something slightly different from the others, and the
overlap is not total — a mechanical rename would be wrong in at least one
direction for several terms. Where a source term has no faithful target, that
is recorded as ``None`` and requires a human decision.

This module exists so that a migration is auditable. The alternative — picking
one vocabulary and hoping — is how ``ganymede3`` ended up with two claim models
joined only by a string.
"""

from __future__ import annotations

from typing import Mapping

from .epistemic import EpistemicState

__all__ = ["CROSSWALK", "resolve", "UnmappedTerm"]

#: Upstream vocabulary -> spine. ``None`` means "no faithful target"; resolving
#: such a term raises rather than guessing.
CROSSWALK: Mapping[str, Mapping[str, EpistemicState | None]] = {
    # brief / authority-epistemic-protocol
    "aep": {
        "VERIFIED": EpistemicState.VALIDATED,
        "SUPPORTED": EpistemicState.SUPPORTED,
        "CONTRADICTED": EpistemicState.CONTRADICTED,
        "INCONCLUSIVE": EpistemicState.INCONCLUSIVE,
        "STALE": EpistemicState.SUPERSEDED,
        "UNVERIFIED": EpistemicState.UNEXAMINED,
    },
    # ganymede3 claim_graph/graph.py — 5 states
    "ganymede3-claim-graph": {
        "PROPOSED": EpistemicState.UNEXAMINED,
        "VERIFIED": EpistemicState.VALIDATED,
        "CONTRADICTED": EpistemicState.CONTRADICTED,
        "DEPRECATED": EpistemicState.SUPERSEDED,
        # MERGED is a graph operation on two claims, not an epistemic state
        "MERGED": None,
    },
    # hermes-atlas lifecycle
    "hermes-atlas": {
        "candidate": EpistemicState.UNEXAMINED,
        "supported": EpistemicState.SUPPORTED,
        "validated": EpistemicState.VALIDATED,
        "contested": EpistemicState.CONTESTED,
        "invalidated": EpistemicState.INVALIDATED,
        "superseded": EpistemicState.SUPERSEDED,
    },
    # sovereign-intelligence hypothesis lifecycle
    "sovereign-intelligence": {
        "ACTIVE": EpistemicState.UNEXAMINED,
        "WEAKENED": EpistemicState.INCONCLUSIVE,
        "CONFIRMED": EpistemicState.VALIDATED,
        "FALSIFIED": EpistemicState.INVALIDATED,
        "DEPRECATED": EpistemicState.SUPERSEDED,
    },
}


class UnmappedTerm(KeyError):
    """An upstream term has no faithful target on the spine."""


def resolve(source: str, term: str) -> EpistemicState:
    """Map an upstream term onto the spine.

    Raises ``UnmappedTerm`` for an unknown source or an unmapped term, so a
    migration can never silently drop a state.
    """
    table = CROSSWALK.get(source)
    if table is None:
        raise UnmappedTerm(
            f"unknown source vocabulary {source!r}; known: {sorted(CROSSWALK)}"
        )
    if term not in table:
        raise UnmappedTerm(
            f"unknown term {term!r} in source {source!r}; known: {sorted(table)}"
        )
    target = table[term]
    if target is None:
        raise UnmappedTerm(
            f"term {term!r} in source {source!r} has no faithful target on the "
            f"{len(EpistemicState)}-state spine — it is a graph operation, not an "
            "epistemic state, and needs an explicit migration decision"
        )
    return target
