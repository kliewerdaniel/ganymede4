"""A claim's state is part of what the artifact says (ADR-018).

The defect these tests exist for was found by measurement, not by reading: on
the real 336,190-claim corpus, retracting a claim and invalidating its
dependent left the Merkle root byte-identical, because the root covered claim
*ids* and a claim's id deliberately excludes its state. A witness bound to the
old version then kept answering, reporting a state distribution that was false,
under a version id that certified it.

So the tests below are ordered to fail loudly on each of the three parts of the
fix, and each was checked against a sabotaged implementation.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ganymede4.compile.compiler import SourceSpec, compile_corpus
from ganymede4.compile.manifest import Manifest, build_manifest, state_digest
from ganymede4.knowledge.epistemic import EpistemicState
from ganymede4.knowledge.revision import Reviser
from ganymede4.knowledge.store import Store
from ganymede4.witness.witness import StaleWitness, Witness

REPO = Path(__file__).resolve().parent.parent
AUDITOR = REPO / "scripts" / "audit_provenance.py"

TT = "2026-09-29T00:00:00Z"

SOURCES = [
    SourceSpec(
        "test://a",
        "The runtime enforces provenance at write time. "
        "Verification is independent of the store that holds the records. "
        "Every claim names the source offset it came from.",
        source_type="test",
    ),
    SourceSpec(
        "test://b",
        "A retracted claim takes its dependents with it. "
        "The reviser only ever moves a claim downward. "
        "Refused transitions are reported rather than dropped.",
        source_type="test",
    ),
]

#: Four sources sharing two sentence shapes. The recurrence miner requires
#: ``HEURISTIC_MIN_SUPPORT`` distinct sources, so a two-source fixture mines
#: nothing and the derived-dependency path goes untested. The first test
#: asserts these actually produce a heuristic, so the fixture cannot silently
#: stop working and leave the revision tests vacuous.
_MINING_SOURCES = [
    SourceSpec(
        f"test://m{i}",
        "The reviewer refused the proposal because the citation was missing. "
        "The reviewer recorded the decision and the reason it was refused.",
        source_type="test",
    )
    for i in range(4)
]


def build(tmp_path: Path, name: str = "a.db") -> tuple[Store, Manifest]:
    store = Store(str(tmp_path / name))
    artifact = compile_corpus(store, SOURCES, transaction_time=TT)
    return store, artifact.manifest


def build_mined(tmp_path: Path, name: str = "m.db") -> tuple[Store, Manifest]:
    store = Store(str(tmp_path / name))
    artifact = compile_corpus(store, _MINING_SOURCES, transaction_time=TT)
    return store, artifact.manifest


def test_the_mining_fixture_really_mines(tmp_path):
    """Guard the guard: without this, the revision tests skip silently."""
    store, _ = build_mined(tmp_path)
    assert store.counts()["claim_edges"] > 0, (
        "the mining fixture no longer produces heuristics, so the "
        "derived-dependency revision path would go untested"
    )


def unexamined(store: Store) -> str:
    for row in store.claims():
        if row["state"] == EpistemicState.UNEXAMINED.value:
            return row["id"]
    raise AssertionError("no unexamined claim in fixture")


# -- part 1: state participates in the root -------------------------------


def test_state_digest_is_order_independent():
    """A digest perturbed by iteration order cannot identify anything."""
    pairs = [("clm-b", "supported"), ("clm-a", "derived"), ("clm-c", "unexamined")]
    assert state_digest(pairs) == state_digest(list(reversed(pairs)))


def test_state_digest_changes_when_any_state_changes():
    base = [("clm-a", "supported"), ("clm-b", "derived")]
    moved = [("clm-a", "retracted"), ("clm-b", "derived")]
    assert state_digest(base) != state_digest(moved)


def test_state_digest_cannot_be_confused_by_repairing_a_pair():
    """NUL separation, so no pair can be re-read as a different pair.

    Without the separator, ("clm-ab", "x") and ("clm-a", "bx") hash the same.
    """
    assert state_digest([("clm-ab", "x")]) != state_digest([("clm-a", "bx")])


def test_state_digest_of_empty_is_a_distinct_stable_value():
    """A manifest built without state data must not equal one built with it."""
    assert state_digest([]) == state_digest([])
    assert state_digest([]) != state_digest([("clm-a", "derived")])


def test_a_state_change_produces_a_new_artifact_version(tmp_path):
    """The headline defect: identical root across a retraction."""
    store, manifest = build(tmp_path)
    before = manifest.root
    store.set_state(unexamined(store), "retracted", transaction_time=TT)
    after = build_manifest(
        source_ids=manifest.source_ids,
        claim_ids=manifest.claim_ids,
        evidence_ids=manifest.evidence_ids,
        heuristic_ids=manifest.heuristic_ids,
        edge_count=manifest.edge_count,
        state_counts=manifest.state_counts,
        states=store.state_pairs(),
    )
    assert after.root != before, (
        "a retraction left the Merkle root unchanged, so the artifact version "
        "cannot tell 'believed' from 'retracted'"
    )


def test_an_unchanged_store_still_reproduces_its_root(tmp_path):
    """The other direction: state in the root must not make it unstable.

    A root that moved on every read would be as useless as one that never
    moves, and the reproducibility guarantee from ADR-004 would be gone.
    """
    store, manifest = build(tmp_path)
    again = build_manifest(
        source_ids=manifest.source_ids,
        claim_ids=manifest.claim_ids,
        evidence_ids=manifest.evidence_ids,
        heuristic_ids=manifest.heuristic_ids,
        edge_count=manifest.edge_count,
        states=store.state_pairs(),
    )
    assert again.root == manifest.root


def test_claim_id_is_still_independent_of_state(tmp_path):
    """The fix belongs at the artifact layer, not in the claim id.

    If state were hashed into the id, every citation to a reassessed claim
    would break. ADR-018 deliberately does not do that, and this pins it.
    """
    store, manifest = build(tmp_path)
    cid = unexamined(store)
    store.set_state(cid, "retracted", transaction_time=TT)
    assert store.get_claim(cid) is not None
    assert cid in manifest.claim_ids


# -- part 2: the witness fails closed on drift ----------------------------


def test_witness_answers_before_the_store_moves(tmp_path):
    store, manifest = build(tmp_path)
    assert Witness(store, manifest).ask("provenance").is_answerable


def test_witness_refuses_after_a_state_change(tmp_path):
    store, manifest = build(tmp_path)
    witness = Witness(store, manifest)
    store.set_state(unexamined(store), "retracted", transaction_time=TT)
    with pytest.raises(StaleWitness):
        witness.ask("provenance")


def test_witness_boundary_refuses_after_a_state_change(tmp_path):
    """The boundary reports the manifest's state_counts.

    Those numbers are true of the bound version and false of a moved store,
    so answering the question "what do you contain?" with them is the defect.
    """
    store, manifest = build(tmp_path)
    witness = Witness(store, manifest)
    store.set_state(unexamined(store), "retracted", transaction_time=TT)
    with pytest.raises(StaleWitness):
        witness.boundary()


def test_witness_refuses_after_a_revision(tmp_path):
    """The full ADR-017 -> ADR-018 path, not a hand-made state change.

    A derived heuristic is invalidated by retracting something it rests on,
    and the witness bound before that must refuse. This is the case ADR-018
    was written for: two claims changed state, no claim was added or removed,
    so the leaf-set check that catches OutOfVersion cannot see it.
    """
    store, manifest = build_mined(tmp_path)
    witness = Witness(store, manifest)

    row = store.db.execute(
        "SELECT to_id FROM claim_edges WHERE relation='DERIVED_FROM' LIMIT 1"
    ).fetchone()
    assert row is not None, "mining fixture produced no dependency to retract"
    support = row["to_id"]

    store.set_state(support, "retracted", transaction_time=TT)
    revision = Reviser(store, manifest.version).revise(support)
    assert revision.invalidated, "the reviser invalidated nothing; nothing to check"

    with pytest.raises(StaleWitness):
        witness.ask("provenance")
    with pytest.raises(StaleWitness):
        witness.boundary()


def test_a_rebound_witness_answers_normally(tmp_path):
    """Fail closed must not mean fail forever."""
    store, manifest = build(tmp_path)
    store.set_state(unexamined(store), "retracted", transaction_time=TT)
    fresh = build_manifest(
        source_ids=manifest.source_ids,
        claim_ids=manifest.claim_ids,
        evidence_ids=manifest.evidence_ids,
        heuristic_ids=manifest.heuristic_ids,
        edge_count=manifest.edge_count,
        states=store.state_pairs(),
    )
    witness = Witness(store, fresh)
    assert witness.boundary().version == fresh.version


def test_stale_witness_is_distinct_from_out_of_version(tmp_path):
    """They are different incidents and must not collapse into one class.

    OutOfVersion: the record was never here. StaleWitness: everything is still
    here, but the version no longer describes the store.
    """
    from ganymede4.witness.witness import OutOfVersion

    store, manifest = build(tmp_path)
    witness = Witness(store, manifest)
    store.set_state(unexamined(store), "retracted", transaction_time=TT)
    with pytest.raises(StaleWitness):
        witness.ask("provenance")
    assert not issubclass(StaleWitness, OutOfVersion)
    assert not issubclass(OutOfVersion, StaleWitness)


# -- part 3: the auditor re-derives it independently ----------------------


def _audit(db: Path) -> tuple[dict, int]:
    proc = subprocess.run(
        [sys.executable, str(AUDITOR), str(db)],
        capture_output=True,
        text=True,
    )
    return __import__("json").loads(proc.stdout), proc.returncode


def test_auditor_passes_a_clean_artifact(tmp_path):
    store, _ = build(tmp_path)
    store.close()
    report, code = _audit(tmp_path / "a.db")
    assert report["clean"] is True, report
    assert report["state_digest_mismatch"] == []
    assert report["artifact_recorded"] is True
    assert code == 0


def test_auditor_does_not_invent_a_defect_for_an_unrecorded_artifact(tmp_path):
    """A store with no declared version is *uncheckable*, not *wrong*.

    The distinction is load-bearing. Several tests build a store directly
    through the Store API without compiling, so there is no manifest to
    compare against. An auditor that reported a mismatch there would be
    fabricating a defect, which is the one thing an independent auditor must
    never do -- and a check that cries wolf is a check that gets ignored.
    """
    store = Store(str(tmp_path / "h.db"))
    store.add_source(
        uri="test://h",
        content="A claim about provenance that is long enough to survive.",
        source_type="test",
    )
    ev = store.add_evidence(
        source_id=store.db.execute("SELECT id FROM sources").fetchone()["id"],
        start_offset=0,
        end_offset=17,
        text="A claim about pro",
    )
    store.add_claim(
        text="A claim about provenance that is long enough to survive.",
        evidence_ids=[ev],
        transaction_time=TT,
    )
    store.close()

    report, code = _audit(tmp_path / "h.db")
    assert report["artifact_recorded"] is False
    assert report["state_digest_mismatch"] == []
    assert report["state_digest_recorded"] is None
    assert report["clean"] is True
    assert code == 0


def test_auditor_catches_a_state_changed_behind_the_apis_back(tmp_path):
    """The reason the auditor check exists at all.

    `set_state` bumps the epoch, so a witness would notice. Direct SQL moves
    the belief without moving the counter -- and the epoch is a counter in the
    database it guards, so it cannot be the only defence.
    """
    store, _ = build(tmp_path)
    store.close()
    conn = sqlite3.connect(tmp_path / "a.db")
    conn.execute("UPDATE claims SET state='retracted' WHERE id=?", (unexamined_ids(tmp_path)[0],))
    conn.commit()
    conn.close()
    report, code = _audit(tmp_path / "a.db")
    assert report["clean"] is False
    assert len(report["state_digest_mismatch"]) == 1
    assert "without going through" in report["state_digest_mismatch"][0]["detail"]
    assert code == 1


def unexamined_ids(tmp_path: Path) -> list[str]:
    conn = sqlite3.connect(tmp_path / "a.db")
    ids = [r[0] for r in conn.execute("SELECT id FROM claims WHERE state='unexamined'")]
    conn.close()
    return ids


def test_auditor_does_not_import_the_package(tmp_path):
    """The independence property, re-checked now that it hashes too.

    The auditor grew a second implementation of the digest. If it were to
    import the package's, the comparison would only prove the code agrees
    with itself.
    """
    source = AUDITOR.read_text()
    assert "ganymede4" not in source, (
        "the auditor names the package; an auditor that imports or references "
        "the code it audits cannot catch that code being wrong"
    )
    assert "hashlib" in source
