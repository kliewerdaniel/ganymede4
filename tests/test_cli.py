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

import pytest
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


def _installed_console(tmp_path, extra_env=None):
    """Install the console into a real venv, in the *installed* layout.

    This exists because the first version of the auditor-path test was
    vacuous. It called ``_auditor_script()`` from a source checkout, where the
    answer is correct by construction -- so sabotaging the resolver to the
    broken installed-module path left the test green. The bug only appears
    when the module lives in ``site-packages``, with no ``scripts/`` beside
    it, which is exactly what a venv install produces.

    So the test installs. It is slower and it needs no network (pip builds
    from the local tree with setuptools already present), and in exchange it
    is falsifiable.
    """
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)],
                   capture_output=True, timeout=300)
    py = venv / "bin" / "python"
    inst = subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", "--no-deps", str(REPO)],
        capture_output=True, text=True, timeout=600,
    )
    if inst.returncode != 0:
        pytest.skip(f"cannot install into a venv here: {inst.stderr[-200:]}")
    # Prove it is really the installed copy, not the checkout leaking in.
    probe = subprocess.run(
        [str(py), "-c", "import ganymede4.cli as c; print(c.__file__)"],
        capture_output=True, text=True,
    )
    assert "site-packages" in probe.stdout, probe.stdout
    return py


def test_auditor_is_found_from_an_installed_console(tmp_path):
    """The bug this pins: under a venv there is no `scripts/` next to the module."""
    py = _installed_console(tmp_path)
    out = subprocess.run(
        [str(py), "-c",
         "import ganymede4.cli as c; print(c._auditor_script())"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert Path(out.stdout.strip()).is_file(), out.stdout


def test_installed_console_actually_runs_the_auditor(tmp_path):
    """The end-to-end proof, and the test that failed open before.

    Before the fix this printed ``clean: None`` and exited 0 in a real
    install: the auditor path pointed into site-packages, the subprocess
    failed, and the failure was reported as a clean result.
    """
    py = _installed_console(tmp_path)
    db = _build(tmp_path, "inst.db")
    proc = subprocess.run([str(py), "-m", "ganymede4.cli", "audit", "--json",
                           str(db)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["clean"] is True, report
    assert report["source_checksum_mismatches"] == []
    assert report["claim_text_mismatches"] == []


def test_installed_verify_audit_is_not_none(tmp_path):
    """`clean: None` means the auditor never ran. That string must not appear."""
    py = _installed_console(tmp_path)
    db = _build(tmp_path, "inst2.db")
    proc = subprocess.run([str(py), "-m", "ganymede4.cli", "verify", "--audit",
                           str(db)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "clean: None" not in proc.stdout, proc.stdout
    assert "clean: True" in proc.stdout, proc.stdout


def test_a_missing_auditor_is_a_failure_not_a_clean_result(tmp_path):
    """The fail-open bug. If the auditor cannot run, we must not print clean.

    Two distinct ways the auditor can fail to run, and both must fail closed:

    a) the resolver raises, because there is no auditor anywhere;
    b) the resolver returns a path that does not exist, or the subprocess
       produces nothing parseable.

    The first version of this test only covered (a), by replacing the
    resolver with a lambda returning a nonexistent path -- which is (b), and
    which took a *different* branch than the sabotage it was written for. It
    passed whether or not the ``except`` handler existed. Both are covered now.
    """
    # This drives a real helper *file*, not `python -c`. Embedding code in a
    # command line means two layers of quoting, and two earlier versions of
    # this test failed on the quoting rather than on the behaviour.
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import ganymede4.cli as c\n"
        "mode = sys.argv[3]\n"
        "if mode == 'raises':\n"
        "    def boom():\n"
        "        raise FileNotFoundError('nowhere')\n"
        "    c._auditor_script = boom\n"
        "else:\n"
        "    c._auditor_script = lambda: '/nonexistent/audit_provenance.py'\n"
        "code, report = c._audit(sys.argv[2])\n"
        "print('CODE', code, 'REPORT', report)\n"
        "sys.exit(code)\n"
    )
    for label, mode in (
        ("resolver raises", "raises"),
        ("resolver returns a missing path", "missing-path"),
    ):
        proc = subprocess.run(
            [sys.executable, str(driver), str(REPO / "src"),
             str(tmp_path / "x.db"), mode],
            capture_output=True, text=True,
        )
        assert proc.returncode != 0, (
            f"{label}: the console returned success without an auditor -- "
            "this is the fail-open bug ADR-025 forbids"
        )
        assert "REPORT {}" in proc.stdout, proc.stdout


def test_an_unparseable_auditor_is_a_failure(tmp_path):
    """Output that is not a report is not a pass.

    A crashed or truncated auditor still exits non-zero, but the console must
    not depend on that: a wrapper that swallows the error and prints a
    traceback would exit 0 with unparseable stdout. Treating that as a clean
    result is the original fail-open bug wearing a different hat.
    """
    fake = tmp_path / "garbage_auditor.py"
    fake.write_text(
        "import sys\n"
        "sys.stdout.write('Traceback (most recent call last): ...')\n"
        "sys.exit(0)\n"
    )
    driver = tmp_path / "drive.py"
    driver.write_text(
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import ganymede4.cli as c\n"
        "c._auditor_script = lambda: sys.argv[3]\n"
        "rc, report = c._audit(sys.argv[2])\n"
        "print('RC', rc, 'REPORT', report)\n"
        "sys.exit(rc)\n"
    )
    proc = subprocess.run(
        [sys.executable, str(driver), str(REPO / "src"),
         str(tmp_path / "x.db"), str(fake)],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0, (
        "unparseable auditor output was reported as success"
    )
    assert "REPORT {}" in proc.stdout, proc.stdout


def test_an_empty_json_report_is_a_failure(tmp_path):
    """A report that parses to ``{}`` is not a report.

    This is the narrow case the ``if not report`` guard exists for, and it is
    reachable: the unparseable-output test above never gets there, because
    garbage stdout trips ``JSONDecodeError`` first. An auditor that emits an
    empty object is well-formed JSON carrying no verdict at all.
    """
    fake = tmp_path / "empty_auditor.py"
    fake.write_text("import json, sys\nsys.stdout.write('{}')\nsys.exit(0)\n")
    driver = tmp_path / "drive.py"
    driver.write_text(
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import ganymede4.cli as c\n"
        "c._auditor_script = lambda: sys.argv[3]\n"
        "rc, report = c._audit(sys.argv[2])\n"
        "print('RC', rc, 'REPORT', report)\n"
        "sys.exit(rc)\n"
    )
    proc = subprocess.run(
        [sys.executable, str(driver), str(REPO / "src"),
         str(tmp_path / "x.db"), str(fake)],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0, (
        "an empty report was accepted as a verdict"
    )


def test_force_controls_overwriting_and_the_flag_is_not_inverted(tmp_path):
    """`--force` must mean overwrite.

    It meant the opposite in the first version of this console: `--force`
    *added* ``--keep-existing`` to the build script's argv, which is the flag
    that forbids overwriting. Every build through the console therefore
    refused to write anywhere, and the error named a flag the user had never
    passed. A flag whose name contradicts its effect is worse than no flag,
    because it teaches the reader that error messages are not to be believed.
    """
    py = _installed_console(tmp_path)
    corpus = tmp_path / "corpus"
    (corpus / "reddit" / "comments").mkdir(parents=True)
    (corpus / "reddit" / "comments" / "a.md").write_text(
        "---\nauthor: KonradFreeman\n---\n\nProvenance is enforced here.\n"
    )
    db = tmp_path / "f.db"
    common = ["-m", "ganymede4.cli", "build", "--db", str(db),
              "--corpus", str(corpus), "--python", str(py)]

    first = subprocess.run([str(py), *common], capture_output=True,
                           text=True, cwd=str(tmp_path), timeout=300)
    assert first.returncode == 0, first.stdout[-1500:] + first.stderr[-800:]
    assert db.is_file()
    stamp = db.stat().st_mtime_ns

    # Without --force, an existing artifact must be left alone.
    second = subprocess.run([str(py), *common], capture_output=True,
                            text=True, cwd=str(tmp_path), timeout=300)
    assert second.returncode != 0, "an existing artifact was overwritten silently"
    assert db.stat().st_mtime_ns == stamp, "the artifact was modified"

    # With --force, it must be replaced -- and must say so in help text that
    # matches the behaviour.
    third = subprocess.run([str(py), *common, "--force"], capture_output=True,
                           text=True, cwd=str(tmp_path), timeout=300)
    assert third.returncode == 0, third.stdout[-1500:] + third.stderr[-800:]
    assert db.stat().st_mtime_ns != stamp, "--force did not overwrite"


def test_build_finds_its_script_from_an_installed_console(tmp_path):
    """`build` had the same path bug as `audit`, and would have failed closed
    but unhelpfully. It must resolve its script the same way."""
    py = _installed_console(tmp_path)
    corpus = tmp_path / "corpus"
    (corpus / "reddit" / "comments").mkdir(parents=True)
    (corpus / "reddit" / "comments" / "a.md").write_text(
        "---\nauthor: KonradFreeman\n---\n\n"
        "Provenance is enforced at write time here.\n"
    )
    proc = subprocess.run(
        [str(py), "-m", "ganymede4.cli", "build",
         "--db", str(tmp_path / "b.db"), "--corpus", str(corpus),
         "--python", str(py)],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-1000:]
    assert (tmp_path / "b.db").is_file()


def test_a_disagreeing_exit_code_cannot_be_overruled(tmp_path):
    """The auditor's exit code and its report body must both count.

    Two shapes are covered, because the console has to be right about both:

    * the report says ``clean: true`` but the auditor exited non-zero. That
      means the auditor found something it could not express in the JSON --
      a crash after writing, or a wrapper that failed. The exit code is the
      harder evidence and must win.
    * the report says ``clean: false`` but the auditor exited 0. That means a
      defect is recorded and the exit code is wrong. The console must still
      fail; a recorded defect is a defect regardless of what the exit code
      claims.

    Driving a fake auditor keeps both cases reachable. A real auditor never
    produces the first combination, which is exactly why a test is needed:
    it is the case that cannot be discovered by running the thing.
    """
    fake = tmp_path / "fake_auditor.py"
    template = tmp_path / "fake_template.py"
    template.write_text(
        "import json, sys\n"
        "sys.stdout.write(json.dumps({'clean': %s, 'synthetic': True}))\n"
        "sys.exit(%s)\n"
    )
    cases = {
        "clean body, nonzero exit": ("True", "1"),
        "dirty body, zero exit": ("False", "0"),
    }
    for label, (clean, code) in cases.items():
        fake.write_text(template.read_text() % (clean, code))
        driver = tmp_path / "drive.py"
        driver.write_text(
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import ganymede4.cli as c\n"
            "c._auditor_script = lambda: sys.argv[3]\n"
            "rc, report = c._audit(sys.argv[2])\n"
            "print('RC', rc, 'CLEAN', report.get('clean'))\n"
            "sys.exit(rc)\n"
        )
        proc = subprocess.run(
            [sys.executable, str(driver), str(REPO / "src"),
             str(tmp_path / "x.db"), str(fake)],
            capture_output=True, text=True,
        )
        assert proc.returncode != 0, (
            f"{label}: the console reported success; a disagreement between "
            "the exit code and the report body must fail closed in both "
            "directions"
        )


def test_a_tampered_artifact_makes_the_installed_console_fail(tmp_path):
    """Fail-closed must survive installation too, not only in a checkout."""
    py = _installed_console(tmp_path)
    db = _build(tmp_path, "tamper.db")
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE claims SET text = text || ' tampered'")
        conn.commit()
    proc = subprocess.run([str(py), "-m", "ganymede4.cli", "audit", "--json",
                           str(db)], capture_output=True, text=True)
    assert proc.returncode != 0, (
        "an installed console reported success on a tampered artifact"
    )
    report = json.loads(proc.stdout)
    assert report["claim_text_mismatches"], report
    assert report["clean"] is False


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
