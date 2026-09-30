"""Reconstructing an artifact identity from a reopened store (ADR-025).

Before `compile.reopen` existed, a reopened database had no way to rebuild the
manifest it was holding. `Store.recorded_artifact()` returned a dict of
strings. That is enough to *print* an artifact's identity and not enough to
*check* one, and the difference is the whole point of the exercise.

The property under test: the recomputed root must equal the recorded root on an
untouched store, and must **differ** when the contents change. A test that only
checked the first half would pass against an implementation that always
returned the recorded value.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from ganymede4.compile import manifest_matches_recorded, rebuild_manifest  # noqa: E402
from ganymede4.compile.compiler import SourceSpec, compile_corpus  # noqa: E402
from ganymede4.knowledge.store import Store  # noqa: E402

SENTENCE = (
    "The provenance of every claim is content addressed so that a later write "
    "cannot silently replace an earlier belief."
)


@pytest.fixture()
def built(tmp_path):
    """A small real artifact: compiled, recorded, then closed and reopened."""
    db = tmp_path / "a.db"
    specs = [
        SourceSpec(f"mem://src/{i}", f"{SENTENCE} This is document {i}.", source_type="reddit_comment")
        for i in range(3)
    ]
    with Store(str(db)) as store:
        compile_corpus(store, specs, transaction_time="2026-01-01T00:00:00Z")
    return db


# ------------------------------------------------------- the happy path
def test_rebuild_reproduces_the_recorded_root(built):
    """The load-bearing claim: reconstructing the manifest from stored rows
    yields the same root the compiler computed in memory."""
    with Store(str(built)) as store:
        matches, rebuilt, recorded = manifest_matches_recorded(store)
        assert recorded is not None
        assert rebuilt.root == recorded["root"]
        assert matches is True


def test_rebuild_is_order_independent(built):
    """build_manifest sorts its leaves, so the reconstruction must not depend
    on SQLite's row order. If a future refactor dropped the sort, the root
    would depend on insertion order and this would catch it."""
    with Store(str(built)) as store:
        a = rebuild_manifest(store)
    with Store(str(built)) as store:
        store.db.execute("VACUUM")
        b = rebuild_manifest(store)
    assert a.root == b.root


def test_rebuild_recognises_heuristics_structurally(built):
    """Heuristics are found by their DERIVED_FROM edges, not by
    `state = 'derived'`.

    This matters because ADR-022's revision path can move a mined claim out of
    `derived` into `invalidated`. A state-based lookup would then drop that
    claim from the leaf set and produce a root matching nothing -- so a
    revision would appear to change the artifact's identity purely because a
    label changed.
    """
    with Store(str(built)) as store:
        m = rebuild_manifest(store)
        by_edge = {
            r[0]
            for r in store.db.execute(
                "SELECT DISTINCT from_id FROM claim_edges WHERE relation = 'DERIVED_FROM'"
            )
        }
        by_state = {
            r[0] for r in store.db.execute("SELECT id FROM claims WHERE state = 'derived'")
        }
    assert by_edge, "fixture must actually mine a heuristic"
    # Both criteria agree here; the structural one is correct where they differ.
    assert by_edge == by_state
    assert set(m.heuristic_ids) == by_edge


AUDITOR = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "audit_provenance.py"


def _audit(db):
    """Run the independent auditor as its own process.

    Subprocess, not import, for the same reason the build script shells out:
    an auditor that imports the package it audits cannot find that package's
    bugs. Running it the way it ships is also the only way this test proves
    the *shipped* entry point detects the tamper.
    """
    proc = subprocess.run(
        [sys.executable, str(AUDITOR), str(db)],
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout), proc.returncode


# ------------------------------------------- the falsifiable half
def test_rebuild_cannot_see_a_content_change_and_the_auditor_can(built):
    """ADR-026: the division of responsibility, stated as a test.

    This test originally asserted that editing a claim's text changes the
    rebuilt root. That assertion is wrong, and it was wrong in an instructive
    way. The Merkle root is computed over content-addressed claim *ids*, and
    an id is a hash of the claim's content. So editing a row's text without
    recomputing its id leaves the id -- and the root -- untouched.

    A root that *did* react would have to hash the text directly, which would
    destroy the property that recompiling unchanged bytes reproduces the same
    root and that reordering sources does not change identity. So the root's
    blindness here is not a defect to fix; it is the division of labour:

      root    -> identity and structure (ids, membership, states, edges)
      auditor -> content (does this row still say what it claims to say?)

    Both halves are asserted. If a future change made the root hash raw text,
    the first half fails. If someone drops the auditor's content check, the
    second fails.
    """
    with Store(str(built)) as store:
        before = rebuild_manifest(store).root

    with sqlite3.connect(str(built)) as conn:
        conn.execute("UPDATE claims SET text = text || ' tampered'")
        conn.commit()

    with Store(str(built)) as store:
        after = rebuild_manifest(store).root

    # The root is blind to it. This is content addressing, not a bug.
    assert after == before, (
        "the rebuilt root now reacts to raw row text; if that was intended, "
        "ADR-004 (compile is a pure function of source bytes, and source "
        "order does not change the root) has been broken"
    )

    # The auditor is not blind to it.
    report, code = _audit(built)
    assert report["claim_text_mismatches"], (
        "an out-of-band claim-text edit went unreported; ADR-026 requires the "
        "independent auditor to catch this"
    )
    assert report["clean"] is False
    assert code != 0


def test_rebuild_detects_a_membership_change(built):
    """Content addressing is why text is invisible to the root. Membership is
    the other half of the contract and *must* be visible: dropping a row
    changes the leaf set, so the root changes."""
    with Store(str(built)) as store:
        before = rebuild_manifest(store).root

    with sqlite3.connect(str(built)) as conn:
        conn.execute(
            "DELETE FROM claims WHERE id = ("
            "  SELECT id FROM claims WHERE state = 'unexamined' LIMIT 1)"
        )
        conn.commit()

    with Store(str(built)) as store:
        after = rebuild_manifest(store).root
        matches, _, _ = manifest_matches_recorded(store)

    assert after != before, "deleting a claim row did not change the root"
    assert matches is False, "a store missing a claim matched its recording"


def test_rebuild_detects_a_state_change(built):
    """ADR-018: the root must be able to see a belief change, not only a
    content change. A claim that is retracted and one that is believed are
    different artifacts."""
    with Store(str(built)) as store:
        before = rebuild_manifest(store).root
        claim_id = next(iter(store.db.execute("SELECT id FROM claims")))[0]
        store.set_state(
            claim_id, "retracted", transaction_time="2026-01-02T00:00:00Z"
        )

    with Store(str(built)) as store:
        assert rebuild_manifest(store).root != before


def test_rebuild_detects_a_deleted_claim(built):
    """Removing a row must change the identity. If it did not, the root would
    not be a function of the contents."""
    with Store(str(built)) as store:
        before = rebuild_manifest(store).root
    with sqlite3.connect(str(built)) as conn:
        conn.execute("DELETE FROM claims WHERE id = (SELECT id FROM claims LIMIT 1)")
        conn.commit()
    with Store(str(built)) as store:
        assert rebuild_manifest(store).root != before


# ----------------------------------------- the unflushed-reader property
def test_the_check_does_not_flush_a_pending_seal(built):
    """`recorded_artifact()` flushes a pending seal. Using it to *check* the
    store would compare the store against a digest just recomputed from it --
    equal by construction, and therefore incapable of detecting anything.

    So the comparison uses the unflushed reader, and this pins that: a store
    left with a pending seal must still report its *stale* recording, and the
    mismatch must survive.
    """
    with Store(str(built)) as store:
        # Plant an out-of-band state change that leaves the seal pending.
        claim_id = next(iter(store.db.execute("SELECT id FROM claims")))[0]
        with sqlite3.connect(str(built)) as conn:
            conn.execute("UPDATE claims SET state = 'retracted' WHERE id = ?", (claim_id,))
            conn.commit()

        matches, _, recorded = manifest_matches_recorded(store)
        assert matches is False, (
            "manifest_matches_recorded flushed a pending seal, so the store "
            "was compared against a digest recomputed from it and always matched"
        )
        assert recorded is not None
