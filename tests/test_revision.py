"""Belief revision (ADR-017).

The tests below are the ADR's claims, one test each. A test that cannot fail
on a specific property of the decision is not testing the decision.
"""

from __future__ import annotations

import pytest

from ganymede4.knowledge.epistemic import EpistemicState
from ganymede4.knowledge.evaluator import Relation
from ganymede4.knowledge.revision import (
    TERMINAL_NEGATIVE,
    RevisionError,
    Reviser,
)
from ganymede4.knowledge.store import Store


def _store_with_claim(store: Store, text: str, state: EpistemicState, **kw):
    """Add a claim and force it to ``state`` via legal intermediate steps.

    The transition table is enforced on the way in, so a test cannot simply
    declare a claim RETRACTED -- it has to walk there, which is the point.
    """
    content = text + "." + "padding " * 20
    src = store.add_source(uri="test://a", source_type="test", content=content)
    ev = store.add_evidence(
        source_id=src, start_offset=0, end_offset=len(content), text=content
    )
    claim = store.add_claim(
        text=text, evidence_ids=[ev], transaction_time="t0", **kw
    )
    if state is not EpistemicState.UNEXAMINED:
        store.set_state(claim, state, transaction_time="t0")
    return claim


def _attest(store: Store, subject_id: str, by: list[str]) -> None:
    """Record an ATTESTED evaluation and move the claim to SUPPORTED."""
    store.add_evaluation(
        subject_id=subject_id,
        relation=Relation.ATTESTED.value,
        method="test",
        attested_by=by,
        decided_at="t1",
    )
    store.set_state(subject_id, EpistemicState.SUPPORTED, transaction_time="t1")


# -- the trigger must actually be terminal --------------------------------


def test_revising_a_nonterminal_claim_is_refused():
    """A SUPPORTED claim still supports what rests on it.

    This is the load-bearing refusal. If revision accepted any state, a
    contested claim would silently strip its dependents, and a corpus with
    646 contradictions would lose a large part of itself.
    """
    store = Store()
    claim = _store_with_claim(store, "a claim that supports another one", EpistemicState.SUPPORTED)
    reviser = Reviser(store, "v1-test")

    with pytest.raises(RevisionError, match="not terminal"):
        reviser.revise(claim)


def test_revising_an_unknown_claim_is_refused():
    store = Store()
    reviser = Reviser(store, "v1-test")
    with pytest.raises(RevisionError, match="no such claim"):
        reviser.revise("clm-00000000000000000")


@pytest.mark.parametrize("state", sorted(TERMINAL_NEGATIVE, key=lambda s: s.value))
def test_every_terminal_state_can_be_a_trigger(state):
    """RETRACTED, INVALIDATED and SUPERSEDED all propagate."""
    store = Store()
    claim = _store_with_claim(store, "a claim that was withdrawn by the author", state)
    revision = Reviser(store, "v1-test").revise(claim)
    assert revision.trigger_state is state


# -- the closure is transitive --------------------------------------------


def test_revision_is_transitive_through_derived_claims():
    """support -> heuristic -> heuristic-over-heuristic all fall.

    One level of propagation leaves the bug one level deep, which is why
    ADR-017 rejects it explicitly.
    """
    store = Store()
    base = _store_with_claim(store, "the original claim that was later retracted", EpistemicState.RETRACTED)
    mid = _store_with_claim(store, "a heuristic drawn from the original claim here", EpistemicState.DERIVED)
    top = _store_with_claim(store, "a heuristic drawn from the heuristic above", EpistemicState.DERIVED)
    store.add_edge(mid, base, "DERIVED_FROM")
    store.add_edge(top, mid, "DERIVED_FROM")

    revision = Reviser(store, "v1-test").revise(base)

    assert set(revision.invalidated) == {mid, top}
    assert revision.refused == ()
    row = store.db.execute("SELECT state FROM claims WHERE id = ?", (top,)).fetchone()
    assert row["state"] == EpistemicState.INVALIDATED.value


def test_revision_propagates_through_attestation():
    """A SUPPORTED claim resting on a retracted claim is invalidated."""
    store = Store()
    base = _store_with_claim(store, "the claim that supported everything downstream", EpistemicState.RETRACTED)
    supported = _store_with_claim(store, "a claim that was supported by the one above", EpistemicState.INCONCLUSIVE)
    _attest(store, supported, [base])

    revision = Reviser(store, "v1-test").revise(base)

    assert supported in revision.invalidated
    row = store.db.execute("SELECT state FROM claims WHERE id = ?", (supported,)).fetchone()
    assert row["state"] == EpistemicState.INVALIDATED.value


def test_a_multi_member_attestation_group_propagates_from_every_member():
    """A group id resolves to all its members, not just one.

    Regression guard for the decoder: attested_by is always a content-
    addressed group id, never a bare claim id. Treating it as a claim id
    would silently find no dependencies at all.
    """
    store = Store()
    a = _store_with_claim(store, "first supporting claim of the attested set", EpistemicState.RETRACTED)
    b = _store_with_claim(store, "second supporting claim of the attested set", EpistemicState.RETRACTED)
    supported = _store_with_claim(store, "the claim that both of the above support", EpistemicState.INCONCLUSIVE)
    _attest(store, supported, [a, b])

    # Revising the SECOND member must also propagate, which it cannot do if
    # only the first member of the group was indexed.
    revision = Reviser(store, "v1-test").revise(b)

    assert supported in revision.invalidated


# -- it never promotes ----------------------------------------------------


def test_revision_never_resurrects_a_claim():
    """INVALIDATED is not a way back to SUPPORTED.

    Revision only moves down. A new belief comes from a new compile, which
    produces a new artifact version.
    """
    store = Store()
    base = _store_with_claim(store, "the original claim that was retracted", EpistemicState.RETRACTED)
    dep = _store_with_claim(store, "a dependent claim that fell with it", EpistemicState.DERIVED)
    store.add_edge(dep, base, "DERIVED_FROM")

    Reviser(store, "v1-test").revise(base)

    row = store.db.execute("SELECT state FROM claims WHERE id = ?", (dep,)).fetchone()
    assert row["state"] == EpistemicState.INVALIDATED.value
    # And there is no API to move it back up.
    with pytest.raises(Exception):
        store.set_state(dep, EpistemicState.SUPPORTED, transaction_time="t2")


def test_revision_is_idempotent():
    """Revising twice reports an empty closure instead of raising.

    A retried revision must not be able to fail a run that already succeeded.
    """
    store = Store()
    base = _store_with_claim(store, "the original claim that was retracted", EpistemicState.RETRACTED)
    dep = _store_with_claim(store, "a dependent claim that fell with it", EpistemicState.DERIVED)
    store.add_edge(dep, base, "DERIVED_FROM")

    first = Reviser(store, "v1-test").revise(base)
    second = Reviser(store, "v1-test").revise(base)

    assert first.invalidated == (dep,)
    assert second.invalidated == ()
    assert second.refused == ()


# -- cycles must not hang -------------------------------------------------


def test_a_dependency_cycle_terminates():
    """A malformed corpus must not hang the closure.

    The seen-set makes the walk linear, but a cycle in a hand-edited or
    corrupted store is exactly the case where that has to be proven rather
    than assumed.
    """
    store = Store()
    a = _store_with_claim(store, "the first claim in a dependency cycle", EpistemicState.RETRACTED)
    b = _store_with_claim(store, "the second claim in a dependency cycle", EpistemicState.DERIVED)
    store.add_edge(b, a, "DERIVED_FROM")
    store.add_edge(a, b, "DERIVED_FROM")  # cycle: a depends on b, b on a

    revision = Reviser(store, "v1-test").revise(a)

    assert b in revision.invalidated
    # The trigger is never invalidated by its own closure.
    assert a not in revision.invalidated


# -- fail closed ----------------------------------------------------------


def test_an_unreadable_attestation_group_is_refused_not_guessed():
    """A dependency that cannot be read stops the revision.

    Silently treating an unresolvable group as an empty set would make a
    SUPPORTED claim read as unattested, which is the same defect one level
    up.
    """
    store = Store()
    base = _store_with_claim(store, "the claim whose support cannot be resolved", EpistemicState.RETRACTED)
    supported = _store_with_claim(store, "a claim attesting via a group that is missing", EpistemicState.INCONCLUSIVE)
    _attest(store, supported, [base])
    # Corrupt the group out from under the evaluation.
    store.db.execute("DELETE FROM peer_groups")
    store.db.commit()

    with pytest.raises(RevisionError, match="does not resolve"):
        Reviser(store, "v1-test").revise(base)


# -- the auditor can re-derive the invariant ------------------------------


def test_audit_dangling_finds_a_dependent_left_behind_by_a_missed_revision():
    """The independent check has something to find.

    If a claim is retracted without revise() being called, the artifact is
    left asserting something the evidence no longer supports. This is that
    state, and audit_dangling names the culprit.
    """
    store = Store()
    base = _store_with_claim(store, "the original claim that was retracted", EpistemicState.RETRACTED)
    dep = _store_with_claim(store, "a derived claim nobody remembered to revise", EpistemicState.DERIVED)
    store.add_edge(dep, base, "DERIVED_FROM")

    reviser = Reviser(store, "v1-test")
    assert reviser.audit_dangling() == (dep,)

    reviser.revise(base)
    assert reviser.audit_dangling() == ()
