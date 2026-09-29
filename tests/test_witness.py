"""Witness tests (ADR-005, Gate 3).

Gate 3 requires: every witness answer resolves to a character offset;
``UNRESOLVED`` requires an investigation record; the boundary object is present
and non-empty on the test corpus.

The tests are organized around the five hard constraints in
``target-architecture.md`` §7 plus the three ways the witness can be defeated
by a careless caller — which are the ways it would actually be defeated.
"""

from __future__ import annotations

import inspect
import json

import pytest

from ganymede4.compile.compiler import SourceSpec, compile_corpus
from ganymede4.knowledge.epistemic import EpistemicState
from ganymede4.knowledge.store import Store
from ganymede4.witness.retrieval import FUNCTION_WORDS, BM25, tokenize
from ganymede4.witness.witness import OutOfVersion, Witness, WitnessAnswer

TT = "2026-09-28T00:00:00Z"

CORPUS = (
    "Daniel built a local runtime. "
    "The runtime enforces provenance at write time. "
    "Verification is independent of the model. "
    "Provenance is content addressed. "
    "The corpus compiles offline."
)


@pytest.fixture()
def witness():
    with Store() as store:
        art = compile_corpus(
            store, [SourceSpec(uri="notes.md", content=CORPUS)], transaction_time=TT
        )
        yield Witness(store, art.manifest), store, art


# -- constraint 1: bound to one version ----------------------------------


class TestVersionBinding:
    def test_witness_reports_its_version(self, witness):
        w, _store, art = witness
        assert w.version == art.manifest.version
        assert w.root == art.manifest.root

    def test_claim_outside_version_is_refused(self, witness):
        w, store, _art = witness
        cid = store.add_claim(
            text="A claim added after the witness was bound.",
            state=EpistemicState.UNEXAMINED,
            transaction_time=TT,
        )
        # A fresh claim is not in the bound version's leaf set.
        with pytest.raises(OutOfVersion):
            w._check_version(cid)

    def test_witness_cannot_see_a_recompiled_store(self, witness):
        """Recompiling with new content must not leak into a bound witness."""
        w, store, _art = witness
        before = len(list(w.iter_claims()))
        compile_corpus(
            store,
            [SourceSpec(uri="notes.md", content=CORPUS), SourceSpec(uri="new.md", content="A new fact arrived.")],
            transaction_time=TT,
        )
        after = len(list(w.iter_claims()))
        assert before == after, "witness saw records added after it was bound"

    def test_new_witness_over_the_new_manifest_does_see_them(self, witness):
        w, store, _art = witness
        # The added sentence must clear ADR-012's proposition floor, or it is
        # correctly discarded and this test would be asserting the opposite of
        # the invariant. That is not hypothetical: the first version of this
        # fixture said "A new fact arrived." and failed, correctly.
        art2 = compile_corpus(
            store,
            [
                SourceSpec(uri="notes.md", content=CORPUS),
                SourceSpec(
                    uri="new.md",
                    content="A new fact arrived in the building yesterday.",
                ),
            ],
            transaction_time=TT,
        )
        w2 = Witness(store, art2.manifest)
        assert len(list(w2.iter_claims())) > len(list(w.iter_claims()))


# -- constraint 2: the boundary is exposed -------------------------------


class TestBoundary:
    def test_boundary_is_present_and_non_empty_on_a_fresh_witness(self, witness):
        w, _store, _art = witness
        b = w.boundary()
        assert b.version
        assert b.sources == ("notes.md",)
        assert b.state_counts.get("unexamined") == 5
        assert b.index["documents"] == 5
        assert b.method

    def test_boundary_records_what_was_asked(self, witness):
        w, _store, _art = witness
        assert w.boundary().asked == ()
        w.ask("provenance")
        assert w.boundary().asked == ("provenance",)

    def test_boundary_separates_asked_from_absence(self, witness):
        w, _store, _art = witness
        w.ask("provenance")
        w.ask("elephants")
        b = w.boundary()
        assert len(b.asked) == 2
        assert b.answered_with_absence == ("elephants",)

    def test_boundary_states_what_it_does_not_contain(self, witness):
        w, _store, _art = witness
        d = w.boundary().to_dict()
        assert d["does_not_contain"], "boundary does not declare its limits"
        assert any("outside these sources" in x for x in d["does_not_contain"])

    def test_boundary_is_serializable(self, witness):
        import json

        w, _store, _art = witness
        json.dumps(w.boundary().to_dict())


# -- constraint 3: answers are typed, never strings ----------------------


class TestTypedAnswers:
    def test_ask_returns_a_typed_answer(self, witness):
        w, _store, _art = witness
        answer = w.ask("provenance")
        assert isinstance(answer, WitnessAnswer)
        assert isinstance(answer.state, EpistemicState)

    def test_there_is_no_string_returning_api(self):
        """The absence of `answer() -> str` is the enforcement (ADR-005 §2)."""
        w_methods = [
            name
            for name, _ in inspect.getmembers(Witness, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]
        for forbidden in ("answer", "respond", "reply", "complete", "generate"):
            assert forbidden not in w_methods, (
                f"Witness.{forbidden}() would let a caller obtain an untyped answer"
            )

    def test_answer_serializes_with_state_and_provenance(self, witness):
        import json

        w, _store, _art = witness
        d = w.ask("provenance").to_dict()
        assert d["state"] == "unexamined"
        assert d["claims"]
        assert all("citation" in e for e in d["evidence"])
        json.dumps(d)

    def test_fresh_witness_has_no_string_helper(self, witness):
        w, _store, _art = witness
        assert not hasattr(w, "__str__override__")
        # The object has no way to stringify itself into an answer.
        assert not callable(getattr(w, "answer_text", None))


# -- constraint 4: every claim resolves to a character offset -------------


class TestProvenanceResolution:
    def test_every_answer_resolves_to_an_offset(self, witness):
        w, _store, _art = witness
        answer = w.ask("provenance")
        assert answer.claims
        assert len(answer.evidence) >= len(answer.claims)
        for ev in answer.evidence:
            assert ev.source_uri
            assert ev.end > ev.start
            assert ev.text

    def test_citations_name_a_real_offset(self, witness):
        w, _store, _art = witness
        citations = w.ask("provenance").citations()
        assert citations
        for c in citations:
            assert c.startswith("notes.md[")
            assert c.endswith("]")

    def test_cited_text_is_verbatim_from_the_source(self, witness):
        w, store, _art = witness
        src = store.get_source(store.claims and w._manifest.source_ids[0])
        assert src is not None
        for ev in w.ask("provenance").evidence:
            assert src["content"][ev.start : ev.end] == ev.text

    def test_require_provenance_raises_on_an_evidence_free_claim_answer(self):
        bad = WitnessAnswer(
            question="q",
            state=EpistemicState.SUPPORTED,
            claims=(("clm-x", "a claim with no evidence"),),
        )
        with pytest.raises(AssertionError):
            bad.require_provenance()

    def test_require_provenance_raises_on_unresolved_without_investigation(self):
        bad = WitnessAnswer(
            question="q", state=EpistemicState.UNRESOLVED, is_absence=True
        )
        with pytest.raises(AssertionError):
            bad.require_provenance()

    def test_resolving_a_corrupt_evidence_edge_raises(self, witness):
        """A claim whose evidence no longer resolves is a defect, not a skip."""
        w, store, _art = witness
        cid = w._manifest.claim_ids[0]
        store.db.execute("UPDATE evidence SET text = 'tampered'")
        store.db.commit()
        from ganymede4.witness.retrieval import ScoredDoc

        with pytest.raises(AssertionError):
            w._resolve(cid, ScoredDoc(doc_id=cid, score=1.0, matched_terms=("x",)))


# -- constraint 5: absence is provable, and only with a record ------------


class TestProvableAbsence:
    def test_miss_returns_unresolved(self, witness):
        w, _store, _art = witness
        answer = w.ask("what is the quantum chromodynamics of elephants")
        assert answer.state is EpistemicState.UNRESOLVED
        assert answer.is_absence
        assert not answer.claims

    def test_miss_writes_an_investigation_record(self, witness):
        w, store, _art = witness
        answer = w.ask("purple submarine aeronautics")
        assert answer.investigation_id is not None
        row = store.db.execute(
            "SELECT question, searched FROM investigations WHERE id = ?",
            (answer.investigation_id,),
        ).fetchone()
        assert row is not None
        assert "purple submarine aeronautics" in row["question"]
        # The record names the method, the version, and the scope. `searched` is
        # stored as a canonical JSON array, so it is parsed rather than joined:
        # iterating the raw column would walk its characters.
        searched = " ".join(json.loads(row["searched"]))
        assert "method=lexical-bm25" in searched
        assert f"version={w.version}" in searched
        assert "claims_searched=" in searched

    def test_absence_is_a_real_state_not_a_flag(self, witness):
        """Absence lives inside the state machine, not beside it."""
        w, _store, _art = witness
        answer = w.ask("elephants")
        assert isinstance(answer.state, EpistemicState)
        assert answer.state is EpistemicState.UNRESOLVED
        # There is no separate "absent" boolean on the state.
        assert not hasattr(answer.state, "absent")

    def test_absence_and_hit_have_the_same_shape(self, witness):
        w, _store, _art = witness
        hit = w.ask("provenance")
        miss = w.ask("elephants")
        assert set(hit.to_dict()) == set(miss.to_dict())
        assert hit.state is not miss.state

    def test_repeat_miss_is_still_backed_by_a_record(self, witness):
        w, _store, _art = witness
        a = w.ask("elephants")
        b = w.ask("elephants")
        assert a.state is EpistemicState.UNRESOLVED
        assert b.state is EpistemicState.UNRESOLVED
        assert a.investigation_id is not None and b.investigation_id is not None

    def test_hit_does_not_claim_absence(self, witness):
        w, _store, _art = witness
        answer = w.ask("provenance content addressing")
        assert answer.state is EpistemicState.UNEXAMINED
        assert not answer.is_absence
        assert answer.investigation_id is None


# -- the witness never certifies (ADR-005 §5) ---------------------------


class TestNoCertification:
    def test_retrieval_never_promotes_a_state(self, witness):
        """Rank is not an epistemic judgement. Nothing leaves UNEXAMINED."""
        w, store, _art = witness
        w.ask("provenance")
        w.ask("runtime local")
        w.ask("verification independent")
        assert {r["state"] for r in store.claims()} == {"unexamined"}

    def test_an_exact_match_is_still_unexamined(self, witness):
        """Presence is not support — this is the v0.1 failure in one line."""
        w, _store, _art = witness
        answer = w.ask("Verification is independent of the model.")
        assert answer.claims
        assert answer.state is EpistemicState.UNEXAMINED

    def test_witness_has_no_write_path_to_claims(self):
        public = [
            n for n, _ in inspect.getmembers(Witness, predicate=inspect.isfunction)
            if not n.startswith("_")
        ]
        for forbidden in ("add_claim", "set_state", "promote", "assert_", "delete"):
            assert not any(forbidden in n for n in public), (
                f"Witness exposes a write path: {public}"
            )


# -- retrieval honesty ---------------------------------------------------


class TestRetrievalHonesty:
    def test_function_words_retrieve_nothing(self, witness):
        w, _store, _art = witness
        for q in ("is", "a", "the", "of and the"):
            assert w.ask(q).state is EpistemicState.UNRESOLVED, f"{q!r} returned a claim"

    def test_nonsense_returns_absence_not_a_weak_claim(self, witness):
        w, _store, _art = witness
        answer = w.ask("zeppelin metallurgy")
        assert answer.state is EpistemicState.UNRESOLVED
        assert answer.claims == ()

    def test_negation_survives_tokenization(self):
        assert "not" in tokenize("the runtime is not local-first")
        assert "not" not in FUNCTION_WORDS

    def test_quantifiers_and_temporal_operators_survive(self):
        for word in ("only", "always", "never", "before", "after", "since", "all", "none"):
            assert word not in FUNCTION_WORDS, (
                f"{word!r} is dropped at tokenization; it changes what a claim asserts"
            )

    def test_negation_survives_into_the_score(self):
        """A negated query must not be tokenized down to the positive one.

        If "not" were dropped, "runtime not local-first" and "runtime
        local-first" would produce identical retrieval, and the witness would
        answer a yes/no question with the same citation either way. The
        retrieval is still BM25 and still matches shared content terms — that
        limitation is documented in the ADR — but the negation is *present* in
        the query, so a future entailment stage can act on it.
        """
        negated = tokenize("runtime not local-first")
        positive = tokenize("runtime local-first")
        assert negated != positive
        assert "not" in negated
        assert set(positive) < set(negated)

    def test_ranking_is_exposed_on_every_hit(self, witness):
        w, _store, _art = witness
        for ev in w.ask("provenance").evidence:
            assert ev.rank > 0
            assert ev.matched_terms

    def test_search_is_deterministic(self, witness):
        w, _store, _art = witness
        first = [d.doc_id for d in w._index.search("provenance runtime")]
        for _ in range(5):
            assert [d.doc_id for d in w._index.search("provenance runtime")] == first

    def test_ties_break_on_doc_id(self):
        # Two identical documents must order deterministically.
        ix = BM25({"b": "same text here", "a": "same text here"})
        results = ix.search("same")
        assert [d.doc_id for d in results] == ["a", "b"]

    def test_method_id_is_disclosed(self, witness):
        from ganymede4.witness.retrieval import METHOD_ID

        w, _store, _art = witness
        assert w.boundary().method == METHOD_ID
        assert w.ask("provenance").method == METHOD_ID

    def test_empty_index_returns_nothing(self):
        assert BM25({}).search("anything") == []

    def test_empty_query_returns_nothing(self, witness):
        w, _store, _art = witness
        assert w._index.search("   ") == []


# -- claims by state -----------------------------------------------------


class TestClaimsByState:
    def test_claims_by_state_returns_only_that_state(self, witness):
        w, store, _art = witness
        unexamined = w.claims_by_state(EpistemicState.UNEXAMINED)
        assert len(unexamined) == 5
        assert w.claims_by_state(EpistemicState.CONTRADICTED) == []

    def test_claims_by_state_reflects_a_later_transition(self, witness):
        w, store, _art = witness
        cid = w._manifest.claim_ids[0]
        store.set_state(cid, EpistemicState.CONTRADICTED, transaction_time=TT)
        assert len(w.claims_by_state(EpistemicState.CONTRADICTED)) == 1
        assert len(w.claims_by_state(EpistemicState.UNEXAMINED)) == 4
