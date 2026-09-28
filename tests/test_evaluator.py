"""Evaluator tests (ADR-006).

The evaluator is the first component allowed to move a claim out of
``UNEXAMINED``, so these tests are mostly adversarial: they try to make it
certify something it shouldn't. The v0.1 failure was 23 "established facts"
that were all equally established and equally meaningless, and the only defence
is a test that fails when the evaluator becomes generous.
"""

from __future__ import annotations

import json

import pytest

from sovereign_runtime.compile.compiler import SourceSpec, compile_corpus
from sovereign_runtime.knowledge.epistemic import EpistemicState
from sovereign_runtime.knowledge.evaluator import (
    EVALUATOR_METHOD,
    Evaluator,
    Relation,
    SelfAttestation,
    Verdict,
)
from sovereign_runtime.knowledge.normalize import NEGATION_UNKNOWN, normalize, stem
from sovereign_runtime.knowledge.store import Store

TT = "2026-09-28T00:00:00Z"

#: A corpus with one genuine contradiction pair, and three claims that exist on
#: their own with nothing to attest or refute them.
CONFLICT = (
    "The runtime enforces provenance at write time. "
    "The runtime does not enforce provenance at write time. "
    "The corpus compiles offline. "
    "Verification is independent. "
    "Provenance is content addressed."
)

#: A corpus where one proposition is stated twice, so it can be attested.
CORROBORATED = (
    "The corpus compiles offline. "
    "The corpus compiles offline without network access. "
    "Verification is independent."
)


def build(content: str, uri: str = "notes.md"):
    store = Store()
    art = compile_corpus(store, [SourceSpec(uri=uri, content=content)], transaction_time=TT)
    return store, art, Evaluator(store, art.manifest)


@pytest.fixture()
def conflict():
    store, art, ev = build(CONFLICT)
    yield ev, store, art
    store.close()


@pytest.fixture()
def corroborated():
    store, art, ev = build(CORROBORATED)
    yield ev, store, art
    store.close()


# -- the rule that makes the rest honest --------------------------------


class TestNoSelfAttestation:
    def test_a_claim_cannot_attest_itself(self, conflict):
        """The load-bearing invariant (ADR-006 §2.2).

        The compiler emits claims that are verbatim source segments, so a
        candidate always contains the same tokens as its own evidence. If it
        counted, every claim would be SUPPORTED with perfect recall and no
        information.
        """
        ev, _store, _art = conflict
        for verdict in ev.evaluate_all(apply=False):
            assert verdict.subject_id not in verdict.attested_by
            assert verdict.subject_id not in verdict.contradicted_by

    def test_a_lone_claim_is_never_supported(self):
        """One claim, repeated nowhere. It cannot attest itself."""
        store, art, ev = build("The corpus compiles offline.")
        verdicts = ev.evaluate_all(apply=False)
        assert len(verdicts) == 1
        assert verdicts[0].relation is Relation.INCONCLUSIVE
        assert verdicts[0].attested_by == ()
        store.close()

    def test_supported_cannot_be_constructed_without_attestation(self):
        with pytest.raises(SelfAttestation):
            Verdict(
                subject_id="clm-x",
                subject_text="x",
                relation=Relation.ATTESTED,
                state=EpistemicState.SUPPORTED,
                evaluation_id="evl-x",
            )

    def test_contradicted_cannot_be_constructed_without_a_counterpart(self):
        with pytest.raises(SelfAttestation):
            Verdict(
                subject_id="clm-x",
                subject_text="x",
                relation=Relation.CONTRADICTED,
                state=EpistemicState.CONTRADICTED,
                evaluation_id="evl-x",
            )

    def test_unresolved_cannot_be_constructed_without_an_investigation(self):
        with pytest.raises(SelfAttestation):
            Verdict(
                subject_id="clm-x",
                subject_text="x",
                relation=Relation.UNRESOLVED,
                state=EpistemicState.UNRESOLVED,
                evaluation_id="evl-x",
            )

    def test_no_claim_in_a_lone_corpus_is_supported(self):
        """The v0.1 shape, tested directly: N claims, zero attestations."""
        content = " ".join(f"The subsystem {n} is isolated." for n in ("one", "two", "three"))
        store, art, ev = build(content)
        states = {v.state for v in ev.evaluate_all(apply=False)}
        assert EpistemicState.SUPPORTED not in states
        store.close()


# -- contradiction is the real product ----------------------------------


class TestContradiction:
    def test_it_finds_a_direct_contradiction(self, conflict):
        ev, _store, _art = conflict
        contradicted = [v for v in ev.evaluate_all(apply=False)
                        if v.relation is Relation.CONTRADICTED]
        assert len(contradicted) == 2, "the conflict pair should both be flagged"

    def test_both_sides_name_each_other(self, conflict):
        ev, _store, _art = conflict
        contradicted = [v for v in ev.evaluate_all(apply=False)
                        if v.relation is Relation.CONTRADICTED]
        a, b = contradicted
        assert a.subject_id in b.contradicted_by
        assert b.subject_id in a.contradicted_by

    def test_contradiction_pairs_on_polality_not_wording(self, conflict):
        """Same terms, opposite polarity. Morphology may differ."""
        ev, _store, _art = conflict
        texts = {v.subject_text for v in ev.evaluate_all(apply=False)
                 if v.relation is Relation.CONTRADICTED}
        assert "The runtime enforces provenance at write time." in texts
        assert "The runtime does not enforce provenance at write time." in texts

    def test_unrelated_claims_are_not_contradictions(self, conflict):
        ev, _store, _art = conflict
        for v in ev.evaluate_all(apply=False):
            if "compiles offline" in v.subject_text or "independent" in v.subject_text:
                assert v.relation is not Relation.CONTRADICTED

    def test_a_repeated_contradiction_is_still_flagged(self):
        """One claim, its negation, and a restatement of that negation.

        The restatement compiles to its own claim — same text, different source
        span, so a different content id — which means there are two distinct
        claims on the negative side. All three are in conflict, and the
        evaluator must say so about all three rather than stopping at the first
        pair it happens to find.
        """
        content = (
            "The runtime is local-first. "
            "The runtime is not local-first. "
            "The runtime is not local-first."
        )
        store, art, ev = build(content)
        assert len(art.manifest.claim_ids) == 3
        contradicted = [v for v in ev.evaluate_all(apply=False)
                        if v.relation is Relation.CONTRADICTED]
        assert len(contradicted) == 3
        # The positive claim is refuted by both negatives; each negative is
        # refuted by the positive.
        positive = [v for v in contradicted if "not" not in v.subject_text]
        assert len(positive) == 1
        assert len(positive[0].contradicted_by) == 2
        store.close()

    def test_double_negation_does_not_manufacture_a_contradiction(self):
        """Unknown polarity means no contradiction, not "not negated".

        Treating stacked negators as positive polarity would let a doubly
        negated claim appear to refute a plain one — the mirror image of the
        defect this module exists to prevent.
        """
        content = (
            "The runtime is not local-first. "
            "The runtime is not without local-first."
        )
        store, art, ev = build(content)
        assert [v for v in ev.evaluate_all(apply=False)
                if v.relation is Relation.CONTRADICTED] == []
        store.close()

    def test_unknown_polarity_blocks_contradiction(self):
        a = normalize("The runtime is not without a network.")
        assert a.polarity is NEGATION_UNKNOWN
        assert not a.polarity_known

    def test_contradiction_is_symmetric(self, conflict):
        ev, _store, _art = conflict
        by_id = {v.subject_id: v for v in ev.evaluate_all(apply=False)}
        for cid, v in by_id.items():
            for other in v.contradicted_by:
                assert cid in by_id[other].contradicted_by


# -- attestation is narrow ----------------------------------------------


class TestAttestation:
    def test_a_restatement_attests(self, corroborated):
        ev, _store, _art = corroborated
        attested = [v for v in ev.evaluate_all(apply=False) if v.relation is Relation.ATTESTED]
        assert attested
        for v in attested:
            assert v.attested_by
            assert v.subject_id not in v.attested_by

    def test_attestation_requires_a_contiguous_run(self, corroborated):
        """A shared term bag is not a restatement.

        "provenance is enforced by the runtime" contains every content term of
        "the runtime enforces provenance" and is a different assertion in a
        different order. Bag-of-terms attestation would call it support.
        """
        store, art, ev = build(
            "The runtime enforces provenance. "
            "Provenance is enforced by the runtime. "
            "An unrelated note about offline builds."
        )
        attested = [v for v in ev.evaluate_all(apply=False) if v.relation is Relation.ATTESTED]
        # The reordering must not produce attestation.
        assert attested == [], "term reordering was treated as a restatement"
        store.close()


# -- states: default is INCONCLUSIVE ------------------------------------


class TestStateAssignment:
    def test_the_default_is_inconclusive_not_supported(self, conflict):
        ev, _store, _art = conflict
        verdicts = ev.evaluate_all(apply=False)
        inconclusive = [v for v in verdicts if v.relation is Relation.INCONCLUSIVE]
        assert inconclusive, "a lexical neighbour is not a conclusion"
        for v in inconclusive:
            assert v.state is EpistemicState.INCONCLUSIVE

    def test_apply_moves_the_state(self, conflict):
        ev, store, _art = conflict
        target = sorted(ev._norms)[0]
        before = store.get_claim(target)["state"]
        verdict = ev.evaluate(target, apply=True)
        after = store.get_claim(target)["state"]
        assert before == EpistemicState.UNEXAMINED.value
        assert after == verdict.state.value

    def test_dry_run_does_not_move_the_state(self, conflict):
        ev, store, _art = conflict
        before = {r["id"]: r["state"] for r in store.claims()}
        ev.evaluate_all(apply=False)
        assert {r["id"]: r["state"] for r in store.claims()} == before

    def test_dry_run_still_leaves_an_audit_record(self, conflict):
        ev, store, _art = conflict
        target = sorted(ev._norms)[0]
        ev.evaluate(target, apply=False)
        assert store.evaluations_for(target), "an unauditable dry run is not a dry run"

    def test_every_state_change_has_an_evaluation(self, conflict):
        ev, store, _art = conflict
        ev.evaluate_all(apply=True)
        moved = [r for r in store.claims() if r["state"] != EpistemicState.UNEXAMINED.value]
        assert moved
        for row in moved:
            assert store.evaluations_for(row["id"]), (
                f"claim {row['id']} is {row['state']} with no evaluation record"
            )

    def test_evaluator_never_reaches_validated(self, conflict):
        """VALIDATED means independently reproduced: a second artifact."""
        ev, _store, _art = conflict
        states = {v.state for v in ev.evaluate_all(apply=False)}
        assert EpistemicState.VALIDATED not in states


# -- every verdict is recorded and replayable ---------------------------


class TestVerdictRecords:
    def test_a_verdict_names_its_method(self, conflict):
        ev, _store, _art = conflict
        for v in ev.evaluate_all(apply=False):
            assert v.method == EVALUATOR_METHOD

    def test_the_record_is_in_the_store(self, conflict):
        ev, store, _art = conflict
        verdict = ev.evaluate(sorted(ev._norms)[0], apply=False)
        rows = store.evaluations_for(verdict.subject_id)
        assert len(rows) == 1
        assert rows[0]["relation"] == verdict.relation.value
        assert rows[0]["method"] == EVALUATOR_METHOD

    def test_attested_by_survives_the_round_trip(self, corroborated):
        ev, store, _art = corroborated
        verdict = next(v for v in ev.evaluate_all(apply=False)
                       if v.relation is Relation.ATTESTED)
        row = store.evaluations_for(verdict.subject_id)[0]
        assert json.loads(row["attested_by"]) == list(verdict.attested_by)
        assert verdict.attested_by, "an attested verdict must name its attestors"

    def test_evaluation_of_an_unknown_claim_raises(self, conflict):
        ev, _store, _art = conflict
        with pytest.raises(KeyError):
            ev.evaluate("clm-not-in-this-version")

    def test_evaluator_is_bound_to_a_version(self, conflict):
        ev, store, _art = conflict
        stranger = store.add_claim(
            text="A claim from outside the bound version.",
            state=EpistemicState.UNEXAMINED,
            transaction_time=TT,
        )
        with pytest.raises(KeyError):
            ev.evaluate(stranger)

    def test_verdict_serializes(self, conflict):
        ev, _store, _art = conflict
        for v in ev.evaluate_all(apply=False):
            json.dumps(v.to_dict())


# -- determinism --------------------------------------------------------


class TestDeterminism:
    def test_the_same_artifact_yields_the_same_verdicts(self, conflict):
        ev, _store, _art = conflict
        first = [v.to_dict() for v in ev.evaluate_all(apply=False)]
        second = [v.to_dict() for v in ev.evaluate_all(apply=False)]
        assert first == second

    def test_verdicts_do_not_depend_on_claim_order(self, conflict):
        ev, _store, _art = conflict
        forward = {v.subject_id: v.relation for v in ev.evaluate_all(apply=False)}
        backward = {v.subject_id: v.relation for v in
                    reversed(ev.evaluate_all(apply=False))}
        assert forward == backward

    def test_a_fresh_evaluator_agrees_with_the_first(self, conflict):
        ev, store, art = conflict
        second = Evaluator(store, art.manifest)
        assert {v.subject_id: v.relation for v in ev.evaluate_all(apply=False)} == {
            v.subject_id: v.relation for v in second.evaluate_all(apply=False)
        }

    def test_no_confidence_value_is_produced(self, conflict):
        """The relation is not a score. Nothing numeric escapes."""
        ev, _store, _art = conflict
        for v in ev.evaluate_all(apply=False):
            d = v.to_dict()
            assert isinstance(d["relation"], str)
            for key, value in d.items():
                assert not isinstance(value, float), f"{key} is a score: {value}"


# -- the stemmer fails toward INCONCLUSIVE ------------------------------


class TestNormalizationFailsSafe:
    def test_verb_families_agree(self):
        for family in (["verify", "verified", "verifies"], ["enforce", "enforced", "enforces"],
                       ["apply", "applied", "applies"]):
            assert len({stem(w) for w in family}) == 1, f"{family} does not agree"

    def test_the_stemmer_does_not_merge_distinct_words(self):
        """Over-stripping manufactures contradictions, so it must not happen."""
        assert stem("early") != stem("ear")
        assert stem("only") == "only"
        assert stem("class") == "class"
        assert stem("independence") != stem("independent")

    def test_normalization_is_idempotent(self):
        for text in ("The runtime enforces provenance.", "Provenance is content addressed."):
            once = normalize(text)
            twice = normalize(" ".join(once.sequence))
            assert once.terms == twice.terms

    def test_normalization_is_case_and_punctuation_insensitive(self):
        a = normalize("The runtime ENFORCES provenance.")
        b = normalize("the runtime enforces provenance")
        assert a.terms == b.terms
        assert a.sequence == b.sequence

    def test_an_empty_claim_normalizes_without_error(self):
        n = normalize("")
        assert n.terms == frozenset()
        assert n.polarity is False
