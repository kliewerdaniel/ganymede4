"""An eight-hour evaluation must be resumable, or it is a trap.

A full-corpus rebuild takes roughly eight hours on this machine. That is
long enough that it will be interrupted -- by a reaped background job, a
closed laptop, a full disk, an operator with other work. When that happens
the only question that matters is whether the work already done survives.

Today it does not. ``Evaluator.evaluate_all`` is a single list comprehension
over every claim with no progress recorded anywhere, so an interruption at 6%
discards the whole run and the next attempt starts from zero again. Two
interruptions means twelve hours for one artifact.

This is the same class of defect as ADR-021, which fixed a re-seal that
depended on the caller remembering. There, a fix relying on the caller was
"the same bug again". Here the fix is not about correctness of the finished
artifact at all -- a resumed run must produce *exactly* the artifact an
uninterrupted run would -- it is about not throwing away hours of work.

Resume is sound because of two properties the code already has:

1. ``evaluate_all`` iterates ``sorted(self._norms)``, a deterministic order
   independent of insertion order and of run history.
2. ``Store.record_evaluation`` writes a content-addressed ``evl-`` id with
   ``INSERT OR IGNORE``, so re-evaluating a claim that already has a
   verdict is a no-op rather than a duplicate.

Together those make skipping already-evaluated claims *indistinguishable*
from evaluating them. Not equivalent -- indistinguishable is the stronger
claim, and it is the one that matters: the artifact must not record that a
resume happened.

These tests are written to fail before the change.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from ganymede4.compile.compiler import SourceSpec, compile_corpus
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.store import Store

TT = "2026-09-28T00:00:00Z"
ROOT = Path(__file__).resolve().parent.parent

#: Enough distinct claims that "the first half" is a real half. The compiler
#: silently drops many segments, so a three-sentence-per-document fixture
#: yields two claims and a resume test over it is vacuous -- which is exactly
#: the trap this file is about.
_A = "Alpha bravo charlie delta echo foxtrot golf hotel india juliet."
_B = "Kilo lima mike november oscar papa quebec romeo sierra tango."
_C = "Uniform victor whiskey xray yankee zulu alpha bravo charlie delta."
_D = "Echo foxtrot golf hotel india juliet kilo lima mike november."

DOCS = [
    ("a.txt", _A + " " + _B),
    ("b.txt", "The model is not intelligent. " + _C),
    ("c.txt", _D + " Water is wet. Fire is hot."),
    ("d.txt", "Cats are mammals. " + _A),
]


def _build():
    store = Store()
    specs = [SourceSpec(uri=uri, content=body) for uri, body in DOCS]
    art = compile_corpus(store, specs, transaction_time=TT)
    return store, Evaluator(store, art.manifest)


def _claims(store):
    return sorted(r["id"] for r in store.db.execute("SELECT id FROM claims"))


def test_evaluate_all_still_returns_verdicts():
    """The public contract must not change shape.

    An earlier version of this work changed ``evaluate_all`` to return claim
    ids instead of ``Verdict`` objects. That broke 45 call sites across six
    test modules, all of which read ``.subject_id`` off the result. The suite
    caught it, but only because unrelated tests happened to use the method;
    a caller outside this repo would have found out at runtime.

    So the contract is pinned explicitly here. Resume needs ids, and
    ``evaluate_ids`` provides them, but ``evaluate_all`` is the documented
    entry point and keeps its meaning.
    """
    from ganymede4.knowledge.evaluator import Verdict

    store, ev = _build()
    out = ev.evaluate_all(apply=True)
    assert out, "fixture evaluated nothing"
    assert all(isinstance(v, Verdict) for v in out), (
        "evaluate_all must keep returning Verdict objects; "
        "use evaluate_ids for claim ids"
    )
    assert {v.subject_id for v in out} == set(_claims(store))

    # The two methods must agree about what a full run covers.
    assert ev.evaluate_ids(apply=True) == _claims(store)


def test_evaluate_all_reports_which_claims_it_covered():
    """A run must be able to say how far it got, in claim ids."""
    store, ev = _build()
    done = ev.evaluate_ids(apply=True, progress_every=1)
    assert set(done) == set(_claims(store))


def test_resuming_evaluates_only_what_is_missing():
    """The second pass must not redo the first pass's work.

    Resume is caller-side on purpose. ``evaluate_all`` is a library
    function and answering "which claims already hold a verdict" means
    querying the ``evaluations`` table, which is the store's business and
    not something the evaluator should be doing behind a caller's back.
    The build script holds both halves, so it does the query and passes
    ``only=``. This test pins that division rather than hiding it.
    """
    store, ev = _build()
    first = ev.evaluate_ids(apply=True, progress_every=1)
    n = store.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]

    # A fresh evaluator over the SAME store: this is what a restart looks
    # like. It must discover the work is done and skip it.
    again = Evaluator(store, ev._manifest)
    done = {r[0] for r in store.db.execute("SELECT DISTINCT subject_id FROM evaluations")}
    todo = [cid for cid in sorted(again._norms) if cid not in done]
    second = again.evaluate_ids(apply=True, only=todo, progress_every=1)

    assert first, "fixture compiled nothing"
    assert len(first) > 1, "fixture must produce more than one claim"
    assert done == set(first), "every evaluated claim should hold a verdict"
    assert todo == [], "resume found work that was already done"
    assert second == [], "resumed run re-evaluated claims that were done"
    assert store.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == n


def test_a_resumed_run_produces_the_identical_artifact():
    """The point of the whole thing: resume must be invisible.

    A resumed artifact and an uninterrupted one must be byte-identical in
    identity. If resume left a mark -- a different epoch, a reordered seal,
    a duplicated evaluation -- this is where it would show.
    """
    # Uninterrupted, in a fresh store.
    s1, e1 = _build()
    e1.evaluate_all(apply=True)
    full = s1.recorded_artifact()

    # Interrupted after two claims, then resumed in a new evaluator.
    s2, e2 = _build()
    order = sorted(e2._norms)
    part = e2.evaluate_ids(apply=True, only=order[:2], progress_every=1)
    assert len(part) == 2
    rest = Evaluator(s2, e2._manifest).evaluate_ids(
        apply=True, only=order[2:], progress_every=1
    )
    assert rest

    assert s2.recorded_artifact() == full


def test_evaluating_the_same_claim_twice_is_idempotent():
    """Underpinning assumption. If this fails, resume is unsound."""
    store, ev = _build()
    ev.evaluate_all(apply=True)
    n = store.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    ev.evaluate_all(apply=True)
    assert store.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == n


def test_the_build_script_offers_resume():
    """The long run is driven by the build script, so it needs the flag."""
    out = subprocess.run(
        [sys.executable, "scripts/build_artifact.py", "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert out.returncode == 0, out.stderr
    assert "--resume" in out.stdout


# --------------------------------------------------------------------------
# End-to-end. The unit tests above all passed while a real defect was live:
# ``rebuild_manifest`` was folding miner's own claims into ``claim_ids``, so
# a resumed artifact had a different root from an uninterrupted one -- and
# not one of them noticed, because none of them built an artifact both ways
# and compared. These do. They are slower and that is the point.
# --------------------------------------------------------------------------

def _build_both_ways(tmp_path: Path, limit: int = 40):
    """Build one artifact straight through and one interrupted+resumed.

    Returns (uninterrupted_version, resumed_version).
    """
    import sqlite3

    full = tmp_path / "full.db"
    part = tmp_path / "part.db"

    def run(db: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "scripts/build_artifact.py", "--db", str(db), *extra],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )

    ok = run(full, "--limit", str(limit))
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # Compile only, then evaluate a couple of claims by hand and stop, which
    # is exactly the state a SIGTERM leaves behind.
    ok = run(part, "--limit", str(limit), "--no-evaluate")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    partial = f"""
import sys; sys.path.insert(0, {str(ROOT / 'src')!r})
from ganymede4.compile.reopen import rebuild_manifest
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.store import Store
s = Store({str(part)!r})
e = Evaluator(s, rebuild_manifest(s))
order = sorted(e._norms)
e.evaluate_ids(apply=True, only=order[:2])
s.db.close()
"""
    subprocess.run([sys.executable, "-c", partial], check=True, cwd=ROOT)

    ok = run(part, "--limit", str(limit), "--resume")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "BUILD OK" in ok.stdout, ok.stdout

    def version(p: Path) -> str:
        # read-only: opening a sqlite path for write is how 27,707 rows
        # were once appended to an artifact that was only being measured.
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return c.execute("SELECT version FROM artifact WHERE id = 1").fetchone()[0]
        finally:
            c.close()

    return version(full), version(part)


def test_a_resumed_build_is_byte_identical_to_an_uninterrupted_one(tmp_path):
    """The whole contract, end to end, through the real script.

    This is the test that failed to exist. Its absence is why a wrong
    ``rebuild_manifest`` could ship: the resumed artifact had 2,724
    evaluations where the uninterrupted one had 2,410, and had lost all 314
    ``derived`` states, and every unit test stayed green.
    """
    full, resumed = _build_both_ways(tmp_path)
    assert full == resumed, (
        f"resume is visible in the artifact: {full} vs {resumed}. "
        "A resumed run must be indistinguishable from an uninterrupted one."
    )


def test_resume_preserves_the_partial_artifact_instead_of_deleting_it(tmp_path):
    """--resume must not throw away the hours already spent."""
    import sqlite3

    part = tmp_path / "part.db"
    subprocess.run(
        [sys.executable, "scripts/build_artifact.py", "--db", str(part),
         "--limit", "40", "--no-evaluate"],
        check=True, capture_output=True, cwd=ROOT,
    )
    claims = sqlite3.connect(f"file:{part}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]
    assert claims > 0, "fixture compiled nothing"

    out = subprocess.run(
        [sys.executable, "scripts/build_artifact.py", "--db", str(part),
         "--limit", "40", "--no-evaluate", "--resume"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "resuming: keeping the partial artifact" in out.stdout
    after = sqlite3.connect(f"file:{part}?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM claims"
    ).fetchone()[0]
    assert after == claims, f"resume changed the claim set: {claims} -> {after}"


def test_a_miners_claims_are_not_members_of_claim_ids(tmp_path):
    """Pins the defect directly, at the unit level the e2e test replaced.

    ``rebuild_manifest`` must exclude claims that carry a DERIVED_FROM edge,
    because the compiler appends source claims to ``claim_ids`` before mining
    runs. Folding them in makes the root depend on whether the artifact was
    built or resumed.
    """
    from ganymede4.compile.reopen import rebuild_manifest

    store = Store(str(tmp_path / "h.db"))
    # Verbatim recurrence across three documents is what the miner actually
    # fires on. Two earlier fixtures (numbered sentences, repeated prose)
    # mined nothing and the test skipped -- a skip is a silent pass, and a
    # test of a miner-exclusion bug must not be allowed to evaporate.
    recurring = "The build should be reproducible from source.\n"
    docs = [
        SourceSpec(uri="a.md", content=recurring + "Provenance is content addressed.\n"),
        SourceSpec(uri="b.md", content=recurring),
        SourceSpec(uri="c.md", content=recurring),
    ]
    art = compile_corpus(store, docs, transaction_time=TT)
    mined = {
        r[0] for r in store.db.execute(
            "SELECT DISTINCT from_id FROM claim_edges WHERE relation = 'DERIVED_FROM'"
        )
    }
    assert mined, "fixture mined no heuristics; the test would be vacuous"

    rebuilt = rebuild_manifest(store)
    overlap = mined & set(rebuilt.claim_ids)
    assert not overlap, f"{len(overlap)} miner claims leaked into claim_ids"
    assert rebuilt.heuristic_ids == tuple(sorted(mined))
    assert rebuilt.root == art.manifest.root, (
        "a freshly compiled artifact must rebuild to its own root"
    )


def test_resume_does_not_skip_unchanged_work_silently():
    """A resume must not claim success while leaving claims unevaluated.

    The failure mode of every checkpoint scheme is a run that "finishes"
    with holes in it. `only` must select exactly what the caller asked for
    and never quietly drop the remainder.
    """
    store, ev = _build()
    everything = sorted(ev._norms)
    half = ev.evaluate_ids(apply=True, only=everything[:1], progress_every=1)
    assert half == everything[:1]

    n_after_partial = store.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    done = set(ev.evaluate_ids(apply=True, only=everything, progress_every=1))
    assert done == set(everything)
    assert store.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] >= n_after_partial


def test_resume_is_faster_because_it_does_less_work(tmp_path):
    """Resume must actually skip, not merely arrive at the same artifact.

    Sabotage note: dropping the ``only=todo`` filter produces a
    *correct* artifact -- ``record_evaluation`` is a content-addressed
    ``INSERT OR IGNORE``, so re-evaluating a finished claim is a no-op. Two
    earlier tests could not catch that, and neither could an
    identity comparison, because the sabotage is a performance defect
    wearing correctness clothing: on the real corpus it turns an eight-hour
    resume into a full eight-hour restart.

    So this asserts the number of claims actually evaluated, which is the
    thing ``only`` exists to reduce. Counting evaluations in the database
    cannot see it -- both runs write the same rows.
    """
    import sqlite3

    db = tmp_path / "p.db"
    subprocess.run(
        [sys.executable, "scripts/build_artifact.py", "--db", str(db),
         "--limit", "40", "--no-evaluate"],
        check=True, capture_output=True, cwd=ROOT,
    )
    seed = f"""
import sys; sys.path.insert(0, {str(ROOT / 'src')!r})
from ganymede4.compile.reopen import rebuild_manifest
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.store import Store
s = Store({str(db)!r})
e = Evaluator(s, rebuild_manifest(s))
order = sorted(e._norms)
e.evaluate_ids(apply=True, only=order[:5])
print("TOTAL", len(order))
s.db.close()
"""
    res = subprocess.run([sys.executable, "-c", seed], check=True,
                         capture_output=True, text=True, cwd=ROOT)
    total = int(res.stdout.strip().split()[-1])
    assert total > 10, "fixture too small to measure anything"

    # --resume must report how much is left, and it must be less than all.
    out = subprocess.run(
        [sys.executable, "scripts/build_artifact.py", "--db", str(db),
         "--limit", "40", "--resume"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    # The line is assembled from two implicitly-concatenated f-strings, so
    # match the numbers with a pattern rather than splitting on a phrase that
    # only appears once. Both counts carry thousands separators.
    match = re.search(
        r"resuming:\s*([\d,]+)\s+already evaluated,\s*([\d,]+)\s+remaining",
        out.stdout,
    )
    assert match, f"no resume accounting in output:\n{out.stdout}"
    already = int(match.group(1).replace(",", ""))
    remaining = int(match.group(2).replace(",", ""))
    assert already == 5, f"expected 5 seeded, reported {already}"
    assert remaining == total - 5, (
        f"resume should have {total - 5} claims left, planned {remaining}"
    )

    # Sabotage note: the two numbers above are a *plan*, and a plan cannot be
    # asserted into existence. Replacing ``evaluate_all(only=todo)`` with
    # ``evaluate_all()`` leaves this entire output byte-for-byte identical
    # while quietly ignoring it -- on the real corpus that turns an
    # eight-hour resume back into a full eight-hour restart.
    #
    # What catches it is the count of claims the run says it evaluated,
    # which the build script takes from evaluate_all's return value rather
    # than from its own todo list. Ask the wrong question and the two
    # disagree, which is the whole point.
    done_match = re.search(r"evaluated\s+([\d,]+)\s+claims", out.stdout)
    assert done_match, f"no evaluation count in output:\n{out.stdout}"
    evaluated = int(done_match.group(1).replace(",", ""))
    assert evaluated == total - 5, (
        f"resume planned {remaining} claims but evaluated {evaluated}. "
        "The plan must be the thing that actually ran."
    )

def test_resume_refuses_a_database_that_does_not_exist(tmp_path):
    """--resume promises to continue an interrupted run. There is nothing to
    continue, and the honest answer is to say so.

    Today this falls through the ``if db.exists() and args.resume`` branch
    into an ordinary fresh build that ignores the flag and compiles the whole
    corpus from scratch. That is a performance defect dressed as success: the
    operator asked to resume, got a different thing, and the only evidence is
    a log line they may not read. Worse, it is the failure mode this ADR
    exists to prevent -- an interruption costing eight hours -- reappearing
    under the very flag that claims to fix it.

    The corpus argument is deliberately not supplied, so if the resume check
    ever regressed into "only check when the corpus is fine" the run would fail
    for the wrong reason and could pass for the right one.
    """
    missing = tmp_path / "never-built.db"
    out = subprocess.run(
        [
            sys.executable,
            "scripts/build_artifact.py",
            "--db",
            str(missing),
            "--resume",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert out.returncode != 0, (
        "resume on a missing database must fail closed, not silently do a "
        f"full build:\n{out.stdout}"
    )
    assert "resume" in (out.stdout + out.stderr).lower()
    assert not missing.exists(), "a refused resume must not create the database"
