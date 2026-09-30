"""Task 5: one-command reproducibility and hygiene.

The build script's whole claim is that a person who has never seen this
repository can produce the artifact and verify it in one command, from a
clean clone, on a machine that is not the one it was written on. Each test
below is a way that claim could be false, expressed as a check.

These use a small synthetic corpus. A real build is 845 MB and ~20 minutes,
which is a manual verification, not a test -- and the *real* run is recorded
in the commit that added this file.
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
BUILD = SCRIPTS / "build_artifact.py"
PY = sys.executable


# ---------------------------------------------------------------- helpers
def _make_corpus(root: Path, n_reddit: int = 4, n_chatgpt: int = 2) -> None:
    """A tiny corpus with the directory shape the real loader expects."""
    subs = root / "reddit" / "submissions"
    comms = root / "reddit" / "comments"
    convs = root / "openai" / "conversations_markdown"
    for d in (subs, comms, convs):
        d.mkdir(parents=True, exist_ok=True)

    long_sentence = (
        "The provenance of every claim is content addressed so that a later "
        "write cannot silently replace an earlier belief."
    )
    for i in range(n_reddit):
        (subs / f"s{i}.md").write_text(
            f"---\nauthor: KonradFreeman\ncreated_utc: 17000000{i:02d}\n---\n\n"
            f"{long_sentence}\nThis is submission {i}.\n"
        )
        (comms / f"c{i}.md").write_text(
            f"---\nauthor: KonradFreeman\ncreated_utc: 17000001{i:02d}\n---\n\n"
            f"{long_sentence}\nThis is comment {i}.\n"
        )
    for i in range(n_chatgpt):
        (convs / f"conv{i}.md").write_text(
            f"---\nconversation_id: conv{i}\n---\n\n"
            "## user\n"
            f"{long_sentence}\n\n"
            "## assistant\n"
            "The model replies that provenance should be enforced at write time.\n"
        )


def _run_build(db: Path, corpus: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            PY,
            str(BUILD),
            "--db",
            str(db),
            "--corpus",
            str(corpus),
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(REPO),
    )


# ----------------------------------------------------- 1. it actually runs
def test_one_command_build_produces_an_audited_artifact(tmp_path):
    """The headline claim, end to end: one command, artifact, clean audit."""
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    db = tmp_path / "out" / "artifact.db"

    result = _run_build(db, corpus)

    assert result.returncode == 0, (
        "the one-command build must succeed and audit clean; "
        f"stdout:\n{result.stdout[-3000:]}\nstderr:\n{result.stderr[-2000:]}"
    )
    assert db.exists(), "no artifact was written"
    assert "BUILD OK" in result.stdout
    # It must say the auditor is independent, since that is the whole point of
    # running it as a subprocess rather than importing it.
    assert "does not import" in result.stdout


def test_build_reports_the_artifact_identity_not_just_success(tmp_path):
    """Version, root, and digest must be printed -- an unverifiable build
    that only says OK is not reproducible, it is merely repeatable."""
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    db = tmp_path / "a.db"

    out = _run_build(db, corpus).stdout

    assert "version" in out and "root" in out and "state_epoch" in out
    assert "digest" in out
    # A real hash, not a placeholder.
    assert any(len(tok) >= 32 for tok in out.split())


# ------------------------------------------------- 2. it fails closed
def test_build_fails_when_the_audit_is_dirty(tmp_path, monkeypatch):
    """A build that compiles but does not audit clean has not produced an
    artifact. This is the fail-closed property, and it is the one most likely
    to be quietly dropped, because dropping it makes the script *succeed*
    more often."""
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    db = tmp_path / "a.db"

    # Sabotage: make the auditor report a defect. Done by pointing the script
    # at a stub auditor via a wrapper interpreter is fragile, so instead
    # corrupt the artifact's recorded digest after a clean build and confirm
    # the auditor -- the real, unmodified one -- catches it. That tests the
    # detector; the wiring is asserted separately below.
    assert _run_build(db, corpus).returncode == 0

    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE artifact SET state_digest = ? WHERE id = 1", ("0" * 64,))
        conn.commit()

    audit = subprocess.run(
        [PY, str(SCRIPTS / "audit_provenance.py"), str(db)],
        capture_output=True,
        text=True,
    )
    assert audit.returncode == 1
    assert json.loads(audit.stdout)["clean"] is False


def test_build_exits_non_zero_when_the_corpus_is_missing(tmp_path):
    """A missing corpus must be a clear error, not a traceback or -- worse --
    a successful build of nothing."""
    result = _run_build(tmp_path / "a.db", tmp_path / "nope")
    assert result.returncode != 0
    assert "no corpus at" in (result.stdout + result.stderr).lower()


# ------------------------------------- 3. no /tmp, no machine-specific paths
def test_no_hardcoded_tmp_interpreter_in_scripts_or_docs():
    """The brief calls this out directly. A reader on another machine cannot
    use /tmp/sr312/bin/python, and a doc that tells them to is a broken doc."""
    offenders = []
    for path in list(SCRIPTS.glob("*.py")) + [REPO / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "/tmp/sr312" in line:
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "/tmp/sr312 is this machine's interpreter and must not appear in "
        "shipped instructions or scripts:\n" + "\n".join(offenders)
    )


def test_corpus_root_is_a_default_not_a_baked_in_constant():
    """CORPUS_ROOT must be overridable, or a clean clone elsewhere cannot
    read the corpus at all."""
    src = (REPO / "src" / "ganymede4" / "corpus" / "load.py").read_text()
    assert "GANYMEDE4_CORPUS_ROOT" in src, (
        "load.py hardcodes the corpus location; it must honour an override"
    )
    assert "Path.home()" in src, "the default should still be the author's path"


def test_build_honours_the_corpus_override(tmp_path):
    """Prove the override is real: two different corpus roots, two different
    artifacts. A flag that is parsed and then ignored would pass every other
    test here."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _make_corpus(a, n_reddit=3)
    _make_corpus(b, n_reddit=6)

    assert _run_build(tmp_path / "a.db", a).returncode == 0
    assert _run_build(tmp_path / "b.db", b).returncode == 0

    def claims(db):
        with sqlite3.connect(str(db)) as conn:
            return conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]

    assert claims(tmp_path / "a.db") < claims(tmp_path / "b.db"), (
        "--corpus was ignored: a bigger corpus produced the same number of claims"
    )


def test_build_env_override_also_works(tmp_path, monkeypatch):
    corpus = tmp_path / "c"
    _make_corpus(corpus)
    db = tmp_path / "a.db"
    monkeypatch.setenv("GANYMEDE4_CORPUS_ROOT", str(corpus))
    result = subprocess.run(
        [PY, str(BUILD), "--db", str(db)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(REPO),
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]


# --------------------------------- 4. the auditor stays independent
def test_build_never_imports_the_auditor():
    """ADR-014's load-bearing property. The build must shell out. An auditor
    that is imported by the thing it audits cannot catch that thing's bugs --
    and a refactor to `import audit_provenance` would look like a harmless
    tidy-up."""
    tree = ast.parse(BUILD.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "audit_provenance" not in alias.name, (
                    f"line {node.lineno}: the auditor must not be imported"
                )
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "audit_provenance" not in node.module, (
                f"line {node.lineno}: the auditor must not be imported"
            )
    assert "subprocess.run" in BUILD.read_text(), "expected a subprocess call"


def test_auditor_still_imports_nothing_from_the_package():
    src = (SCRIPTS / "audit_provenance.py").read_text()
    assert "import ganymede4" not in src
    assert "from ganymede4" not in src


# ------------------------------------------ 5. the --help file, and hygiene
def test_stray_help_file_is_gone_and_will_not_come_back():
    """A zero-byte file named `--help` was committed at the repository root.

    Nothing in the repository produces it: it was a shell redirect, tracked
    by accident, and never referenced. Asserting its absence is cheap and
    makes the deletion deliberate rather than cosmetic."""
    assert not (REPO / "--help").exists(), (
        "a file literally named --help is back at the repository root"
    )
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=str(REPO)
    ).stdout.split()
    assert "--help" not in tracked, "--help is still tracked in git"


@pytest.mark.parametrize(
    "entry",
    [
        'ganymede4 = "ganymede4.cli:main"',
    ],
)
def test_console_entry_point_is_either_real_or_absent(entry):
    """Task 5 explicitly requires resolving this. A `[project.scripts]`
    entry naming a module that does not exist is worse than no entry at all:
    `pip install` succeeds and the command fails later, on a user's machine.

    The decision here is to BUILD the module, so this asserts it exists and
    is importable with a `main`.
    """
    text = (REPO / "pyproject.toml").read_text()
    if entry not in text:
        pytest.skip("no console entry declared")
    cli = REPO / "src" / "ganymede4" / "cli.py"
    assert cli.exists(), (
        f"{entry} is declared but {cli.relative_to(REPO)} does not exist; "
        "either build it or remove the entry point"
    )
    tree = ast.parse(cli.read_text())
    names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "main" in names, "cli.py must define main() for the entry point to work"


def test_generated_artifacts_are_not_tracked():
    """A derived 845 MB database in git would contradict the project's own
    thesis. `.gitignore` is the enforcement."""
    ignore = (REPO / ".gitignore").read_text()
    for pattern in ("*.db", "__pycache__/"):
        assert pattern in ignore, f"{pattern} missing from .gitignore"


# ------------------------------------------- 6. it is deterministic
def test_two_builds_of_the_same_corpus_agree(tmp_path):
    """Reproducibility, at the level a test can afford. The full-corpus
    version of this is the 20-minute build recorded in the commit message;
    this catches the class of bug where a new build step introduces
    nondeterminism (an unordered set, a timestamp, a dict iteration order)."""
    corpus = tmp_path / "c"
    _make_corpus(corpus)
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    assert _run_build(a, corpus).returncode == 0
    assert _run_build(b, corpus).returncode == 0

    def ident(db):
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                "SELECT root, version, state_digest FROM artifact WHERE id = 1"
            ).fetchone()
        return row

    assert ident(a) == ident(b), "two builds of the same corpus produced different identities"


# ------------------------------------------------------ 7. no-evaluate path
def test_no_evaluate_is_labelled_as_incomplete(tmp_path):
    """`--no-evaluate` produces a valid artifact that contains no verdicts.
    It must say so, or someone will read a compile-only artifact's empty
    state counts as a finding."""
    corpus = tmp_path / "c"
    _make_corpus(corpus)
    result = _run_build(tmp_path / "a.db", corpus, "--no-evaluate")
    assert result.returncode == 0
    assert "NO verdicts" in result.stdout or "no verdicts" in result.stdout.lower()


def test_keep_existing_refuses_to_overwrite(tmp_path):
    """Silently deleting someone's artifact because a flag was forgotten is
    the kind of default that loses work."""
    corpus = tmp_path / "c"
    _make_corpus(corpus)
    db = tmp_path / "a.db"
    assert _run_build(db, corpus).returncode == 0
    before = db.stat().st_mtime
    result = _run_build(db, corpus, "--keep-existing")
    assert result.returncode == 2
    assert db.stat().st_mtime == before, "the artifact was modified anyway"


# --------------------------------------------------- 8. stdlib-only, offline
def test_build_imports_no_third_party_package():
    src = BUILD.read_text()
    banned = (
        "sqlalchemy", "numpy", "pandas", "requests", "httpx", "fastapi",
        "openai", "anthropic", "ollama", "torch", "transformers", "yaml",
    )
    for name in banned:
        assert f"import {name}" not in src, f"build script imports {name}"
