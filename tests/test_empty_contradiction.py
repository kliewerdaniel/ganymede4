"""Two claims that share nothing are not a contradiction (ADR-020).

ADR-019's duplicate-attestation tests could not have caught this: the fixture
corpora are English sentences that all normalize to non-empty term sets, so
the empty-set path was never taken. The corpus that reaches it is one with
sentences the ASCII tokenizer cannot read at all.
"""

from __future__ import annotations

import pytest

from ganymede4.compile.compiler import SourceSpec, compile_corpus
from ganymede4.knowledge.epistemic import EpistemicState
from ganymede4.knowledge.evaluator import Evaluator, Relation
from ganymede4.knowledge.normalize import normalize
from ganymede4.knowledge.store import Store

TT = "2026-09-29T00:00:00Z"

#: Real strings from the corpus. The tokenizer is `[a-z0-9]+`, so none of
#: these produces a single term.
CYRILLIC = "Я - гангстер, я - настоящий босс, я не играю в игры."
GREEK = "Αυτό είναι ένα ελληνικό κείμενο για δοκιμή."
#: 33 characters of English function words. Clears MIN_CLAIM_CHARS=25 and
#: MIN_CLAIM_LETTERS=1, so `is_proposition` accepts it.
FUNCTION_WORDS = "But that's not what this was."


def build(docs: list[tuple[str, str]]):
    store = Store()
    specs = [SourceSpec(uri=uri, content=content) for uri, content in docs]
    art = compile_corpus(store, specs, transaction_time=TT)
    return store, art, Evaluator(store, art.manifest)


# -- the defect ----------------------------------------------------------


class TestEmptyTermSetsDoNotContradict:
    def test_cyrillic_is_not_contradicted_by_an_english_sentence(self):
        """The exact collision observed on the real corpus.

        A line of Russian lyrics and an English function-word sentence share
        nothing -- and both normalize to the empty term set, so a set-equality
        predicate reports them as the same proposition.
        """
        store, _art, ev = build(
            [
                ("ru.md", CYRILLIC),
                ("en.md", FUNCTION_WORDS),
            ]
        )
        try:
            # Precondition: the fixture must actually reach the defect. A
            # corpus of English sentences cannot.
            assert normalize(CYRILLIC).terms == frozenset()
            assert normalize(FUNCTION_WORDS).terms == frozenset()

            for v in ev.evaluate_all(apply=False):
                assert v.relation is not Relation.CONTRADICTED, (
                    f"unrelated claim marked CONTRADICTED: {v.subject_text!r} "
                    f"by {v.contradicted_by}"
                )
        finally:
            store.close()

    def test_the_empty_set_collision_is_real(self):
        """Confirm the precondition on its own, so the test above is not vacuous."""
        assert normalize(CYRILLIC).terms == normalize(GREEK).terms == frozenset()
        assert normalize(CYRILLIC).polarity is normalize(GREEK).polarity
        # The collision needs *opposite* polarity, which "not" supplies.
        assert normalize(FUNCTION_WORDS).polarity is True
        assert normalize(CYRILLIC).polarity is False

    def test_many_unrelated_scripts_never_contradict_each_other(self):
        """The corpus-scale shape: 193 unrelated claims, one polarity flip.

        Reproduces the observed bucket — a large number of claims sharing the
        empty set at one polarity, and a single one at the other.
        """
        docs = [(f"doc{i}.md", f"{CYRILLIC} {GREEK} Вариант номер {i}.")
                for i in range(6)]
        docs.append(("flip.md", FUNCTION_WORDS))
        store, _art, ev = build(docs)
        try:
            for v in ev.evaluate_all(apply=False):
                assert v.relation is not Relation.CONTRADICTED
        finally:
            store.close()

    def test_a_genuine_contradiction_still_finds_its_peer(self):
        """The guard must not disable contradiction detection.

        A fix that returned early for all claims, or that treated any claim
        without a shared peer as contradictory, would pass the tests above and
        be catastrophically wrong. Real term sets, real polarity flip.
        """
        store, _art, ev = build(
            [
                ("a.md", "The runtime enforces provenance. "
                         "The runtime does not enforce provenance."),
            ]
        )
        try:
            relations = [v.relation for v in ev.evaluate_all(apply=False)]
            assert Relation.CONTRADICTED in relations, "a real contradiction was missed"
        finally:
            store.close()

    def test_a_real_contradiction_survives_alongside_empty_term_claims(self):
        """The guard is per-claim, not global.

        Empty-term claims in the corpus must not disable contradiction
        detection for the claims that can be compared.
        """
        store, _art, ev = build(
            [
                ("ru.md", CYRILLIC),
                ("en.md", FUNCTION_WORDS),
                ("real.md", "The corpus compiles offline. "
                            "The corpus does not compile offline."),
            ]
        )
        try:
            verdicts = ev.evaluate_all(apply=False)
            real = [v for v in verdicts if "compiles offline" in v.subject_text]
            assert real, "fixture lost the real contradiction claims"
            assert any(v.relation is Relation.CONTRADICTED for v in real)
            empty = [v for v in verdicts if v.subject_text in (CYRILLIC, FUNCTION_WORDS)]
            assert all(v.relation is not Relation.CONTRADICTED for v in empty)
        finally:
            store.close()

    def test_empty_term_claims_land_in_inconclusive(self):
        """The documented safe direction, not silence or absence.

        A non-Latin claim is correctly stored, correctly provenanced, and
        correctly reported as not evaluable by this method. It must not look
        like an unevaluated claim -- it must carry a verdict saying the
        comparison established nothing.
        """
        store, _art, ev = build([("ru.md", CYRILLIC), ("en.md", FUNCTION_WORDS)])
        try:
            for v in ev.evaluate_all(apply=False):
                if v.subject_text not in (CYRILLIC, FUNCTION_WORDS):
                    continue
                assert v.relation is Relation.INCONCLUSIVE, v.relation
                assert v.state is EpistemicState.INCONCLUSIVE
        finally:
            store.close()

    def test_empty_term_claims_keep_their_provenance(self):
        """The guard is a verdict filter, never a deletion.

        A claim the evaluator cannot compare is still a claim, and dropping it
        would quietly shrink the corpus — the exact failure ADR-012 was written
        to make visible rather than silent.
        """
        store, _art, _ev = build([("ru.md", CYRILLIC)])
        try:
            rows = store.db.execute("SELECT id FROM claims").fetchall()
            assert len(rows) == 1, "the Cyrillic claim was dropped from the store"
            n = store.db.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0]
            assert n == 1, "its evidence span was dropped"
        finally:
            store.close()


# -- attestation is already covered, and why ----------------------------


class TestAttestationWasAlreadyFixed:
    def test_empty_term_claims_do_not_attest_each_other(self):
        """ADR-019 covers this as a consequence, not by design.

        Attestation requires `peer.sequence != subject.sequence` (ADR-019), and
        two empty sequences are equal, so empty-term claims already cannot
        witness each other. Pinned here so the reasoning is not rediscovered as
        a missing check later.
        """
        store, _art, ev = build([("ru.md", CYRILLIC), ("en.md", FUNCTION_WORDS)])
        try:
            for v in ev.evaluate_all(apply=False):
                assert v.relation is not Relation.ATTESTED
        finally:
            store.close()


# -- the general property ------------------------------------------------


class TestTheGeneralProperty:
    def test_no_claim_with_an_empty_term_set_ever_contradicts(self):
        """Stated over the whole corpus rather than a sample."""
        docs = [
            ("ru.md", CYRILLIC),
            ("el.md", GREEK),
            ("en.md", FUNCTION_WORDS),
            ("real.md", "The runtime enforces provenance. "
                        "The runtime does not enforce provenance. "
                        "Provenance is content addressed."),
        ]
        store, _art, ev = build(docs)
        try:
            verdicts = ev.evaluate_all(apply=False)
            empty = [v for v in verdicts if not normalize(v.subject_text).terms]
            assert empty, "fixture produced no empty-term claims; test is vacuous"
            for v in verdicts:
                if not normalize(v.subject_text).terms:
                    assert v.relation is not Relation.CONTRADICTED
        finally:
            store.close()

    def test_the_tokenizer_limitation_is_documented_not_hidden(self):
        """`normalize` is ASCII-only, and that is a design choice worth pinning.

        The stemmer is English-only. Applying it to Cyrillic would manufacture
        worse errors than admitting ignorance, so the honest behavior is an
        empty term set — which ADR-020 then declines to treat as a match. This
        test fails if someone "fixes" the regex without also fixing the stemmer
        and the guard, which is the sequence of changes that would be needed.
        """
        assert normalize(CYRILLIC).terms == frozenset()
        assert normalize("provenance").terms == frozenset({"provenance"})
        # And the stemmer does reduce inflections, which is the whole reason
        # an English-only tokenizer is a deliberate limit rather than a bug.
        assert normalize("verified").terms == normalize("verify").terms
        # Latin claims are unaffected: the guard costs nothing for the 99.9%
        # of the corpus that the tokenizer can read.
        assert normalize("Provenance is content addressed.").terms
