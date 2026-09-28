"""Epistemic state machine (ADR-002).

Thirteen states, with legal transitions declared as data. The point is that
illegal transitions are rejected by construction rather than by convention: a
claim cannot silently go from ``UNEXAMINED`` to ``VALIDATED`` because some
caller forgot to walk it through evidence.

Two invariants are enforced here and are not negotiable:

1. **Confidence never gates a state.** No function in this module reads a
   confidence value. Confidence is a claim about the estimator; epistemic state
   is a claim about the relationship between evidence and proposition. Merging
   them is the defect that produced the v0.1 fabrication, where 23 "established
   facts" all carried the identical confidence ``0.47636587698586486``.

2. **Absence is representable.** ``UNRESOLVED`` (searched, found nothing) and
   ``INCONCLUSIVE`` (found something, it does not support a conclusion) are
   distinct states. Collapsing them makes it impossible to tell a question the
   system never asked from one it asked and failed to answer — which is the
   difference between *unknown* and *known to be unknown*.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

__all__ = [
    "EpistemicState",
    "LEGAL_TRANSITIONS",
    "TransitionError",
    "InvalidTransition",
    "MissingInvestigation",
    "check_transition",
    "validate_state",
]


class EpistemicState(str, Enum):
    """The 13-state epistemic spine."""

    #: never searched
    UNEXAMINED = "unexamined"
    #: searched, no answer exists in this artifact (requires investigation record)
    UNRESOLVED = "unresolved"
    #: evidence found, but not enough of it (magnitude is not sufficiency)
    INSUFFICIENT = "insufficient"
    #: evidence found, and it does not support a conclusion
    INCONCLUSIVE = "inconclusive"
    #: declared axiom; no evidence required, but an author is
    ASSUMED = "assumed"
    #: heuristic-derived; traces to a miner and an input fingerprint
    DERIVED = "derived"
    #: evidence bears the claim
    SUPPORTED = "supported"
    #: independently reproduced
    VALIDATED = "validated"
    #: credible evidence on both sides
    CONTESTED = "contested"
    #: direct contradicting evidence
    CONTRADICTED = "contradicted"
    #: withdrawn by its author
    RETRACTED = "retracted"
    #: replaced by a newer claim
    SUPERSEDED = "superseded"
    #: falsified
    INVALIDATED = "invalidated"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: States reachable from any state, i.e. no restriction. Used for the
#: administrative states that a claim can always be moved to by a human
#: withdrawing or superseding it.
_LEGAL: frozenset[EpistemicState] = frozenset(EpistemicState)

#: The full transition table. Deliberately explicit and small enough to read in
#: one sitting — an implicit "anything not listed is illegal" rule would be
#: simpler to write and harder to audit, and auditability is the point.
LEGAL_TRANSITIONS: Mapping[EpistemicState, frozenset[EpistemicState]] = {
    # nothing is known yet; anything is possible
    EpistemicState.UNEXAMINED: _LEGAL,
    # searched and found nothing — can be reopened if a wider search runs
    EpistemicState.UNRESOLVED: frozenset(
        {EpistemicState.UNEXAMINED} | _LEGAL - {EpistemicState.UNRESOLVED}
    ),
    # evidence exists but is thin — may accumulate, or be found insufficient
    EpistemicState.INSUFFICIENT: frozenset(
        {
            EpistemicState.SUPPORTED,
            EpistemicState.INCONCLUSIVE,
            EpistemicState.UNRESOLVED,
            EpistemicState.CONTRADICTED,
            EpistemicState.CONTESTED,
            EpistemicState.INVALIDATED,
            EpistemicState.RETRACTED,
            EpistemicState.SUPERSEDED,
        }
    ),
    # evidence found, does not support a conclusion
    EpistemicState.INCONCLUSIVE: frozenset(
        {
            EpistemicState.INSUFFICIENT,
            EpistemicState.SUPPORTED,
            EpistemicState.CONTRADICTED,
            EpistemicState.CONTESTED,
            EpistemicState.UNRESOLVED,
            EpistemicState.INVALIDATED,
            EpistemicState.RETRACTED,
            EpistemicState.SUPERSEDED,
        }
    ),
    # axioms are stable but can be falsified or withdrawn
    EpistemicState.ASSUMED: frozenset(
        {
            EpistemicState.SUPPORTED,
            EpistemicState.UNRESOLVED,
            EpistemicState.CONTRADICTED,
            EpistemicState.INVALIDATED,
            EpistemicState.RETRACTED,
            EpistemicState.SUPERSEDED,
        }
    ),
    # heuristic output is never authoritative
    EpistemicState.DERIVED: frozenset(
        {
            EpistemicState.SUPPORTED,
            EpistemicState.INSUFFICIENT,
            EpistemicState.INCONCLUSIVE,
            EpistemicState.UNRESOLVED,
            EpistemicState.CONTRADICTED,
            EpistemicState.INVALIDATED,
            EpistemicState.RETRACTED,
            EpistemicState.SUPERSEDED,
        }
    ),
    # the working states: evidence bears the claim. UNRESOLVED is reachable
    # from every evidence-bearing state because a wider search can fail to
    # reproduce support — "we believed this, and we went looking again, and it
    # is not there" is a real and common outcome.
    EpistemicState.SUPPORTED: frozenset(
        {
            EpistemicState.VALIDATED,
            EpistemicState.CONTESTED,
            EpistemicState.CONTRADICTED,
            EpistemicState.INVALIDATED,
            EpistemicState.INSUFFICIENT,
            EpistemicState.INCONCLUSIVE,
            EpistemicState.UNRESOLVED,
            EpistemicState.RETRACTED,
            EpistemicState.SUPERSEDED,
        }
    ),
    # independently reproduced — the strongest affirmative state
    EpistemicState.VALIDATED: frozenset(
        {
            EpistemicState.CONTESTED,
            EpistemicState.CONTRADICTED,
            EpistemicState.INVALIDATED,
            EpistemicState.UNRESOLVED,
            EpistemicState.RETRACTED,
            EpistemicState.SUPERSEDED,
        }
    ),
    # evidence on both sides
    EpistemicState.CONTESTED: frozenset(
        {
            EpistemicState.SUPPORTED,
            EpistemicState.VALIDATED,
            EpistemicState.CONTRADICTED,
            EpistemicState.INSUFFICIENT,
            EpistemicState.INCONCLUSIVE,
            EpistemicState.UNRESOLVED,
            EpistemicState.INVALIDATED,
            EpistemicState.RETRACTED,
            EpistemicState.SUPERSEDED,
        }
    ),
    # the negative terminal states. These are absorbing by design: a claim that
    # has been contradicted, retracted, superseded, or invalidated is a fact
    # about the record, not a question awaiting more evidence. Only
    # administrative re-opening (back to UNEXAMINED) is permitted.
    EpistemicState.CONTRADICTED: frozenset(
        {EpistemicState.UNEXAMINED, EpistemicState.INVALIDATED, EpistemicState.SUPERSEDED}
    ),
    EpistemicState.RETRACTED: frozenset({EpistemicState.UNEXAMINED, EpistemicState.SUPERSEDED}),
    EpistemicState.SUPERSEDED: frozenset({EpistemicState.UNEXAMINED}),
    EpistemicState.INVALIDATED: frozenset({EpistemicState.UNEXAMINED, EpistemicState.SUPERSEDED}),
}


#: Self-transitions are always legal — recording a re-confirmation of the same
#: state is how a claim accumulates transaction history in a bitemporal store.
for _state in EpistemicState:
    LEGAL_TRANSITIONS[_state] = LEGAL_TRANSITIONS[_state] | {_state}
del _state


class TransitionError(Exception):
    """Base class for epistemic state machine violations."""


class InvalidTransition(TransitionError):
    """An illegal state transition was attempted."""


class MissingInvestigation(TransitionError):
    """``UNRESOLVED`` was set without an investigation record.

    This is the fail-closed check that makes absence *provable* rather than
    merely asserted. A system that can mark a question unanswered without
    recording what it searched cannot distinguish a genuine absence of an answer
    from a system that simply never looked.
    """


def check_transition(
    current: EpistemicState | str,
    target: EpistemicState | str,
    *,
    has_investigation: bool = False,
) -> EpistemicState:
    """Validate a transition, returning the target state.

    Raises ``InvalidTransition`` if the edge does not exist, and
    ``MissingInvestigation`` if the target is ``UNRESOLVED`` with no
    investigation record.
    """
    cur = EpistemicState(current)
    tgt = EpistemicState(target)

    if tgt not in LEGAL_TRANSITIONS[cur]:
        raise InvalidTransition(
            f"illegal epistemic transition {cur.value} -> {tgt.value}; "
            f"legal targets: {sorted(s.value for s in LEGAL_TRANSITIONS[cur])}"
        )

    if tgt is EpistemicState.UNRESOLVED and not has_investigation:
        raise MissingInvestigation(
            f"cannot set {tgt.value} without an investigation record naming "
            "what was searched; absence must be provable, not asserted"
        )

    return tgt


def validate_state(state: EpistemicState | str) -> EpistemicState:
    """Coerce and validate a state value, raising on anything unknown."""
    try:
        return EpistemicState(state)
    except ValueError as exc:
        raise TransitionError(
            f"unknown epistemic state {state!r}; the spine is closed — new states "
            "require an ADR, not a runtime flag"
        ) from exc
