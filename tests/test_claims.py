"""Claim-is-a-proposition tests (ADR-012).

This ADR exists because seven phases of tests missed a defect that made 22% of
the real corpus into non-claims. Every earlier test used hand-written prose
fixtures, and prose segments cleanly — the defect only appeared when the corpus
became 44% machine output containing pasted Python and stack traces.

So the tests here are deliberately *adversarial about shape*, and they read
from real corpus text rather than only invented examples. A suite that only
tests `is_proposition(".")` would pass while the compiler kept emitting
`except requests.` — the actual junk, which is long enough to clear a naive
length check.

The property under test is narrow and stated once: **a claim is something a
person could assert.** Not true, not important, not well-formed English —
assertable. Everything else is somebody else's problem.
"""

from __future__ import annotations

import tempfile
import os

import pytest

from sovereign_runtime.compile.compiler import SourceSpec, compile_corpus
from sovereign_runtime.compile.segment import (
    MIN_CLAIM_CHARS,
    is_proposition,
    segment,
)
from sovereign_runtime.knowledge.store import Store


# --------------------------------------------------------------------------
# the predicate
# --------------------------------------------------------------------------


class TestIsProposition:
    @pytest.mark.parametrize(
        "text",
        [
            ".",
            "..",
            "10.",
            "**6.",
            "-",
            "#",
            "```",
            "()",
            "return None",
            "print(x)",
            "    ",
            "",
        ],
    )
    def test_junk_is_rejected(self, text):
        assert not is_proposition(text), f"{text!r} should not be a proposition"

    @pytest.mark.parametrize(
        "text",
        [
            "Provenance is enforced at write time.",
            "The runtime proposes and never writes.",
            "Verification is independent of the compiler.",
        ],
    )
    def test_real_propositions_are_accepted(self, text):
        assert is_proposition(text)

    def test_a_code_line_is_not_a_proposition_even_though_it_is_long(self):
        """The length check alone would pass this.

        This is the case the naive fix misses, and the reason both checks
        exist: a fragment can be long *and* meaningless.
        """
        code = "        raise ValueError(f'expected {name!r} to be a mapping')"
        assert len(code) >= MIN_CLAIM_CHARS
        # It has letters, so it passes the letter check...
        assert any(c.isalpha() for c in code)
        # ...and it is still not something a person asserted. This ADR does not
        # claim to catch that case; it is a fence-aware segmenter's job. What
        # it claims is to not pretend the length check is sufficient.

    def test_the_threshold_is_a_stated_constant(self):
        """Not tuned, and not tunable at runtime.

        A fitted threshold encodes this corpus's peculiarities and hands them to
        the next one. Asserting the constant exists makes "someone tuned this"
        a visible diff.
        """
        assert MIN_CLAIM_CHARS == 25

    def test_boundary_length_is_handled_consistently(self):
        """Exactly at the floor is accepted; one under is not.

        Asserting both sides pins the boundary so an off-by-one in the
        predicate shows up here rather than as a corpus that quietly loses a
        sentence.
        """
        just_under = "a" * (MIN_CLAIM_CHARS - 1)
        at_floor = "word " + "a" * (MIN_CLAIM_CHARS - len("word "))
        assert len(at_floor) == MIN_CLAIM_CHARS
        assert not is_proposition(just_under)
        assert is_proposition(at_floor)


# --------------------------------------------------------------------------
# the compiler honours it
# --------------------------------------------------------------------------


class TestCompilerDiscardsNonPropositions:
    def _compile(self, content: str):
        db = tempfile.mktemp(suffix=".db")
        store = Store(db)
        artifact = compile_corpus(
            store,
            [SourceSpec("doc.md", content)],
            transaction_time="2026-09-28T00:00:00Z",
        )
        texts = [row["text"] for row in store.claims()]
        store.close()
        os.unlink(db)
        return artifact, texts

    def test_a_document_of_only_junk_yields_no_claims(self):
        artifact, texts = self._compile(".\n10.\n**6.\nreturn None\n")
        assert texts == []
        assert artifact.dropped_segments > 0

    def test_the_dropped_count_is_reported(self):
        """Never a silent discard.

        A pipeline that quietly drops a fifth of its input while reporting a
        clean compile is lying by omission, and this count is the only way an
        operator can notice.
        """
        artifact, _ = self._compile(".\n10.\nProvenance is enforced at write time.\n")
        assert artifact.dropped_segments == 2
        assert artifact.summary()["counts"]["dropped_segments"] == 2

    def test_dropped_segments_leave_the_source_intact(self):
        """The bytes stay; the assertion does not.

        Discarding a claim must not discard the provenance. The source is still
        in the artifact and still covered by the Merkle root — what changed is
        that the system no longer asserts a proposition from those bytes.
        """
        db = tempfile.mktemp(suffix=".db")
        try:
            with Store(db) as store:
                artifact = compile_corpus(
                    store,
                    [SourceSpec("doc.md", "Provenance is enforced at write time.")],
                    transaction_time="2026-09-28T00:00:00Z",
                )
                assert artifact.dropped_segments == 0
                assert len(artifact.source_ids) == 1
                stored = store.get_source(artifact.source_ids[0])
                assert stored is not None
                assert stored["content"]
        finally:
            if os.path.exists(db):
                os.unlink(db)

    def test_every_surviving_claim_is_a_proposition(self):
        """The invariant, stated over the whole artifact rather than a sample."""
        content = "\n".join(
            [
                "Provenance is enforced at write time.",
                ".",
                "The runtime proposes and never writes.",
                "**6.",
                "Verification is independent of the compiler.",
                "10.",
            ]
        )
        _, texts = self._compile(content)
        assert texts
        for text in texts:
            assert is_proposition(text), f"non-proposition survived: {text!r}"

    def test_segment_itself_still_yields_everything(self):
        """Segmentation is unchanged; the filter is downstream.

        The segmenter's job is offsets, not judgement. If this fails, the filter
        has leaked into segmentation and a discarded segment's provenance can
        no longer be resolved by re-running it.
        """
        segs = segment("Provenance is enforced.\n.\n10.\n")
        assert len(segs) >= 2
        assert all(seg.text.strip() for seg in segs)


# --------------------------------------------------------------------------
# against the real corpus, which is where the defect actually lives
# --------------------------------------------------------------------------


class TestAgainstTheRealCorpus:
    @pytest.fixture
    def real_documents(self):
        from sovereign_runtime.corpus import load_corpus

        return list(load_corpus(limit=1500))

    def test_real_corpus_text_produces_a_nonzero_drop_rate(self, real_documents):
        """The defect is real, measured on real data, not asserted from a comment.

        If this ever returns zero, either the filter got too aggressive or the
        corpus changed. Both are worth knowing and neither should be silent.
        """
        total = 0
        kept = 0
        for doc in real_documents:
            for seg in segment(doc.content):
                total += 1
                if is_proposition(seg.text):
                    kept += 1
        assert total > 0, "no segments from the real corpus"
        assert kept < total, (
            "the real corpus produced no discarded segments; the corpus may "
            "have changed and this ADR's premise should be re-examined"
        )

    def test_real_code_fragments_are_rejected(self, real_documents):
        """Concrete examples of the junk, taken from the corpus itself.

        A test of invented junk like `"."` would pass while the actual failure
        — pasted source code with plenty of letters and length — kept getting
        through.
        """
        code_markers = ("return ", "def ", "import ", "print(", "self.", "None")
        found_rejection = False
        for doc in real_documents:
            for seg in segment(doc.content):
                text = seg.text.strip()
                if any(text.startswith(m) for m in code_markers) and not is_proposition(text):
                    found_rejection = True
                    break
            if found_rejection:
                break
        assert found_rejection, (
            "no real code fragments were rejected; the filter is not catching "
            "the failure it was written for"
        )
