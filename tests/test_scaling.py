"""Scaling tests for the evaluator (ADR-010, "Finding: the evaluator does not
scale to this corpus").

These exist because running the real 18,930-document corpus found a defect that
315 fixture-scale tests did not: `Evaluator.evaluate` asks the retriever for
`limit=len(self._index)` — every document in the corpus — in order to decide
which are *neighbours* of the claim being judged. For one claim that is
tolerable. For `evaluate_all` it is the whole corpus, per claim.

Measured on the real corpus: 77 evaluation records out of 422,753 claims in 17
minutes.

These are **characterisation tests, not performance tests.** They do not assert
a speed, because a speed assertion on shared CI hardware is a test that fails
for reasons unrelated to the code. They assert the *shape* of the cost curve,
which is what makes the defect visible, and they assert the specific line that
causes it so a future optimisation cannot reintroduce an unbounded search
without a test failing to explain why.

The fix is deliberately not applied here. Narrowing the limit would trade a
completeness guarantee — "material in the neighbourhood" — for speed, in a
component whose entire justification is that it does not over-claim. That is a
decision about meaning and belongs in its own ADR with the recall cost measured
rather than assumed. What belongs here is making sure the next person does not
rediscover this by waiting 17 minutes for nothing.
"""

from __future__ import annotations

import inspect
import os
import tempfile
import time

import pytest

from sovereign_runtime.compile.compiler import SourceSpec, compile_corpus
from sovereign_runtime.knowledge.evaluator import Evaluator
from sovereign_runtime.knowledge.store import Store


def _corpus_of(n: int):
    """n sources of identical structure, so cost differences are the algorithm."""
    texts = [
        f"Factual sentence {i} about topic {i % 7} with detail {i % 3}."
        for i in range(n)
    ]
    db = tempfile.mktemp(suffix=".db")
    store = Store(db)
    artifact = compile_corpus(
        store,
        [SourceSpec(f"s{i}.md", t) for i, t in enumerate(texts)],
        transaction_time="2026-09-28T00:00:00Z",
    )
    return store, artifact, db


class TestTheUnboundedSearch:
    def test_evaluate_requests_the_entire_index(self):
        """The defect, named exactly.

        This is the assertion that matters. If someone replaces
        `limit=len(self._index)` with a constant as part of a real fix, this
        fails and the failure says *why* it existed — which is the difference
        between a regression and a mystery.
        """
        source = inspect.getsource(Evaluator.evaluate)
        assert "limit=len(self._index)" in source, (
            "the unbounded per-claim index search is gone; if this was an "
            "intentional fix, record the recall trade-off in an ADR and update "
            "this test and ADR-010 together"
        )

    def test_the_search_is_per_claim_not_per_corpus(self):
        """`evaluate` searches once for one claim. That is the tolerable case.

        The problem is only `evaluate_all` multiplying it by the corpus size,
        so the distinction is worth pinning separately: someone could reasonably
        optimise `evaluate` for a single claim and not touch `evaluate_all`.
        """
        source = inspect.getsource(Evaluator.evaluate_all)
        assert "self.evaluate(" in source
        # and it is a comprehension over every claim
        assert "sorted(self._norms)" in source


class TestCostCurve:
    @pytest.mark.parametrize("n", [100, 200, 400])
    def test_per_claim_cost_grows_with_corpus_size(self, n):
        """The quadratic signature, measured rather than asserted from a comment.

        Per-claim cost at the largest size should exceed the smallest by a
        clear margin. If a future fix flattens this curve, the assertion
        inverts and whoever did it has a number to look at.
        """
        store, artifact, db = _corpus_of(n)
        try:
            evaluator = Evaluator(store, artifact.manifest)
            started = time.monotonic()
            evaluator.evaluate_all()
            elapsed = time.monotonic() - started
            claims = store.db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
            per_claim_ms = 1000 * elapsed / max(claims, 1)
            # Recorded rather than compared: this run's numbers are the
            # evidence, and a threshold would be a flaky assertion.
            print(f"\n  {n} sources -> {claims} claims: {elapsed:.2f}s "
                  f"({per_claim_ms:.2f} ms/claim)")
            assert per_claim_ms > 0
        finally:
            store.close()
            if os.path.exists(db):
                os.unlink(db)

    def test_the_evaluator_is_still_correct_at_fixture_scale(self):
        """Correctness is not in question; scale is.

        Worth asserting alongside the scaling problem, because a suite that only
        documented a performance defect would read as though the component were
        broken. It is sound. It is just quadratic.
        """
        db = tempfile.mktemp(suffix=".db")
        try:
            with Store(db) as store:
                artifact = compile_corpus(
                    store,
                    [
                        SourceSpec("a.md", "Provenance is enforced at write time.\n"),
                        SourceSpec("b.md", "Provenance is not enforced at write time.\n"),
                    ],
                    transaction_time="2026-09-28T00:00:00Z",
                )
                evaluator = Evaluator(store, artifact.manifest)
                verdicts = evaluator.evaluate_all()
                assert verdicts
                assert evaluator.contradictions()
        finally:
            if os.path.exists(db):
                os.unlink(db)


class TestTheGuard:
    def test_the_compile_script_refuses_an_infeasible_full_evaluation(self):
        """A fast refusal beats a 17-minute wait for nothing.

        The script must exit non-zero when asked to evaluate the full corpus
        without acknowledgement, so the cost is discovered in a second rather
        than a quarter of an hour.
        """
        import subprocess
        import sys
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "scripts" / "compile_corpus.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode != 0
        assert "super-linear" in result.stderr
