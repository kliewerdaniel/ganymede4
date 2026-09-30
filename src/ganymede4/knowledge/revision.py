"""Belief revision: a retracted claim takes its dependents with it (ADR-017).

The README advertised ``belief revision -> next version`` for eleven phases
before this module existed. The gap was not cosmetic: a ``DERIVED`` heuristic
rests on the claims that produced it, and when one of those is retracted the
heuristic does not get weaker -- it becomes unsupported, and nothing noticed.

This is a deterministic, bounded, transitive closure over claim dependencies,
driven by the existing ``LEGAL_TRANSITIONS`` table. It is not a model and it
never reasons about meaning. It also only ever moves claims *down*: revision
can invalidate a dependent but can never resurrect a retracted claim or promote
anything back to ``SUPPORTED``. A new belief comes from a new compile, which
produces a new artifact version.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .epistemic import EpistemicState, check_transition
from .store import Store

__all__ = [
    "TERMINAL_NEGATIVE",
    "RevisionError",
    "Revision",
    "Reviser",
]


class RevisionError(Exception):
    """Revision was asked to do something it cannot justify.

    Separate from ``TransitionError`` because the transition table is not the
    problem: a refusal here means the *request* was wrong (revising a claim
    that is not terminal, or a dependency that cannot be read), not that some
    edge is illegal.
    """


#: States from which a dependent inherits invalidation. A claim in one of
#: these no longer supports anything resting on it. ``CONTRADICTED`` is
#: deliberately *absent*: a contested claim is still a claim, and
#: contradiction is a finding the evaluator records rather than a retraction.
#: Including it would invalidate half the corpus the moment any peer group
#: formed.
TERMINAL_NEGATIVE: frozenset[EpistemicState] = frozenset(
    {
        EpistemicState.RETRACTED,
        EpistemicState.INVALIDATED,
        EpistemicState.SUPERSEDED,
    }
)


@dataclass(frozen=True)
class Revision:
    """One revision event: what was retracted, and what fell with it."""

    trigger_id: str
    trigger_state: EpistemicState
    #: Claims invalidated by this revision, in breadth-first discovery order.
    invalidated: tuple[str, ...] = ()
    #: Dependents that could not legally be moved to INVALIDATED. The revision
    #: is refused for these rather than completed quietly over them -- see
    #: ADR-017, "fails closed".
    refused: tuple[str, ...] = ()
    version: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "trigger_id": self.trigger_id,
            "trigger_state": self.trigger_state.value,
            "invalidated": list(self.invalidated),
            "refused": list(self.refused),
            "version": self.version,
        }


class Reviser:
    """Propagates a retraction through the claim dependency graph.

    Constructed over a store and a manifest, so -- like the evaluator and the
    witness -- it is bound to one artifact version rather than to whatever
    happens to be in the database.

    Dependencies are read from the evaluation records, which are
    authoritative, and from the ``DERIVED_FROM`` edges the compiler writes.
    An ``ATTESTED_BY`` edge is deliberately *not* invented alongside them: two
    sources of truth for the same relation is a way to disagree with itself.
    """

    def __init__(
        self,
        store: Store,
        version: str,
        *,
        decided_at: str = "1970-01-01T00:00:00Z",
    ) -> None:
        self._store = store
        self._version = version
        #: When the revision decided. Recorded on every evaluation this
        #: walk writes (ADR-022), so the decision is dated rather than
        #: inheriting a timestamp from wherever the caller happened to
        #: be. The default is the epoch the evaluator also uses, keeping
        #: bulk operations deterministic unless a caller opts in.
        self._decided_at = decided_at
        self._dependents: Mapping[str, tuple[str, ...]] | None = None

    # -- dependency graph -------------------------------------------------

    def _graph(self) -> Mapping[str, tuple[str, ...]]:
        """Map each claim to the claims that depend on it (reverse edges).

        Built once and cached, because revision over the real corpus walks
        this on every call and rebuilding it per event was a quadratic
        mistake waiting to happen -- the same shape of mistake ADR-011 and
        ADR-014 each had to undo elsewhere.
        """
        if self._dependents is not None:
            return self._dependents

        edges: dict[str, list[str]] = {}
        db = self._store.db

        # Derived heuristics rest on the claims that produced them.
        for dependent, support in db.execute(
            "SELECT from_id, to_id FROM claim_edges WHERE relation = 'DERIVED_FROM'"
        ):
            edges.setdefault(support, []).append(dependent)

        # A supported claim rests on the claims that attested it. The
        # evaluation record is the authority here; peer_groups is only a
        # content-addressed dedup of the same sets.
        #
        # Relation values are the enum's lowercase values ('attested'), which
        # is why this is not spelled ATTESTED.
        for subject_id, attested_by in db.execute(
            "SELECT subject_id, attested_by FROM evaluations "
            "WHERE relation = 'attested' AND attested_by IS NOT NULL "
            "AND attested_by != ''"
        ):
            for support in self._decode_group(subject_id, attested_by):
                edges.setdefault(support, []).append(subject_id)

        self._dependents = {k: tuple(sorted(set(v))) for k, v in edges.items()}
        return self._dependents

    def _decode_group(self, subject_id: str, attested_by: str) -> tuple[str, ...]:
        """Resolve an ``attested_by`` cell to member claim ids.

        Always a group id: ``add_evaluation`` runs every attestation set
        through ``_put_peer_group``, so the cell holds an ``agr-`` content id
        or the empty string, never a bare claim id. Decoding it any other way
        would treat a group id as a claim that does not exist.

        ``_peer_members`` raises on an unresolvable group rather than
        returning empty, which is the behaviour we want: an empty list would
        make a SUPPORTED claim read as unattested, and a claim that quietly
        lost its evidence is worse than a crash.
        """
        if not attested_by:
            return ()
        try:
            return tuple(self._store._peer_members(attested_by))
        except Exception as exc:
            raise RevisionError(
                f"claim {subject_id} attests via group {attested_by}, which does "
                f"not resolve ({exc}); refusing to revise over an unreadable "
                f"dependency"
            ) from exc

    def dependents(self, claim_id: str) -> tuple[str, ...]:
        """The claims that rest directly on ``claim_id``."""
        return self._graph().get(claim_id, ())

    # -- the closure ------------------------------------------------------

    def revise(self, trigger_id: str) -> Revision:
        """Invalidate everything that transitively depends on a terminal claim.

        Idempotent: revising an already-revised claim reports an empty
        closure rather than raising, because a retried revision must not be
        able to fail a run that has already succeeded.
        """
        row = self._store.db.execute(
            "SELECT state FROM claims WHERE id = ?", (trigger_id,)
        ).fetchone()
        if row is None:
            raise RevisionError(f"cannot revise: no such claim {trigger_id}")

        trigger_state = EpistemicState(row["state"])
        if trigger_state not in TERMINAL_NEGATIVE:
            raise RevisionError(
                f"cannot revise {trigger_id}: it is {trigger_state.value}, which "
                f"is not terminal. Revision propagates retractions, not "
                f"contestations -- a claim in {trigger_state.value} still "
                f"supports whatever rests on it."
            )

        graph = self._graph()
        seen: set[str] = {trigger_id}
        queue: deque[str] = deque([trigger_id])
        invalidated: list[str] = []
        refused: list[str] = []

        while queue:
            current = queue.popleft()
            for dependent in graph.get(current, ()):
                if dependent in seen:
                    continue
                seen.add(dependent)
                outcome = self._invalidate(dependent)
                if outcome == "moved":
                    invalidated.append(dependent)
                    # ADR-022: record *why* this claim fell, naming the claim
                    # it fell from. ADR-006 requires an evaluation behind
                    # every state change, and without this the independent
                    # auditor reports the move as both an orphan and
                    # `state_without_evaluation`.
                    #
                    # Found on the real corpus: all 16,897 DERIVED heuristics
                    # have zero evaluations, which is legal only because the
                    # auditor exempts the `derived` state. The moment
                    # revision moves one, the exemption stops applying and
                    # the claim is unexplained. This is the sixth occurrence
                    # of the shape and the sharpest -- ADR-014/018/020/021
                    # were all values that went *stale*; this one was never
                    # *written*.
                    self._store.add_evaluation(
                        subject_id=dependent,
                        relation="invalidated",
                        method="reviser.propagate",
                        decided_at=self._decided_at,
                        meta={
                            "trigger": trigger_id,
                            "invalidated_from": current,
                            "trigger_state": trigger_state.value,
                        },
                    )
                    # Only a claim this call actually moved can propagate
                    # further; an already-invalid one was handled when it
                    # first fell.
                    queue.append(dependent)
                elif outcome == "refused":
                    refused.append(dependent)

        # ADR-022: this is the fifth-occurrence shape. `Reviser` predates
        # ADR-018 and ADR-021 and predates `set_state` as the single funnel
        # for belief changes, so it invalidates with a raw UPDATE and was
        # missing *both* invariants: the epoch never moved and the recorded
        # state_digest still described the pre-revision beliefs. Verified on
        # the real 336,190-claim artifact -- one revision left the store
        # failing its own audit, and the witness tripwire did not fire.
        #
        # The re-seal goes here, at the end of the walk, rather than in each
        # `_invalidate`. Revision can move a transitive closure of ~142,000
        # claims, and a re-seal per claim is a full store scan each time. The
        # invariant that matters is "the digest describes the beliefs this
        # call left behind", and one seal at the end establishes exactly
        # that.
        #
        # The epoch is bumped for the same reason and by the same argument:
        # the tripwire's question is "did anything move", and the honest
        # answer after N moves is one bump, not N. `set_state` bumps per
        # transition because it is a single claim's transition; this is a
        # bulk operation and the counter is a tripwire, not a ledger.
        if invalidated:
            self._store.bump_state_epoch()
            self._store.reseal()

        return Revision(
            trigger_id=trigger_id,
            trigger_state=trigger_state,
            invalidated=tuple(invalidated),
            refused=tuple(refused),
            version=self._version,
        )

    def _invalidate(self, claim_id: str) -> str:
        """Move one claim to INVALIDATED. Returns what happened.

        Three outcomes rather than a boolean, because "I moved it", "it was
        already invalid", and "I could not legally move it" are different
        facts and collapsing them makes a retried revision claim credit for
        work a previous run already did.

        The refusal is the fail-closed half of the rule. A dependent that
        cannot legally be invalidated keeps its state, but it is reported in
        ``Revision.refused`` -- never dropped, because a silently skipped
        dependent is the same stale-belief defect one level up.
        """
        db = self._store.db
        row = db.execute("SELECT state FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if row is None:
            return "refused"
        current = EpistemicState(row["state"])
        if current is EpistemicState.INVALIDATED:
            return "already"  # nothing to do, but still correctly invalid
        try:
            check_transition(current, EpistemicState.INVALIDATED)
        except Exception:
            return "refused"
        db.execute(
            "UPDATE claims SET state = ? WHERE id = ?",
            (EpistemicState.INVALIDATED.value, claim_id),
        )
        return "moved"

    def audit_dangling(self) -> tuple[str, ...]:
        """Claims still resting on something that was invalidated.

        The check the independent auditor performs, exposed here so a test can
        assert the invariant and the auditor can re-derive it without importing
        this package. Returns claim ids in the order found.
        """
        db = self._store.db
        found: list[str] = []
        for support, dependents in self._graph().items():
            row = db.execute("SELECT state FROM claims WHERE id = ?", (support,)).fetchone()
            if row is None or EpistemicState(row["state"]) not in TERMINAL_NEGATIVE:
                continue
            for dependent in dependents:
                drow = db.execute(
                    "SELECT state FROM claims WHERE id = ?", (dependent,)
                ).fetchone()
                if drow is None:
                    continue
                if EpistemicState(drow["state"]) in (
                    EpistemicState.SUPPORTED,
                    EpistemicState.DERIVED,
                    EpistemicState.VALIDATED,
                ):
                    found.append(dependent)
        return tuple(found)
