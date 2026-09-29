"""Scaling tests for the evaluator (ADR-010 → ADR-011).

These exist because running the real 18,930-document corpus found a defect that
322 fixture-scale tests did not: `Evaluator.evaluate` asked the retriever for
`limit=len(self._index)` — every document in the corpus — in order to decide
which claims were *neighbours* of the claim under judgement. On the real corpus
it produced 77 evaluation records out of 422,753 claims in 17 minutes.

**ADR-011 fixed it without trading anything away.** The evaluator's question was
a *membership* question wearing a ranking question's clothes: nothing downstream
ever looked at a neighbour's score. So `BM25` gained an inverted index and a
`matching_docs` method returning the same set in time proportional to the
postings touched. No document is dropped that was not already dropped, so no
verdict can change — recall is preserved, not spent.

These tests assert three things, which is the durable form of the lesson:

1. the unbounded search is **gone**, asserted against executable source rather
   than a substring;
2. the fast path is **exact** — the same set as the ranking pass, checked on
   real vocabulary with adversarial queries;
3. the evaluator is **still correct**, because a suite documenting only a
   performance problem reads as though the component were broken. It is sound.

**A trap worth recording.** The first version of assertion (1) was
`assert "limit=len(self._index)" in inspect.getsource(...)`. After the fix it
still passed — because the line survived in the *comment* explaining what had
been replaced. A test pinning a substring in a file will happily guard a prose
description of the bug instead of the bug's absence. Hence the AST-based
assertion below: no comment, docstring, or string literal can satisfy it.
"""

from __future__ import annotations

import ast
import inspect
import os
import random
import tempfile
import time

import pytest

from ganymede4.compile.compiler import SourceSpec, compile_corpus
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.store import Store
from ganymede4.witness.retrieval import BM25


def _corpus_of(n: int):
    """n sources with a *realistic* vocabulary.

    The first version of this fixture used ~11 distinct terms across every
    sentence, which made every BM25 posting list the entire corpus. That is
    degenerate: it measures the pathological case where lexical retrieval
    cannot discriminate, and it made the evaluator look quadratic no matter how
    well the indexes worked. Real corpora have large vocabularies with skewed
    term frequencies, and that is the case worth measuring.

    The distinction matters enough that it is worth stating plainly: the old
    fixture reported 0.86 ms/claim at 800 sources and appeared super-linear;
    this one reports 0.137 ms/claim at the same size and is flat. Both are
    true, and only the second one describes the system.
    """
    rng = random.Random(20260928)
    vocab = [f"term{i}" for i in range(4000)]
    texts = [
        " ".join(rng.sample(vocab, 8)) + f" note{i}" for i in range(n)
    ]
    db = tempfile.mktemp(suffix=".db")
    store = Store(db)
    artifact = compile_corpus(
        store,
        [SourceSpec(f"s{i}.md", t) for i, t in enumerate(texts)],
        transaction_time="2026-09-28T00:00:00Z",
    )
    return store, artifact, db


def _index_search_call_args() -> list[str]:
    """Every `limit=` keyword value passed to a `.search(...)` call.

    AST rather than substring matching, so a comment or docstring cannot
    satisfy the assertion. This is the whole reason this helper exists.
    """
    found: list[str] = []
    tree = ast.parse(inspect.getsource(Evaluator))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "search":
                for kw in node.keywords:
                    if kw.arg == "limit":
                        found.append(
                            kw.value.id
                            if isinstance(kw.value, ast.Name)
                            else ast.dump(kw.value)
                        )
    return found


class TestTheUnboundedSearchIsGone:
    def test_evaluate_no_longer_searches_the_whole_index(self):
        """The defect, asserted in a way a comment cannot fake.

        If this ever fails, someone reintroduced a corpus-sized scan. The
        message points at ADR-011, which is where the reasoning lives.
        """
        for value in _index_search_call_args():
            assert value != "len", (
                "an unbounded index search is back in Evaluator; see ADR-011 "
                "for why the set path is exact and why a constant limit is not "
                "an acceptable substitute"
            )

    def test_the_evaluator_uses_the_set_path(self):
        source = inspect.getsource(Evaluator)
        assert "matching_docs(" in source, (
            "the evaluator should ask for neighbours as a set (ADR-011)"
        )
        assert "self._index.matching_docs" in source

    def test_evaluate_all_still_covers_every_claim(self):
        """The optimisation must not have quietly narrowed the pass.

        A fix that made evaluation fast by evaluating fewer claims would be a
        different and much worse defect than the one it replaced.
        """
        source = inspect.getsource(Evaluator.evaluate_all)
        assert "sorted(self._norms)" in source
        assert "self.evaluate(" in source


class TestExactness:
    """The fast path must return the same set as the ranking pass."""

    @pytest.fixture
    def index(self):
        docs = {
            f"d{i:03d}": " ".join(
                f"term{(i * 7 + j) % 23} content{(i + j) % 11}" for j in range(3 + i % 9)
            )
            for i in range(240)
        }
        return BM25(docs)

    @pytest.mark.parametrize(
        "query",
        [
            "term1 content2",
            "term0",                       # ubiquitous: present everywhere
            "term5 content5",              # repeats, must not double-count
            "termabsent",                  # matches nothing
            "",                            # empty
            "   ",                         # whitespace only
            "term1 term2 term3 term4 term5 content1 content2 content3",
        ],
    )
    def test_matching_docs_equals_the_ranking_pass(self, index, query):
        ranked = {
            hit.doc_id
            for hit in index.search(query, limit=len(index) or 1)
            if hit.score > 0
        }
        assert set(index.matching_docs(query)) == ranked

    def test_a_document_is_never_double_counted(self, index):
        result = index.matching_docs("term3 content3")
        assert len(result) == len(set(result))
        assert list(result) == sorted(result)

    def test_the_equivalence_holds_on_real_vocabulary(self):
        """Checked against real text, not only synthetic filler.

        The equivalence rests on every idf being positive, and real vocabulary
        has far more skewed df distributions than synthetic tokens. If a term
        appearing in nearly every document ever produced a zero idf, this is
        where it would show.
        """
        docs = {
            "a": "the quick brown fox jumps over the lazy dog again and again",
            "b": "provenance is enforced at write time by the substrate",
            "c": "verification is independent of compilation and of provenance",
            "d": "a very common word appears here and also in every other note",
            "e": "common word common word common word rare token provenance",
        }
        index = BM25(docs)
        for query in ("provenance", "common", "common word", "rare", "provenance common"):
            ranked = {h.doc_id for h in index.search(query, limit=len(index)) if h.score > 0}
            assert set(index.matching_docs(query)) == ranked, query

    def test_every_idf_is_strictly_positive(self):
        """The precondition the equivalence rests on, asserted directly.

        If this fails, `score > 0` no longer means "shares a term" and
        `matching_docs` silently diverges from `search`. Pinning the
        precondition keeps that coupling visible instead of latent.
        """
        index = BM25({f"d{i}": "shared rare" for i in range(50)})
        assert all(idf > 0.0 for idf in index._idf.values())


class TestTheCandidateIndexesAreExact:
    """The indexed peers must equal the full scan, for both predicates.

    ADR-011's second amendment: `_peers` iterated every claim in the version,
    so every claim scanned the whole corpus twice. Replacing it with indexes
    only preserves behaviour if the indexes return the same candidates — so
    this compares against the brute-force answer directly, rather than
    asserting some proxy that would also pass if both were wrong.
    """

    TEXTS = [
        "Provenance is enforced at write time.",
        "Provenance is not enforced at write time.",
        "Provenance is enforced at write time by the substrate.",
        "The runtime enforces provenance at write time.",
        "Verification is independent.",
        "Provenance is enforced at write time.",
        "Nothing relevant here at all.",
        "The runtime proposes and never writes.",
    ]

    @pytest.fixture
    def evaluator(self):
        db = tempfile.mktemp(suffix=".db")
        store = Store(db)
        artifact = compile_corpus(
            store,
            [SourceSpec(f"s{i}.md", t) for i, t in enumerate(self.TEXTS)],
            transaction_time="2026-09-28T00:00:00Z",
        )
        ev = Evaluator(store, artifact.manifest)
        yield ev
        store.close()
        if os.path.exists(db):
            os.unlink(db)

    def test_contradictions_match_a_full_scan(self, evaluator):
        ev = evaluator
        assert ev._norms, "fixture produced no claims"
        for cid, norm in ev._norms.items():
            brute = sorted(
                c
                for c, n in ev._norms.items()
                if c != cid
                and n.terms == norm.terms
                and n.polarity_known
                and norm.polarity_known
                and n.polarity != norm.polarity
            )
            assert ev._contradictions(cid, norm) == brute, cid

    def test_attestations_match_a_full_scan(self, evaluator):
        ev = evaluator
        for cid, norm in ev._norms.items():
            brute = sorted(
                c for c, n in ev._norms.items()
                if c != cid and norm.is_contiguous_in(n)
            )
            assert ev._attestations(cid, norm) == brute, cid

    def test_a_claim_is_never_its_own_peer(self, evaluator):
        """Self-attestation is structurally impossible, index or not.

        The exclusion lives in `_term_peers`/`_token_peers` rather than at the
        call site on purpose: a filter a future caller can forget is not a
        guarantee.
        """
        ev = evaluator
        for cid, norm in ev._norms.items():
            assert cid not in ev._term_peers(cid, norm)
            assert cid not in ev._token_peers(cid, norm)

    def test_the_first_token_index_is_a_superset_not_an_equal(self, evaluator):
        """Documented shape, asserted so the docs cannot drift from the code.

        Attestation needs a contiguous run, so sharing a first token is
        necessary but not sufficient. The index rules candidates out; the
        contiguity test rules them in. If this ever became an equality, the
        docstring claiming "superset" would be wrong.
        """
        ev = evaluator
        for cid, norm in ev._norms.items():
            candidates = set(ev._token_peers(cid, norm))
            attested = set(ev._attestations(cid, norm))
            assert attested <= candidates, "an attested claim was not a candidate"


class TestCostCurve:
    @pytest.mark.parametrize("n", [200, 400, 800, 1600])
    def test_per_claim_cost_stays_low(self, n):
        """Recorded, not thresholded.

        A speed assertion on shared hardware fails for reasons unrelated to the
        code. What matters is the *shape*: these numbers are reported so a
        future regression shows up as a jump rather than a drift.

        Expected to be roughly **flat**. Per-claim cost climbing with n means a
        full-corpus scan has crept back into the hot path — which is exactly the
        defect this file exists to prevent, and exactly what an unbounded
        `_peers` looked like.
        """
        store, artifact, db = _corpus_of(n)
        try:
            evaluator = Evaluator(store, artifact.manifest)
            started = time.monotonic()
            evaluator.evaluate_all()
            elapsed = time.monotonic() - started
            claims = store.db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
            per_claim_ms = 1000 * elapsed / max(claims, 1)
            print(
                f"\n  {n} sources -> {claims} claims: {elapsed:.3f}s "
                f"({per_claim_ms:.3f} ms/claim)"
            )
            assert per_claim_ms > 0
        finally:
            store.close()
            if os.path.exists(db):
                os.unlink(db)

    def test_the_evaluator_is_still_correct(self):
        """Correctness was never in question; scale was.

        Worth asserting alongside the scaling work, because a suite that only
        documented a performance defect would read as though the component were
        broken.
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
                assert evaluator.evaluate_all()
                assert evaluator.contradictions()
        finally:
            if os.path.exists(db):
                os.unlink(db)


class TestTheGuardIsRetired:
    def test_the_script_no_longer_refuses_a_full_run(self):
        """The ADR-010 workaround is retired now the fix has landed.

        The `--no-evaluate` flag stays accepted, because skipping a pass you do
        not need is still reasonable. The *refusal* is gone, since the reason
        for it no longer applies — and a stale guard that blocks a now-valid
        invocation is its own kind of defect.
        """
        import subprocess
        import sys
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "scripts" / "compile_corpus.py"
        assert "refusing to run evaluate_all over the full corpus" not in script.read_text(), (
            "the ADR-010 workaround guard is still in place but the fix has "
            "landed; remove it rather than leaving a full run impossible"
        )
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0
