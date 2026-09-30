"""Every bulk belief change must re-establish the artifact invariants.

ADR-021 fixed one instance of the fifth-occurrence shape: ``evaluate_all``
moved 319,293 claims and left the recorded ``state_digest`` describing the
beliefs from before. The fix was in the funnel, because the defect was
reachable by using the API correctly.

The question this file asks is whether any *other* funnel was missed. Four
values describe the artifact and can be invalidated by a later write:

* ``state_digest`` -- what the auditor re-derives and compares
* ``state_epoch``  -- the witness tripwire (ADR-018)
* ``state_counts`` -- the distribution recorded in the manifest
* the artifact root/version itself

Reviser is the prime suspect: ADR-017 predates ADR-018 and ADR-021 and
invalidate claims with a raw ``UPDATE``, so it is the one bulk path that
never passed through ``set_state`` -- the single funnel ADR-018 inlined its
epoch bump into specifically so the tripwire could not be missed.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sqlite3
import sys
from pathlib import Path

import pytest

from ganymede4.compile.compiler import CompiledArtifact, SourceSpec, compile_corpus
from ganymede4.knowledge.epistemic import EpistemicState
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.revision import Reviser, RevisionError
from ganymede4.knowledge.store import Store
from ganymede4.witness.witness import Witness

REPO = Path(__file__).resolve().parents[1]
AUDITOR = REPO / "scripts" / "audit_provenance.py"
TT = "2026-09-30T00:00:00Z"

# Enough structure that revision has a real dependency graph to walk:
# claim A is attested by claim B, so retracting A must fall B.
#: Appears verbatim in three sources, so the miner emits a DERIVED heuristic
#: resting on it. Needed to reach the revision defect: every derived claim in
#: the real corpus has zero evaluations, and that is what makes moving one
#: illegal.
RECURRING = "The build should be reproducible from source.\n"

DOCS = [
    SourceSpec(
        uri="a.md",
        content=(
            "The runtime enforces provenance at write time.\n"
            "Verification is independent of the writer.\n"
            "It is also a pyramid scheme.\n"
        ),
    ),
    SourceSpec(
        uri="b.md",
        content=(
            "THIS IS NOT A PYRAMID SCHEME!\n"
            "Provenance is content addressed by hash.\n"
            "The system is deliberately not a pyramid scheme.\n"
        ),
    ),
    SourceSpec(
        uri="c.md",
        content=(
            "The runtime enforces provenance at write time always.\n"
            "Verification is independent of the writer indeed.\n"
            + RECURRING
        ),
    ),
    # Mining needs HEURISTIC_MIN_SUPPORT=3 *distinct sources* sharing a
    # normalized sentence shape -- the same shape, not similar ones. Two
    # paraphrases do not mine; this sentence is verbatim across three
    # documents. Without a DERIVED claim the "invalidation leaves no record"
    # defect is unreachable and the test would pass vacuously.
    SourceSpec(uri="d.md", content=RECURRING),
    SourceSpec(uri="e.md", content=RECURRING),
]


def _artifact(tmp_path: Path) -> tuple[Path, Store, CompiledArtifact]:
    db = tmp_path / "a.db"
    store = Store(str(db))
    art = compile_corpus(store, DOCS, transaction_time=TT)
    return db, store, art


def _pick_trigger(store: Store, art: CompiledArtifact) -> str:
    """A claim that is terminal-able AND has at least one dependent.

    Picking the wrong direction is easy and silent: an *attested subject*
    is a claim that was supported, while a claim that others were attested
    *by* is its dependency. Only the second has anything to fall.
    """
    reviser = Reviser(store, art.manifest.version)
    graph = reviser._graph()
    candidates = [cid for cid, deps in graph.items() if deps]
    assert candidates, "fixture must produce a dependency edge"
    return candidates[0]


def _audit(path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(AUDITOR), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return {"code": proc.returncode, "report": json.loads(proc.stdout)}


def _digest_matches(store: Store) -> bool:
    from ganymede4.compile.manifest import state_digest

    return store.recorded_artifact()["state_digest"] == state_digest(store.state_pairs())


# -- the suspect ---------------------------------------------------------


def test_reviser_invalidates_real_dependents(tmp_path: Path):
    """Establish that revision actually moves beliefs.

    A test that passes because nothing moved would make every check below
    vacuous, so this pins that the graph is non-empty and the walk lands.
    """
    db, store, art = _artifact(tmp_path)
    Evaluator(store, art.manifest).evaluate_all()

    supported = [
        r["id"]
        for r in store.db.execute("SELECT id FROM claims WHERE state = 'supported'")
    ]
    assert supported, "fixture must produce at least one SUPPORTED claim"

    # Retract a support claim that something else was attested by.
    attested_subjects = [
        r["subject_id"]
        for r in store.db.execute(
            "SELECT subject_id FROM evaluations WHERE relation = 'attested'"
        )
    ]
    assert attested_subjects, "fixture must produce at least one attestation"

    reviser = Reviser(store, art.manifest.version)
    trigger = _pick_trigger(store, art)

    before = store.db.execute(
        "SELECT state FROM claims WHERE id = ?", (trigger,)
    ).fetchone()["state"]
    store.set_state(trigger, EpistemicState.RETRACTED, transaction_time=TT)

    revision = reviser.revise(trigger)
    assert revision.invalidated, "revision must move at least one dependent"
    assert revision.version == art.manifest.version


def test_reviser_keeps_digest_true(tmp_path: Path):
    """ADR-022: revision must leave the store describing itself truthfully."""
    db, store, art = _artifact(tmp_path)
    Evaluator(store, art.manifest).evaluate_all()
    assert _digest_matches(store), "precondition: sealed after evaluation"

    reviser = Reviser(store, art.manifest.version)
    trigger = _pick_trigger(store, art)
    store.set_state(trigger, EpistemicState.RETRACTED, transaction_time=TT)
    reviser.revise(trigger)

    assert _digest_matches(store), (
        "after Reviser.revise the recorded state_digest must still describe "
        "the claims actually stored"
    )


def test_reviser_bumps_the_epoch(tmp_path: Path):
    """ADR-018's tripwire must survive the revision path.

    The subtle part: `set_state` also bumps, so "the epoch went up" proves
    nothing unless the two contributions are separated. This measures the
    epoch *after* the retraction and *again* after the walk, so the second
    reading can only have moved if `revise` itself bumped it.

    Written this way after the sabotage pass: removing the bump from
    `revise` was caught only by a source-text assertion, because the
    existing test was satisfied by `set_state`'s contribution alone.
    """
    db, store, art = _artifact(tmp_path)
    Evaluator(store, art.manifest).evaluate_all()
    reviser = Reviser(store, art.manifest.version)
    trigger = _pick_trigger(store, art)

    store.set_state(trigger, EpistemicState.RETRACTED, transaction_time=TT)
    after_retraction = store.state_epoch()

    assert reviser.revise(trigger).invalidated
    after_walk = store.state_epoch()

    assert after_walk > after_retraction, (
        "revise() moved claims but did not bump the epoch, so a witness "
        "bound before the revision would not be refused: the ADR-018 "
        f"tripwire is not armed by this path (epoch stayed {after_retraction})"
    )


def test_idempotent_revise_does_not_move_the_epoch(tmp_path: Path):
    """A retry must not keep advancing identity for work already done."""
    db, store, art = _artifact(tmp_path)
    Evaluator(store, art.manifest).evaluate_all()
    reviser = Reviser(store, art.manifest.version)
    trigger = _pick_trigger(store, art)
    store.set_state(trigger, EpistemicState.RETRACTED, transaction_time=TT)

    assert reviser.revise(trigger).invalidated
    settled = store.state_epoch()
    digest = store.recorded_artifact()["state_digest"]

    # Second pass finds nothing left to move.
    assert reviser.revise(trigger).invalidated == ()
    assert store.state_epoch() == settled, "a no-op revise must not bump the epoch"
    held = store.recorded_artifact() or {}
    assert held["state_digest"] == digest, (
        "a no-op revise must not re-seal; identity should be stable"
    )


def test_witness_refused_after_revision(tmp_path: Path):
    """A witness bound before revision must refuse after it."""
    db, store, art = _artifact(tmp_path)
    Evaluator(store, art.manifest).evaluate_all()
    witness = Witness(store, art.manifest)
    witness.ask("Is it a pyramid scheme?")

    reviser = Reviser(store, art.manifest.version)
    trigger = _pick_trigger(store, art)
    store.set_state(trigger, EpistemicState.RETRACTED, transaction_time=TT)
    reviser.revise(trigger)

    from ganymede4.witness.witness import StaleWitness

    with pytest.raises(StaleWitness):
        witness.ask("Is it a pyramid scheme?")


def test_auditor_clean_after_revision(tmp_path: Path):
    """The end-to-end consequence, on a compiled+evaluated+revised store."""
    db, store, art = _artifact(tmp_path)
    Evaluator(store, art.manifest).evaluate_all()

    reviser = Reviser(store, art.manifest.version)
    trigger = _pick_trigger(store, art)
    store.set_state(trigger, EpistemicState.RETRACTED, transaction_time=TT)
    reviser.revise(trigger)
    store.reseal()
    store.close()

    out = _audit(db)
    assert out["report"]["state_digest_mismatch"] == [], out
    assert out["report"]["clean"] is True, out
    assert out["code"] == 0, out


def test_auditor_clean_after_revision_without_manual_reseal(tmp_path: Path):
    """ADR-022: revision alone must leave a store the auditor accepts.

    This test was originally written to assert the *defect* -- that the
    auditor rejects a store revised without a manual re-seal. It passed, and
    that was the finding. It is now inverted, which is the honest record of
    the fix: the same scenario, asserted the other way, with the comment
    explaining why the assertion flipped.
    """
    db, store, art = _artifact(tmp_path)
    Evaluator(store, art.manifest).evaluate_all()

    reviser = Reviser(store, art.manifest.version)
    trigger = _pick_trigger(store, art)
    store.set_state(trigger, EpistemicState.RETRACTED, transaction_time=TT)
    assert reviser.revise(trigger).invalidated, "revision must have moved something"
    store.close()

    out = _audit(db)
    assert out["report"]["state_digest_mismatch"] == [], out
    assert out["report"]["clean"] is True, (
        "Reviser must re-establish the digest itself; a store revised "
        f"through the API is now expected to audit clean. Report: {out}"
    )
    assert out["code"] == 0, out


def test_reviser_still_invalidates_via_a_bulk_write(tmp_path: Path):
    """Pin that the fix did not route revision through per-claim set_state.

    Routing through `set_state` would be correct but would cost one commit
    per invalidated claim, and revision closures reach ~142,000 claims on the
    real corpus. The bulk write is deliberate; these two assertions keep the
    decision visible so a future change to it is a conscious one.
    """
    source = Path(inspect.getsourcefile(Reviser) or "").read_text(encoding="utf-8")
    assert "UPDATE claims SET state" in source, (
        "expected Reviser to invalidate via a raw bulk UPDATE; if this "
        "changed, re-check whether the epoch and digest are still maintained"
    )
    assert "set_state(" not in source, (
        "Reviser now routes through set_state -- one commit per claim, and "
        "the invariant matrix needs updating"
    )
    # And the maintenance must live in revise()'s body, not in the per-claim
    # helper. revise() is defined before _invalidate, so "inside revise" is
    # exactly "before _invalidate" -- a weaker claim than it looks, which is
    # why the check is scoped between the two defs rather than by direction.
    revise_body = source[source.index("def revise") : source.index("def _invalidate")]
    assert "bump_state_epoch" in revise_body, (
        "the epoch bump belongs in revise(), after the walk completes, so a "
        "closure pays for one bump rather than one per claim"
    )
    assert "self._store.reseal()" in revise_body, (
        "reseal must be in revise() so a closure pays for one store scan, "
        "not one per claim"
    )
    assert "bump_state_epoch" not in source[
        source.index("def _invalidate") :
    ], "the per-claim helper must not bump the epoch"


def test_revise_refuses_non_terminal_trigger(tmp_path: Path):
    """Revision is not a general state-setting path; keep it that way."""
    db, store, art = _artifact(tmp_path)
    reviser = Reviser(store, art.manifest.version)
    claim = store.db.execute("SELECT id FROM claims LIMIT 1").fetchone()["id"]
    with pytest.raises(RevisionError):
        reviser.revise(claim)

def _dependent_without_evaluation(store: Store, art: CompiledArtifact) -> str:
    """A claim that revision can move and that has no evaluation record.

    The two obvious candidates both mislead. A `SUPPORTED` claim already has
    an evaluator verdict, so "does it have an evaluation?" passes for the
    wrong reason. The case that actually broke on the real corpus is a
    `DERIVED` heuristic: all 16,897 of them have zero evaluations and are
    legal only because the auditor exempts the `derived` state -- the moment
    revision moves one, the exemption no longer applies and the claim is
    both an orphan and unexplained.
    """
    reviser = Reviser(store, art.manifest.version)
    graph = reviser._graph()
    for dependent in graph.values():
        for cid in dependent:
            row = store.db.execute(
                "SELECT state FROM claims WHERE id = ?", (cid,)
            ).fetchone()
            if row is None or row["state"] != "derived":
                continue
            if store.db.execute(
                "SELECT COUNT(*) FROM evaluations WHERE subject_id = ?", (cid,)
            ).fetchone()[0]:
                continue
            return cid
    raise AssertionError("fixture must contain an unevaluated DERIVED dependent")


def test_invalidation_is_recorded_as_an_evaluation(tmp_path: Path):
    """ADR-022: a state change must leave a record of what decided it.

    Found on the real corpus. The auditor flagged the claim the Reviser
    invalidated with BOTH `orphans` and `state_without_evaluation`, and it
    was a mined `DERIVED` heuristic with zero evaluation rows in its entire
    life. ADR-006 requires an evaluation behind every state change; the
    Reviser moves claims without ever writing one.

    This is the sixth occurrence of the shape, and the sharpest yet:
    ADR-014/018/020/021 were all about a value that went *stale*. This one
    is about a value that was never *written*.
    """
    db, store, art = _artifact(tmp_path)
    Evaluator(store, art.manifest).evaluate_all()
    reviser = Reviser(store, art.manifest.version)
    target = _dependent_without_evaluation(store, art)

    # Retract the support it rests on so the walk reaches it.
    support = next(s for s, deps in reviser._graph().items() if target in deps)
    store.set_state(support, EpistemicState.RETRACTED, transaction_time=TT)
    assert target in reviser.revise(support).invalidated, (
        "the walk must have reached the derived dependent"
    )

    rows = store.db.execute(
        "SELECT COUNT(*) FROM evaluations WHERE subject_id = ?", (target,)
    ).fetchone()[0]
    assert rows > 0, (
        f"claim {target} was moved to INVALIDATED with no evaluation record "
        "naming what decided it; ADR-006 requires one, and the independent "
        "auditor reports exactly this as state_without_evaluation + orphan"
    )


def test_invalidation_record_names_the_trigger(tmp_path: Path):
    """The record must be falsifiable, not merely present.

    A count of one is satisfiable by an empty or meaningless record. This
    requires the invalidating decision to name the claim it was caused by,
    so a reader can walk back from the state change to the retraction.
    """
    db, store, art = _artifact(tmp_path)
    Evaluator(store, art.manifest).evaluate_all()
    reviser = Reviser(store, art.manifest.version)
    target = _dependent_without_evaluation(store, art)
    support = next(s for s, deps in reviser._graph().items() if target in deps)
    store.set_state(support, EpistemicState.RETRACTED, transaction_time=TT)
    assert target in reviser.revise(support).invalidated

    metas = [
        r["meta"]
        for r in store.db.execute(
            "SELECT meta FROM evaluations WHERE subject_id = ?", (target,)
        )
    ]
    assert any(isinstance(m, str) and support in m for m in metas), (
        f"no evaluation for {target} names the retraction that caused it "
        f"({support}): {metas}"
    )
