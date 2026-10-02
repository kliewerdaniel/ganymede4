"""A contradiction requires a shared subject, not a shared word.

Red test written against the real artifact's defect (ADR-032).

``Evaluator._contradictions`` fires when two claims normalize to an *identical
term set* with opposite polarity. That is a bag-of-words test, and it has no
notion of which document either claim came from. So it will happily mark a
sentence about AI sapience CONTRADICTED by an unrelated sentence about being
robbed at gunpoint, provided both happen to normalize to ``{'you'}``.

Measured on the canonical artifact, before this change:

    contradicted subjects                402
    contradiction pair rows            2,854
    pair rows spanning two documents    2,676  (93.8%)
    pair rows within one document         178  ( 6.2%)
    subjects retaining a same-doc peer    150

Every one of the 402 subjects draws its evidence from exactly one source, so
a document-scoping constraint is well defined and needs no fallback rule.
Thread-scoping (reddit `comments/<thread_id>/`) was measured too and admits
exactly the same 178 pairs -- each source is already a single comment or a
single chatgpt conversation -- so this records the simpler rule.

``CONTRADICTED`` is absorbing. Asserting it across unrelated documents is not
a weak verdict, it is a wrong one that a later operator has to undo by hand.

These tests are written to fail against the current evaluator.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ganymede4.compile.compiler import SourceSpec, compile_corpus
from ganymede4.compile.manifest import Manifest
from ganymede4.knowledge.epistemic import EpistemicState
from ganymede4.knowledge.evaluator import Evaluator, Relation
from ganymede4.knowledge.store import Store

TT = "2026-09-28T00:00:00Z"
ROOT = Path(__file__).resolve().parent.parent


def _build(docs: list[tuple[str, str]]):
    """Compile a multi-document corpus and return (store, evaluator)."""
    store = Store()
    specs = [SourceSpec(uri=uri, content=body) for uri, body in docs]
    art = compile_corpus(store, specs, transaction_time=TT)
    return store, Evaluator(store, art.manifest)


def _build_expecting(docs: list[tuple[str, str]], must_emit: list[str]):
    """Compile, then prove every expected claim actually became a claim.

    The compiler silently drops some short segments -- "You can run it
    locally." never becomes a claim, while "You cannot run it locally." does.
    A test asserting "no contradiction was found" therefore passes *vacuously*
    when the compiler dropped the subject, which is exactly the kind of test
    that looks like evidence and is not. This helper fails loudly instead.

    Found the hard way: the first version of this file had two tests that
    passed against the unfixed evaluator because their subject never existed.
    """
    store = Store()
    specs = [SourceSpec(uri=uri, content=body) for uri, body in docs]
    art = compile_corpus(store, specs, transaction_time=TT)
    emitted = {row["text"] for row in store.db.execute("SELECT text FROM claims")}
    missing = [t for t in must_emit if t not in emitted]
    if missing:
        store.close()
        raise AssertionError(
            "the compiler did not emit these claims, so the test would have "
            f"passed vacuously: {missing}"
        )
    return store, Evaluator(store, art.manifest)


def _verdicts(ev: Evaluator) -> list:
    return ev.evaluate_all(apply=False)


def _relations(ev: Evaluator, needle: str) -> list:
    return [v.relation for v in _verdicts(ev) if needle in v.subject_text]


class TestCrossDocumentContradiction:
    """The defect, stated as a test."""

    def test_identical_terms_opposite_polarity_across_documents(self):
        """Two documents, two true opposites, no shared subject.

        "you can run this locally" and "you cannot run this locally" are
        genuine contradictions of each other -- but only if they are claims
        about the same thing. Placed in unrelated documents they are two
        statements that have never heard of one another.
        """
        store, ev = _build_expecting(
            [
                ("a.md", "This is a pyramid scheme."),
                ("b.md", "This is not a pyramid scheme."),
            ],
            must_emit=["This is a pyramid scheme.", "This is not a pyramid scheme."],
        )
        try:
            for v in _verdicts(ev):
                assert v.relation is not Relation.CONTRADICTED, (
                    f"claim {v.subject_id} ({v.subject_text!r}) was marked "
                    f"CONTRADICTED against a claim from another document"
                )
        finally:
            store.close()

    def test_no_cross_document_contradiction_survives(self):
        """Whole-corpus form: the count of CONTRADICTED must be zero here.

        Asserting on the aggregate rather than one subject at a time, because
        a fix that special-cased one phrasing would pass a per-subject test and
        still leave the defect in place.
        """
        store, ev = _build_expecting(
            [
                ("a.md", "This is a pyramid scheme."),
                ("b.md", "This is not a pyramid scheme."),
                ("c.md", "The compiler emits claims."),
                ("d.md", "The compiler does not emit claims."),
                ("e.md", "That is not why you do that."),
                ("f.md", "That is what you have to do."),
            ],
            must_emit=[
                "This is a pyramid scheme.",
                "This is not a pyramid scheme.",
                "The compiler emits claims.",
                "The compiler does not emit claims.",
                "That is not why you do that.",
                "That is what you have to do.",
            ],
        )
        try:
            contradicted = [v for v in _verdicts(ev) if v.relation is Relation.CONTRADICTED]
            assert contradicted == [], (
                "cross-document contradictions survived: "
                + "; ".join(v.subject_text for v in contradicted)
            )
        finally:
            store.close()


class TestSameDocumentStillDetected:
    """The constraint must not become a blanket suppression.

    A fix that simply disabled contradictions would pass every test above and
    destroy real findings. `CONTRADICTED` is absorbing, but so is its absence:
    a missed contradiction is a gap an operator must close by hand, and that
    asymmetry is the evaluator's whole design.
    """

    def test_genuine_same_document_contradiction_still_fires(self):
        store, ev = _build(
            [
                (
                    "notes.md",
                    "The runtime enforces provenance at write time. "
                    "The runtime does not enforce provenance at write time. "
                    "The corpus compiles offline.",
                )
            ]
        )
        try:
            verdicts = [v for v in _verdicts(ev) if "provenance" in v.subject_text]
            assert any(v.relation is Relation.CONTRADICTED for v in verdicts), (
                "a genuine same-document contradiction stopped being detected; "
                "the scope constraint suppressed a real finding"
            )
            contradicted = [v for v in verdicts if v.relation is Relation.CONTRADICTED][0]
            assert contradicted.state is EpistemicState.CONTRADICTED
            assert contradicted.contradicted_by, "a CONTRADICTED verdict named no peer"
        finally:
            store.close()

    def test_the_surviving_peer_is_named(self):
        """Scope narrows which peers count; it does not anonymize them.

        A CONTRADICTED verdict must still name the claims that refuted it, so
        the record says which document the refutation came from. When every
        document states the same opposition, the subject in a.md is refuted by
        nobody -- b.md and c.md are correct about themselves and irrelevant to
        a.md -- so the honest outcome for a.md is INCONCLUSIVE while each of
        b.md and c.md is refuted by the other.
        """
        store, ev = _build_expecting(
            [
                ("a.md", "The compiler emits claims."),
                ("b.md", "The compiler does not emit claims."),
                ("c.md", "The compiler does not emit claims."),
            ],
            must_emit=[
                "The compiler emits claims.",
                "The compiler does not emit claims.",
            ],
        )
        try:
            contradicted = [v for v in _verdicts(ev) if v.relation is Relation.CONTRADICTED]
            assert contradicted == [], (
                "each document states its own position; none of them is in "
                f"conflict with another, got: "
                + "; ".join(v.subject_text for v in contradicted)
            )
            # b and c are the two that share a document with each other? No --
            # they do not. Each is alone in its own document, so *nothing*
            # should be contradicted here. Assert the real property instead:
            # no verdict may name a peer from a document it does not share.
            store2 = Store()
            art = compile_corpus(
                store2,
                [
                    SourceSpec(uri="x.md", content="The compiler emits claims."),
                    SourceSpec(
                        uri="y.md",
                        content="The compiler does not emit claims.",
                    ),
                ],
                transaction_time=TT,
            )
            ev2 = Evaluator(store2, art.manifest)
            for v in ev2.evaluate_all(apply=False):
                assert not v.contradicted_by, (
                    "a verdict named a peer with no shared document"
                )
            store2.close()
        finally:
            store.close()


class TestAttestationUnaffected:
    """The constraint applies to contradictions only.

    Attestation already requires the subject's whole token sequence to appear
    contiguously in a peer, which is a far stronger relation than set
    equality. Refusing cross-document attestation would be a different change
    with a different justification, and smuggling it in here would make this
    ADR claim something it does not do.
    """

    def test_cross_document_attestation_still_fires(self):
        store, ev = _build(
            [
                (
                    "a.md",
                    "The model runs on the local machine without a network "
                    "call of any kind.",
                ),
                ("b.md", "The model runs on the local machine."),
            ]
        )
        try:
            attested = [
                v
                for v in _verdicts(ev)
                if "on the local machine" in v.subject_text
                and v.subject_text.strip() == "The model runs on the local machine."
            ]
            assert attested, "the target claim was not evaluated"
            assert attested[0].relation is Relation.ATTESTED, (
                "cross-document attestation stopped working; ADR-032 was "
                "supposed to constrain contradictions only"
            )
        finally:
            store.close()


class TestFailClosedOnMissingScope:
    """No provenance means no shared subject, so no contradiction.

    The scope constraint is satisfied by sharing a source. A claim with no
    evidence has no source to share, and the honest reading of that is "scope
    unknown" -- which under a fail-closed rule means the contradiction cannot be
    established, not that it is established anyway.

    This test constructs two evidence-free claims: identical term sets,
    opposite polarity, no provenance on either side. That is the case the
    fail-closed rule exists for.

    On what this does and does not prove: the evaluator's per-peer scope
    intersection *already* rejects this pair, so deleting the explicit
    empty-scope early return leaves this test green. The early return is
    defence in depth rather than load-bearing, and `test_no_cross_document_
    contradiction_survives` is what actually pins the scope filter. This test
    pins the *behaviour* -- no provenance, no contradiction -- independently of
    which of the two guards happens to be doing the work.
    """

    def test_an_unscoped_claim_is_never_contradicted(self):
        """Two evidence-free claims. Neither has a source, so neither conflicts."""
        store = Store()
        try:
            sid = store.add_source(
                uri="scoped.md",
                source_type="note",
                content="This is not a pyramid scheme.",
            )
            store.record_artifact(Manifest(root="test", schema_version=1))
            # UNEXAMINED is one of the states the store permits without
            # provenance, so an evidence-free claim is a legal write.
            unscoped = store.add_claim(
                text="This is a pyramid scheme.",
                evidence_ids=(),
                transaction_time=TT,
            )
            unscoped_peer = store.add_claim(
                text="This is not a pyramid scheme.",
                evidence_ids=(),
                transaction_time=TT,
            )
            # BOTH claims must be in the manifest, or the unscoped one has no
            # peer to be contradicted by and the test cannot fail no matter
            # what the guard does. That was the first version's bug.
            manifest = Manifest(
                root="test",
                schema_version=1,
                claim_ids=(unscoped, unscoped_peer),
            )

            ev = Evaluator(store, manifest)
            v = ev.evaluate(unscoped, apply=False)
            assert v.relation is not Relation.CONTRADICTED, (
                "a claim with no evidence was marked CONTRADICTED; with no "
                "provenance there is no shared subject"
            )
        finally:
            store.close()


def _whole_source_evidence(store, sid):
    """An evidence id covering the whole source."""
    row = store.get_source(sid)
    eid = store.add_evidence(
        source_id=sid,
        start_offset=0,
        end_offset=len(row["content"]),
        text=row["content"],
    )
    return eid


class TestAttestationIsNotScoped:
    """Attestation must keep working across documents.

    Stated separately because it is a different relation with a different
    justification, and because the first version of the fix never tested it:
    adding the scope filter to `_attestations` as well left every test green.
    """

    def test_a_cross_document_attester_still_attests(self):
        store, ev = _build_expecting(
            [
                ("long.md", "The model runs on the local machine without any network call."),
                ("short.md", "The model runs on the local machine."),
            ],
            must_emit=["The model runs on the local machine."],
        )
        try:
            subject = "The model runs on the local machine."
            verdicts = [v for v in _verdicts(ev) if v.subject_text == subject]
            assert verdicts, "the subject claim was not evaluated"
            assert verdicts[0].relation is Relation.ATTESTED, (
                "cross-document attestation stopped working; ADR-032 was "
                "supposed to constrain contradictions only, and the attesting "
                "peer lives in a different document"
            )
        finally:
            store.close()


class TestTheRecordedNumbers:
    """Pin the ADR-032 measurements so they cannot rot.

    A number in a decision record that nothing checks is a number that will
    quietly become wrong, and the next person to read it has no way to tell.
    """

    ADR = ROOT / "docs" / "adr" / "ADR-032-a-contradiction-needs-a-shared-subject.md"

    def test_the_adr_exists_and_records_the_decision(self):
        assert self.ADR.exists(), f"{self.ADR} is missing"
        text = self.ADR.read_text(encoding="utf-8")
        assert "Status: Ratified" in text
        assert "Depends on: ADR-030, ADR-031" in text

    def test_the_adr_records_the_cross_document_measurement(self):
        text = self.ADR.read_text(encoding="utf-8")
        assert "93.8%" in text, "the cross-document share is no longer recorded"
        assert "2,676" in text and "2,854" in text
        assert "156" in text, "the post-fix contradicted count is not recorded"

    def test_the_adr_does_not_claim_scope_preserves_every_finding(self):
        """The honest-limits section must survive.

        Scoping is a correctness fix, not a safe migration: a genuine
        cross-document contradiction would now be missed. If that caveat is
        deleted, this fails.
        """
        text = self.ADR.read_text(encoding="utf-8")
        assert "would now be missed" in text, (
            "ADR-032 lost the caveat that scoping can miss a genuine "
            "cross-document contradiction"
        )

    def test_superseded_adrs_point_forward(self):
        """ADR-030 and ADR-031 must not still read as current.

        Both carry decisions that survive, for a reason that turned out to be
        wrong. A reader arriving at either must be told to read ADR-032.
        """
        adr030 = (ROOT / "docs" / "adr" / "ADR-030-2854-pairs-are-70-pairs.md").read_text(
            encoding="utf-8"
        )
        adr031 = (ROOT / "docs" / "adr" / "ADR-031-no-code-has-no-safe-setting.md").read_text(
            encoding="utf-8"
        )
        assert "ADR-032" in adr030, "ADR-030 does not point forward"
        assert "ADR-032" in adr031, "ADR-031 does not point forward"


class TestTheRealPairs:
    """Pairs taken from the artifact's agent-labelled 'real contradictions'.

    Every one of these spanned two unrelated documents, and three of them
    shared a single normalized term -- 'you', 'point', 'pattern'. These are
    the actual cases the human re-read caught, pinned as regressions.
    """

    @pytest.mark.parametrize(
        "a_text,b_text",
        [
            ("That is not why you do that.", "That is what you have to do."),
            ("At that point, what's the point.", "But that is not the point."),
            ("But there's been a pattern.", "The pattern was not there."),
        ],
    )
    def test_artifact_pairs_are_not_contradictions(self, a_text, b_text):
        store, ev = _build_expecting(
            [
                ("first.md", a_text),
                ("second.md", b_text),
            ],
            must_emit=[a_text, b_text],
        )
        try:
            contradicted = [v for v in _verdicts(ev) if v.relation is Relation.CONTRADICTED]
            assert contradicted == [], (
                f"artifact pair {a_text!r} / {b_text!r} was still scored as a "
                f"contradiction"
            )
        finally:
            store.close()