"""Two gaps found while building `verify` (ADR-025).

These were not on the task list. They surfaced because Task 5 required a
`verify` command that *recomputes* the artifact identity rather than printing
it, and building that meant asking "what happens if I change a row behind the
pipeline's back?" — a question nothing had previously asked with an answer.

The measurement, on a small compiled artifact, tampering one row at a time:

| tamper                        | root detects | auditor clean | exit |
|---|---|---|---|
| `claims.text` edited         | **no**  | **true**  | 0 |
| `sources.content` edited     | **no**  | **true**  | 0 |
| `evidence.text` edited       | no         | false      | 1 |
| `claims.state` edited        | yes        | false      | 1 |
| claim row deleted            | yes        | false      | 1 |
| evidence row deleted         | yes        | **true**  | 0 |

Three of those are wrong, and two of them are the project's central promise.

**Why the root cannot catch text edits.** The Merkle root is computed over
content-addressed claim *ids*, not over claim text. A claim id is a hash of
the claim's content, so editing the text of a row without recomputing its id
leaves the id — and therefore the root — untouched. That is not a bug in the
hashing scheme; it is what content addressing *is*. It does mean the root
verifies identity and structure, not text. A root that "detected" a text edit
would have to be hashing the text, which would break the property that makes
recompiling unchanged bytes reproduce the same root.

**Why that leaves a hole.** The project's thesis is that claims resolve to
exact source offsets and that this is mechanically checkable. Two of the six
tampers above — editing `claims.text` and editing `sources.content` — are
invisible to both the root and the auditor. You can change what a claim says,
or change the document it came from, and the artifact still reports
`clean: true` with a matching root.

`evidence.text` edits *are* caught, because the auditor re-slices the source
and compares. So the exact-offset machinery works for evidence spans and not
for the two things it was supposed to be the backstop for.

**The fix is cheap and was already in the schema.** `sources` has a `checksum`
column, written at insert time, and *nothing ever reads it back*. One SQL
comparison in the auditor closes the `sources.content` hole. For
`claims.text`, the invariant is that a claim's text must equal the
concatenation of its evidence spans, which is checkable with the same
Python-slicing approach the auditor already uses for spans (ADR-014 had to
switch to Python slicing because SQLite's `substr()` stops at a NUL).

Both are in the auditor, not in the store. The store is the thing being
audited; a check that lives there is not an independent check.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from ganymede4.compile.compiler import SourceSpec, compile_corpus  # noqa: E402
from ganymede4.knowledge.store import Store  # noqa: E402

SENTENCE = (
    "The provenance of every claim is content addressed so that a later write "
    "cannot silently replace an earlier belief."
)
PY = sys.executable
AUDIT = REPO / "scripts" / "audit_provenance.py"


@pytest.fixture()
def built(tmp_path):
    db = tmp_path / "a.db"
    with Store(str(db)) as store:
        compile_corpus(
            store,
            [
                SourceSpec(f"mem://s/{i}", f"{SENTENCE} Document {i}.", source_type="reddit_comment")
                for i in range(3)
            ],
            transaction_time="2026-01-01T00:00:00Z",
        )
    return db


def _audit(db) -> tuple[int, dict]:
    r = subprocess.run([PY, str(AUDIT), str(db)], capture_output=True, text=True)
    try:
        return r.returncode, json.loads(r.stdout)
    except json.JSONDecodeError:  # pragma: no cover
        pytest.fail(f"auditor produced no JSON: {r.stdout[-800:]} {r.stderr[-800:]}")


def _tamper(db, sql: str) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.execute(sql)
        conn.commit()


# ------------------------------------------------ the two real gaps
def test_edited_source_content_is_detected(built):
    """A source document is the ground truth. Changing it behind the
    pipeline's back must not leave the artifact reporting itself clean.

    Before this was fixed the auditor returned clean: true, exit 0. The
    `checksum` column was already in the schema and already written -- nothing
    read it back.
    """
    assert _audit(built)[0] == 0, "fixture should be clean before tampering"
    _tamper(built, "UPDATE sources SET content = content || ' tampered'")
    code, report = _audit(built)
    assert code == 1
    assert report["clean"] is False
    assert report["source_checksum_mismatches"], (
        "an edited source was not reported as a checksum mismatch"
    )


def test_edited_claim_text_is_detected(built):
    """A claim's text must be exactly the source span it cites. If they can
    diverge, the artifact will answer a question with a sentence that appears
    nowhere in any document."""
    _tamper(built, "UPDATE claims SET text = text || ' tampered'")
    code, report = _audit(built)
    assert code == 1
    assert report["clean"] is False
    assert report["claim_text_mismatches"], (
        "a claim whose text no longer matches its cited source span was not reported"
    )


# ------------------------------------------- must not break the honest cases
def test_a_clean_artifact_reports_no_mismatches(built):
    """The new checks must not fire on an untouched artifact. A check that
    fires on everything detects nothing."""
    code, report = _audit(built)
    assert code == 0
    assert report["source_checksum_mismatches"] == []
    assert report["claim_text_mismatches"] == []


def test_every_claim_in_a_real_artifact_satisfies_both_invariants(built):
    """The claim-text invariant has to hold for *every* claim, not just the
    one a fixture happens to have. Derived claims have no evidence rows, so
    they are exempt by construction -- stated here because that exemption is a
    decision, not an oversight."""
    _, report = _audit(built)
    assert report["claim_text_mismatches"] == []

    with Store(str(built)) as store:
        derived = [
            r["id"] for r in store.db.execute("SELECT id FROM claims WHERE state = 'derived'")
        ]
        for claim_id in derived:
            row = store.get_claim(claim_id)
            assert row is not None
            ev = store.evidence_for(claim_id)
            assert ev == [], (
                "this fixture was expected to have a derived claim with no evidence"
            )


def test_deleting_a_source_is_still_detected(built):
    """Reordering the checks must not lose an existing detection. `evidence`
    rows carry a foreign key to sources, so this is the auditor's job."""
    _tamper(built, "DELETE FROM sources WHERE id = (SELECT id FROM sources LIMIT 1)")
    code, report = _audit(built)
    assert code == 1
    assert report["clean"] is False


def test_state_tampering_is_still_detected(built):
    """ADR-021's detection must survive."""
    _tamper(built, "UPDATE claims SET state = 'supported'")
    code, report = _audit(built)
    assert code == 1
    assert report["clean"] is False


# --------------------------------------- the auditor stays independent
def test_the_new_checks_do_not_import_the_package():
    """Both new checks were added to `scripts/audit_provenance.py`, which must
    keep importing nothing from `ganymede4`. Recomputing a claim's expected
    text needs the *rule*, not the implementation -- and copying the rule into
    the auditor is the price of independence."""
    src = AUDIT.read_text()
    assert "import ganymede4" not in src
    assert "from ganymede4" not in src
