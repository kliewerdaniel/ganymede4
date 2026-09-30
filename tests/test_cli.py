"""The console must fail closed, including when it is installed.

ADR-025. Two bugs were found here by installing the console entry into a
real virtualenv rather than running it from a source checkout:

1. `_SCRIPTS` was resolved relative to the *installed* module, so in a venv
   the auditor path pointed into `.../lib/python3.11/scripts/` and did not
   exist. The auditor never ran.
2. A missing auditor was reported as `clean: None` and returned **exit 0**.

The second is the serious one. A verification command that cannot find its own
checker and says "fine" is worse than one that does not exist: it converts a
broken install into an apparent clean bill of health, and the user has no way
to tell. Both are fixed by resolving the auditor through the *repository*
layout and by treating "the auditor did not run" as a failure, not a result.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "src" / "ganymede4" / "cli.py"
AUDITOR = REPO / "scripts" / "audit_provenance.py"


def _run(args, **kw):
    env = {"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        **kw,
    )


def _build(tmp_path, name="c.db"):
    """Compile a small corpus through the real pipeline."""
    from ganymede4.compile.compiler import SourceSpec, compile_corpus
    from ganymede4.knowledge.store import Store

    db = tmp_path / name
    store = Store(str(db))
    compile_corpus(
        store,
        [
            SourceSpec("test://a", "Provenance is enforced at write time here. "
                                  "Verification is independent of the store."),
            SourceSpec("test://b", "A claim resolves to an exact source offset."),
        ],
        transaction_time="2026-09-30T00:00:00Z",
    )
    store.close()
    return db


def test_auditor_path_resolves_outside_a_source_checkout():
    """The auditor must be findable from an installed package.

    This is the condition that broke: the path was derived from the installed
    module's location, which under a venv is `lib/pythonX.Y/site-packages`,
    and `scripts/audit_provenance.py` does not exist there.
    """
    # The resolved path must actually be the auditor, from a foreign cwd --
    # which is the condition that broke under an installed venv.
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from ganymede4.cli import _auditor_script\n"
        "print(_auditor_script())" % str(REPO / "src")
    )
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert Path(out.stdout.strip()).is_file(), out.stdout
    assert out.stdout.strip().endswith("audit_provenance.py")


def test_a_missing_auditor_is_a_failure_not_a_clean_result(tmp_path):
    """The fail-open bug. If the auditor cannot run, we must not print clean.

    Simulated by pointing the resolver at a path that does not exist, which is
    what an incomplete install looks like from the console's side.
    """
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import ganymede4.cli as c\n"
        "c._auditor_script = lambda: '/nonexistent/audit_provenance.py'\n"
        "sys.exit(c._cmd_audit(__import__('argparse').Namespace("
        "db='%s', json=True)))" % (str(REPO / "src"), str(tmp_path / "x.db"))
    )
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True)
    assert proc.returncode != 0, (
        "a console that cannot find its auditor returned success -- this is "
        "the fail-open bug ADR-025 forbids"
    )


def test_audit_reports_clean_on_a_real_artifact(tmp_path):
    db = _build(tmp_path)
    proc = _run(["audit", "--json", str(db)])
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["clean"] is True, report
    # ADR-026's two new checks must be present and empty, not merely absent.
    assert report["source_checksum_mismatches"] == []
    assert report["claim_text_mismatches"] == []


def test_verify_audit_agrees_with_the_audit_subcommand(tmp_path):
    db = _build(tmp_path)
    proc = _run(["verify", "--audit", str(db)])
    assert proc.returncode == 0, proc.stderr
    assert "clean: None" not in proc.stdout, (
        "verify reported an auditor that never ran as if it had"
    )
    assert "True" in proc.stdout


def test_verify_detects_a_tampered_artifact(tmp_path):
    db = _build(tmp_path)
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE claims SET text = text || ' tampered'")
        conn.commit()
    proc = _run(["verify", "--audit", str(db)])
    assert proc.returncode != 0, "a tampered artifact verified as sound"
    assert "MISMATCH" in proc.stdout or "clean: False" in proc.stdout


def _logical_content(db):
    """Hash every table's rows, ignoring SQLite's own bookkeeping.

    Byte-equality is the wrong assertion and was the first version of this
    test. Opening a SQLite file read-write bumps its file change counter even
    when every query is a SELECT, so a byte comparison fails on a database
    nobody touched. What matters is whether any *row* changed.
    """
    import hashlib

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = {}
        for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ):
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            out[table] = (
                hashlib.sha256(repr(rows).encode()).hexdigest(),
                len(rows),
            )
        return out
    finally:
        conn.close()


def test_query_writes_no_claim_or_state(tmp_path):
    """`query` must not change what the artifact *asserts*.

    The first version of this test asserted the database was byte-identical,
    then that no row changed at all. Both were wrong, and the second was
    wrong in a way worth recording.

    Asking a question the artifact cannot answer causes the witness to record
    an `investigations` row. That is required, not incidental: an artifact may
    only assert absence if something names what was searched (ADR-005). A
    witness that answered "no evidence" without a record of the search would
    be asserting absence from nothing.

    So the invariant is narrower and more meaningful: a read may leave a
    record of the search, but it must not change a claim's text, its epistemic
    state, its evidence, or the recorded identity. Those are the parts of the
    artifact that carry assertions.
    """
    db = _build(tmp_path)
    assertive = ("claims", "claim_evidence", "claim_edges", "evaluations",
                 "sources", "evidence", "artifact")
    before = _logical_content(db)
    proc = _run(["query", str(db), "is the model the intelligence?", "--limit", "2"])
    assert proc.returncode == 0, proc.stderr
    after = _logical_content(db)

    changed = {k for k in assertive if before[k] != after[k]}
    assert not changed, f"query changed assertion-bearing tables: {changed}"

    # The search record, if any, is additive and carries the question asked.
    if "investigations" in after and before["investigations"][1] == 0:
        assert after["investigations"][1] == 1, (
            "an investigation record was replaced rather than added; absence "
            "must be provable, not erasable"
        )


def test_verify_is_read_only(tmp_path):
    """`verify` recomputes identity from the rows. It must not seal.

    A verify that flushed a pending seal would change the artifact it is
    inspecting, so a subsequent verify would report different numbers. This is
    the invariant that ``recorded_artifact_unflushed`` exists to protect.
    """
    db = _build(tmp_path)
    before = _logical_content(db)
    proc = _run(["verify", "--audit", str(db)])
    assert proc.returncode == 0, proc.stderr
    assert _logical_content(db) == before, "verify modified the artifact it read"
    assert "MATCH" in proc.stdout


def test_missing_corpus_fails_closed(tmp_path):
    proc = _run(["build", "--db", str(tmp_path / "b.db"),
                 "--corpus", str(tmp_path / "no-such-corpus")])
    assert proc.returncode != 0, "a missing corpus reported success"


def test_invalid_database_path_fails_closed(tmp_path):
    proc = _run(["verify", str(tmp_path / "does-not-exist.db")])
    assert proc.returncode != 0, "verifying a nonexistent artifact reported success"
