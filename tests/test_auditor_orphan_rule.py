"""The orphan rule must key on provenance, not on state (ADR-022).

Changing an auditor is the most dangerous kind of fix in this project: the
auditor is the thing that catches everything else, so weakening it to let a
defect pass is a fix that removes the capacity to detect defects. These
tests exist to prove the new rule is *narrower in the right direction* --
it still catches a claim with no support of any kind, and it no longer
reports a false positive for a claim whose derivation edges are intact.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from ganymede4.compile.compiler import CompiledArtifact, SourceSpec, compile_corpus
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.store import Store

REPO = Path(__file__).resolve().parents[1]
AUDITOR = REPO / "scripts" / "audit_provenance.py"
TT = "2026-09-30T00:00:00Z"
RECURRING = "The build should be reproducible from source.\n"

DOCS = [
    SourceSpec(uri="a.md", content=RECURRING + "Provenance is content addressed.\n"),
    SourceSpec(uri="b.md", content=RECURRING),
    SourceSpec(uri="c.md", content=RECURRING),
]


def _audit(path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(AUDITOR), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(proc.stdout)


def _compiled(tmp_path: Path) -> tuple[Path, CompiledArtifact]:
    db = tmp_path / "a.db"
    store = Store(str(db))
    art = compile_corpus(store, DOCS, transaction_time=TT)
    return db, art


def test_fixture_really_produces_a_derived_heuristic(tmp_path: Path):
    """Precondition. Without this every assertion below is vacuous."""
    db, _ = _compiled(tmp_path)
    conn = sqlite3.connect(str(db))
    derived = conn.execute("SELECT COUNT(*) FROM claims WHERE state='derived'").fetchone()[0]
    edges = conn.execute(
        "SELECT COUNT(DISTINCT from_id) FROM claim_edges WHERE relation='DERIVED_FROM'"
    ).fetchone()[0]
    conn.close()
    assert derived >= 1, "fixture must mine a heuristic"
    assert edges >= 1, "a derived claim must rest on DERIVED_FROM edges"


def test_derived_claim_has_no_direct_evidence(tmp_path: Path):
    """The premise of the whole change: derivation replaces evidence."""
    db, _ = _compiled(tmp_path)
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT c.id FROM claims c WHERE c.state='derived' LIMIT 1"
    ).fetchone()
    assert row is not None
    with_ev = conn.execute(
        "SELECT COUNT(*) FROM claim_evidence WHERE claim_id=?", (row[0],)
    ).fetchone()[0]
    conn.close()
    assert with_ev == 0, (
        "ADR-004 says derived claims rest on DERIVED_FROM edges, not on "
        "claim_evidence; if this changed the orphan rule may be wrong"
    )


def test_orphaned_claim_with_no_support_is_still_caught(tmp_path: Path):
    """The negative direction, and the one that matters.

    Plant a claim in a state that promises evidence, with neither direct
    evidence nor derivation edges. The auditor must report it. A fix that
    silenced the false positive by widening the exemption would pass every
    other test in this file and fail here.
    """
    db, art = _compiled(tmp_path)
    store = Store(str(db))
    evaluator = Evaluator(store, art.manifest)
    evaluator.evaluate_all()

    conn = sqlite3.connect(str(db))
    # UPDATE ... LIMIT is not valid SQLite. Select the target row first, then
    # write to it by id -- and assert we got one, because a test that plants
    # nothing and then asserts the auditor is clean proves nothing.
    # After evaluate_all nothing is `unexamined` any more, so select any
    # non-derived claim in a state that promises evidence. The point of the
    # plant is the *absence* of support, not any particular state.
    target = conn.execute(
        "SELECT id FROM claims WHERE state NOT IN "
        "('unexamined','assumed','derived','unresolved') AND id NOT IN "
        "(SELECT from_id FROM claim_edges WHERE relation='DERIVED_FROM') "
        "AND id NOT IN (SELECT claim_id FROM claim_evidence) LIMIT 1"
    ).fetchone()
    if target is None:
        # Evaluation gave every claim evidence; strip it from one, which is
        # the same defect by a different route.
        target = conn.execute(
            "SELECT c.id FROM claims c WHERE c.state NOT IN "
            "('unexamined','assumed','derived','unresolved') AND c.id NOT IN "
            "(SELECT from_id FROM claim_edges WHERE relation='DERIVED_FROM') LIMIT 1"
        ).fetchone()
        assert target is not None, "fixture must contain a non-derived claim"
        conn.execute(
            "DELETE FROM claim_evidence WHERE claim_id=?", (target[0],)
        )
    assert target is not None
    conn.execute("UPDATE claims SET state='supported' WHERE id=?", (target[0],))
    conn.commit()
    planted_id = target[0]
    conn.close()

    report = _audit(db)
    orphans = {o["claim_id"] for o in report["orphans"]}
    assert planted_id in orphans, (
        "a claim with neither evidence nor derivation edges must still be "
        f"reported as an orphan; orphans={report['orphans']}"
    )
    assert report["clean"] is False


def test_derived_claim_invalidated_is_not_a_false_orphan(tmp_path: Path):
    """The false positive ADR-022 removes."""
    db, art = _compiled(tmp_path)
    store = Store(str(db))
    Evaluator(store, art.manifest).evaluate_all()
    conn = sqlite3.connect(str(db))
    derived = conn.execute(
        "SELECT c.id FROM claims c WHERE c.state='derived' AND EXISTS "
        "(SELECT 1 FROM claim_edges e WHERE e.from_id=c.id AND e.relation='DERIVED_FROM') LIMIT 1"
    ).fetchone()
    conn.close()
    assert derived is not None
    claim_id = derived[0]

    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE claims SET state='invalidated' WHERE id=?", (claim_id,))
    conn.commit()
    conn.close()

    report = _audit(db)
    orphans = {o["claim_id"] for o in report["orphans"]}
    assert claim_id not in orphans, (
        "a derived claim whose DERIVED_FROM edges are intact is not an "
        "orphan; its support is edges, permanently (ADR-004)"
    )
