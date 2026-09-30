"""Evaluation moves beliefs, so the artifact must be re-sealed (ADR-021).

ADR-018 put a state digest in the artifact identity and bumped an epoch on
every transition. The epoch works. The *digest* did not: it is written once
by ``record_artifact`` at compile time and never refreshed, so a store that
has been evaluated says it holds an artifact whose beliefs have moved.

Found by running the auditor against the first completed full-corpus
evaluation. The auditor refused, and it was right.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ganymede4.compile.compiler import CompiledArtifact, SourceSpec, compile_corpus
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.store import Store
from ganymede4.witness.witness import StaleWitness, Witness

REPO = Path(__file__).resolve().parents[1]
AUDITOR = REPO / "scripts" / "audit_provenance.py"


TT = "2026-09-29T00:00:00Z"

DOC_A = (
    "The runtime enforces provenance at write time.\n"
    "Verification is independent of the writer.\n"
    "It is also a pyramid scheme.\n"
)
DOC_B = (
    "THIS IS NOT A PYRAMID SCHEME!\n"
    "Provenance is content addressed by hash.\n"
    "The system is deliberately not a pyramid scheme.\n"
)


def _sources() -> list[SourceSpec]:
    return [
        SourceSpec(uri="notes.md", content=DOC_A),
        SourceSpec(uri="other.md", content=DOC_B),
    ]


def _compile(tmp_path: Path, evaluate: bool) -> tuple[Path, CompiledArtifact]:
    db_path = tmp_path / "artifact.db"
    store = Store(str(db_path))
    artifact = compile_corpus(store, _sources(), transaction_time=TT)
    if evaluate:
        Evaluator(store, artifact.manifest).evaluate_all()
    return db_path, artifact


def test_evaluated_store_agrees_with_its_own_artifact(tmp_path: Path):
    """The defect: a store that has been evaluated must still be self-consistent."""
    db_path, _ = _compile(tmp_path, evaluate=True)
    store = Store(str(db_path))

    from ganymede4.compile.manifest import state_digest

    recomputed = state_digest(store.state_pairs())
    assert store.recorded_artifact()["state_digest"] == recomputed, (
        "after evaluation the recorded state digest must match the beliefs "
        "actually stored, or the store misdescribes itself"
    )


def test_unevaluated_store_is_also_self_consistent(tmp_path: Path):
    """The invariant must hold in both directions, not only after evaluation."""
    db_path, _ = _compile(tmp_path, evaluate=False)
    store = Store(str(db_path))
    from ganymede4.compile.manifest import state_digest

    assert store.recorded_artifact()["state_digest"] == state_digest(store.state_pairs())


def test_evaluation_actually_moved_the_digest(tmp_path: Path):
    """Guard against a vacuous fix that records a constant."""
    from ganymede4.compile.manifest import state_digest

    fresh_dir = tmp_path / "fresh"
    done_dir = tmp_path / "done"
    fresh_dir.mkdir()
    done_dir.mkdir()

    fresh, _ = _compile(fresh_dir, evaluate=False)
    done, _ = _compile(done_dir, evaluate=True)

    a = state_digest(Store(str(fresh)).state_pairs())
    b = state_digest(Store(str(done)).state_pairs())
    assert a != b, "evaluation must change what is believed, else this ADR tests nothing"


def test_epoch_bumps_once_per_transition(tmp_path: Path):
    """The existing half of ADR-018 must keep working."""
    db_path, _ = _compile(tmp_path, evaluate=True)
    store = Store(str(db_path))
    moved = sum(
        1
        for _, state in store.state_pairs()
        if state not in ("unexamined", "derived")
    )
    assert store.state_epoch() == moved


def test_auditor_accepts_an_evaluated_store(tmp_path: Path):
    """The end-to-end consequence: the independent auditor must exit 0."""
    db_path, _ = _compile(tmp_path, evaluate=True)
    proc = subprocess.run(
        [sys.executable, str(AUDITOR), str(db_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(proc.stdout)
    assert report["state_digest_mismatch"] == [], report
    assert report["clean"] is True, report
    assert proc.returncode == 0, report


def test_auditor_still_rejects_a_doctored_digest(tmp_path: Path):
    """A re-seal must not become a way to silence the auditor.

    If sealing is 'recompute and overwrite', then a store whose claims were
    edited behind the API's back would also re-seal clean. The auditor's
    value depends on the recorded digest being an independent commitment,
    so this pins that the auditor still fails on a mismatch.
    """
    db_path, _ = _compile(tmp_path, evaluate=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE artifact SET state_digest = '0' * 64 WHERE id = 1")
    conn.commit()
    conn.close()

    proc = subprocess.run(
        [sys.executable, str(AUDITOR), str(db_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(proc.stdout)
    assert report["clean"] is False
    assert report["state_digest_mismatch"], "a doctored digest must be detected"
    assert proc.returncode == 1


def test_witness_bound_before_evaluation_is_refused_after(tmp_path: Path):
    """The tripwire: a witness bound pre-evaluation must refuse afterwards.

    This is the user-visible consequence of the missing re-seal. A witness
    built against the pre-evaluation manifest is holding a version whose
    beliefs no longer exist, and ADR-018's epoch exists precisely to catch
    that. Constructing the witness first is the point -- a witness built
    after evaluation is legitimately fresh.
    """
    db_path = tmp_path / "artifact.db"
    store = Store(str(db_path))
    artifact = compile_corpus(store, _sources(), transaction_time=TT)

    witness = Witness(store, artifact.manifest)
    # Fresh right now: nothing has moved yet.
    witness.ask("Is it a pyramid scheme?")

    Evaluator(store, artifact.manifest).evaluate_all()

    with pytest.raises(StaleWitness) as exc:
        witness.ask("Is it a pyramid scheme?")
    assert "stale" in str(exc.value).lower() or "moved under" in str(exc.value).lower()


def test_reseal_does_not_disturb_provenance(tmp_path: Path):
    """Re-sealing changes identity, never content."""
    db_path, _ = _compile(tmp_path, evaluate=True)
    store = Store(str(db_path))
    before = store.counts()

    store.reseal()

    assert store.counts() == before, "re-sealing must not touch claims or evidence"
    proc = subprocess.run(
        [sys.executable, str(AUDITOR), str(db_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(proc.stdout)
    assert report["clean"] is True, report
