"""A duplicate is not a witness to itself (ADR-019).

Every evaluator test that predates this file used a **single-source** fixture.
That is why the defect survived eleven phases: with one source, a repeated
sentence collapses to one claim, so the duplicate-attestation path is never
reached. The fixtures below are deliberately multi-source, because
multi-source is the only way to have two claims that say the same thing.
"""

from __future__ import annotations

import json

import pytest

from ganymede4.compile.compiler import SourceSpec, compile_corpus
from ganymede4.knowledge.epistemic import EpistemicState
from ganymede4.knowledge.evaluator import Evaluator, Relation
from ganymede4.knowledge.normalize import normalize
from ganymede4.knowledge.store import Store

TT = "2026-09-29T00:00:00Z"

#: The same sign-off line in three separate conversations. This is the real
#: shape of the defect: ChatGPT closes thousands of exports with an identical
#: sentence, so the corpus contains many copies with distinct claim ids.
REPEATED = "Looking forward to the conversation!"


def build_multi(docs: list[tuple[str, str]]):
    store = Store()
    specs = [SourceSpec(uri=uri, content=content) for uri, content in docs]
    art = compile_corpus(store, specs, transaction_time=TT)
    return store, art, Evaluator(store, art.manifest)


def claims_with_text(store: Store, text: str) -> list[str]:
    return [r["id"] for r in store.db.execute("SELECT id FROM claims WHERE text = ?", (text,))]


# -- the defect itself ---------------------------------------------------


class TestDuplicatesDoNotAttest:
    def test_a_sentence_repeated_across_sources_is_not_supported(self):
        """The load-bearing invariant, and the one that actually fires.

        Three documents each containing the identical sentence produce three
        claims with three distinct ids -- correctly, since each carries its own
        evidence span. Before ADR-019 each attested the other two and all three
        came back SUPPORTED on nothing but their own repetition.
        """
        store, _art, ev = build_multi(
            [
                ("a.md", REPEATED),
                ("b.md", REPEATED),
                ("c.md", REPEATED),
            ]
        )
        try:
            ids = claims_with_text(store, REPEATED)
            assert len(ids) == 3, "fixture must actually produce three duplicate claims"

            verdicts = ev.evaluate_all(apply=False)
            assert verdicts, "no verdicts produced"

            for v in verdicts:
                assert v.relation is not Relation.ATTESTED, (
                    f"a duplicate was accepted as evidence: {v.subject_text!r} "
                    f"attested by {v.attested_by}"
                )
                assert v.state is not EpistemicState.SUPPORTED
        finally:
            store.close()

    def test_the_duplicates_are_still_stored_with_their_own_provenance(self):
        """The fix must not become a deduplication.

        Three occurrences of a sentence is a real fact about the corpus, and
        the store is right to keep three claims with three verbatim spans. Only
        the *verdict* changes. A fix that collapsed the rows would have
        destroyed the provenance structure ADR-003 establishes.
        """
        store, _art, _ev = build_multi(
            [("a.md", REPEATED), ("b.md", REPEATED), ("c.md", REPEATED)]
        )
        try:
            ids = claims_with_text(store, REPEATED)
            assert len(ids) == 3
            for cid in ids:
                n = store.db.execute(
                    "SELECT COUNT(*) FROM claim_evidence WHERE claim_id = ?", (cid,)
                ).fetchone()[0]
                assert n == 1, "each occurrence keeps its own evidence span"
                row = store.get_claim(cid)
                assert row is not None
                meta = json.loads(row["meta"])
                assert meta["source_uri"] in {"a.md", "b.md", "c.md"}
        finally:
            store.close()

    def test_a_strictly_longer_claim_still_attests(self):
        """The genuine case is preserved.

        "X" is genuinely witnessed by a longer claim that contains it. Only
        *equality* is excluded -- an over-broad fix that dropped all
        same-first-token peers would make the evaluator permanently unable to
        attest anything, which is theater in the other direction.
        """
        store, _art, ev = build_multi(
            [
                ("a.md", "The corpus compiles offline."),
                ("b.md", "The corpus compiles offline without any network access."),
            ]
        )
        try:
            short = claims_with_text(store, "The corpus compiles offline.")[0]
            verdict = ev.evaluate(short, apply=False)
            assert verdict.relation is Relation.ATTESTED
            assert verdict.state is EpistemicState.SUPPORTED
            assert verdict.attested_by
        finally:
            store.close()

    def test_duplicates_land_in_inconclusive_with_their_neighbourhood_recorded(self):
        """Falling back to INCONCLUSIVE must still record what was searched.

        A duplicate is not absence. The eleven copies are real neighbours, and
        the evaluation record should say so rather than reporting an empty
        search.
        """
        store, _art, ev = build_multi(
            [("a.md", REPEATED), ("b.md", REPEATED), ("c.md", REPEATED)]
        )
        try:
            cid = claims_with_text(store, REPEATED)[0]
            ev.evaluate(cid, apply=False)
            row = store.db.execute(
                "SELECT relation, meta FROM evaluations WHERE subject_id = ?", (cid,)
            ).fetchone()
            assert row["relation"] == Relation.INCONCLUSIVE.value
            meta = json.loads(row["meta"])
            assert "neighbours=" in meta["investigation"]
            assert "no-neighbours" not in meta["investigation"]
        finally:
            store.close()


# -- the generalisation --------------------------------------------------


class TestSelfAttestationGeneralised:
    def test_no_claim_is_attested_by_an_identical_normalised_proposition(self):
        """The property, checked over the whole corpus rather than a sample.

        Normalized-sequence equality is the identity test, and it is stricter
        than text equality: two claims differing only in punctuation or function
        words are the same assertion and must not witness each other either.
        """
        docs = [
            ("a.md", "The corpus compiles offline. " + REPEATED),
            ("b.md", "The corpus compiles offline. " + REPEATED),
            ("c.md", "Verification is independent. " + REPEATED),
            ("d.md", REPEATED),
        ]
        store, _art, ev = build_multi(docs)
        try:
            for v in ev.evaluate_all(apply=False):
                if v.relation is not Relation.ATTESTED:
                    continue
                subject_seq = normalize(v.subject_text).sequence
                for attester in v.attested_by:
                    peer = store.get_claim(attester)
                    assert peer is not None
                    assert normalize(peer["text"]).sequence != subject_seq, (
                        f"{v.subject_text!r} was attested by an equivalent claim"
                    )
        finally:
            store.close()

    def test_punctuation_only_variants_do_not_attest_each_other(self):
        """"Same sentence" survives normalization even when the text differs."""
        store, _art, ev = build_multi(
            [
                ("a.md", "The corpus compiles offline."),
                ("b.md", "The corpus compiles offline!!"),
            ]
        )
        try:
            ids = claims_with_text(store, "The corpus compiles offline.")
            assert ids
            for cid in ids:
                verdict = ev.evaluate(cid, apply=False)
                assert verdict.relation is not Relation.ATTESTED
        finally:
            store.close()

    def test_duplication_does_not_manufacture_contradictions(self):
        """Scoped check: the fix must not have touched the other relation.

        `_contradictions` requires a polarity flip, so identical-text claims
        could never contradict each other even before ADR-019. This pins that
        the attestation fix left contradiction detection alone, in both
        directions -- copies must not become contradictions, and a real
        polarity flip must still be found.
        """
        store, _art, ev = build_multi(
            [
                ("a.md", REPEATED),
                ("b.md", REPEATED),
                ("c.md", "The runtime enforces provenance. "
                         "The runtime does not enforce provenance."),
            ]
        )
        try:
            relations = [v.relation for v in ev.evaluate_all(apply=False)]
            assert Relation.CONTRADICTED in relations, "a real contradiction was missed"
            for v in ev.evaluate_all(apply=False):
                if v.relation is Relation.CONTRADICTED:
                    assert REPEATED not in v.subject_text
        finally:
            store.close()


# -- the reason this was not caught earlier ------------------------------


class TestTheFixtureThatCouldNotReachIt:
    def test_a_repeated_sentence_within_one_source_also_makes_distinct_claims(self):
        """Why eleven phases of fixture-scale testing missed this.

        The obvious explanation -- "the fixtures were single-source" -- is
        wrong, and worth pinning because it is the more comfortable wrong
        answer. Repeating a sentence *within one document* already produces
        distinct claims, because ``ordinal`` is part of the content hash. Two
        occurrences at offsets 0 and 1 are two rows.

        So source count was never the protection. The real reason is simpler
        and worse: no pre-ADR-019 fixture repeated a sentence at all. Every
        one stated each proposition once, so the duplicate path was simply
        never taken. The defect was not hidden by a fixture *shape*; it was
        absent from every example anyone wrote.
        """
        store, _art, ev = build_multi([("only.md", f"{REPEATED} {REPEATED}")])
        try:
            ids = claims_with_text(store, REPEATED)
            assert len(ids) == 2, "a repeated sentence in one source must make two claims"

            # And that is enough to trigger the defect on its own: no second
            # document required.
            for cid in ids:
                verdict = ev.evaluate(cid, apply=False)
                assert verdict.relation is not Relation.ATTESTED, (
                    "two occurrences in one document attested each other"
                )
        finally:
            store.close()


# -- scale ---------------------------------------------------------------


class TestAtRealCorpusScale:
    def test_a_repeated_signoff_across_many_documents_yields_no_support(self):
        """The real corpus shape: many documents, one boilerplate line.

        The 300-document slice produced 6,075 SUPPORTED claims, 5,987 of them
        attested only to copies of themselves. This asserts the property that
        number violated, on a fixture built the same way.
        """
        boiler = "Looking forward to the conversation! " * 3
        docs = [(f"doc{i}.md", boiler + f"Document {i} discusses topic {i}.")
                for i in range(12)]
        store, _art, ev = build_multi(docs)
        try:
            # The corpus must actually contain the duplicates, or every
            # assertion below is vacuous. Checked first, deliberately.
            dupes = store.db.execute(
                "SELECT COUNT(*) FROM (SELECT text FROM claims "
                "GROUP BY text HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            assert dupes > 0, "fixture produced no duplicate claims; test is vacuous"

            for v in ev.evaluate_all(apply=False):
                if v.subject_text.strip() != REPEATED:
                    continue
                assert v.relation is not Relation.ATTESTED, (
                    "boilerplate was promoted to SUPPORTED by its own copies"
                )
        finally:
            store.close()
