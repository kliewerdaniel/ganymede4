"""Control characters are a boundary, not content (ADR-015).

The real corpus contains exactly one NUL byte, at character 242,506 of a
415,353-character ChatGPT export, in the middle of ``⇡ =E⇡ ⇥\\x001 +`` — a
PDF whose font encoding lost a glyph index. One byte, in the middle, with
172,847 characters of real record after it.

These tests pin the decision that came out of finding it: the source stays
byte-exact, the corrupt region is fenced, and the surrounding text is
unaffected. They also pin the two ways that could go wrong — stripping the
byte (breaks content identity) and rejecting the source (destroys the record).
"""

from __future__ import annotations

import pytest

from sovereign_runtime.compile.segment import (
    CONTROL_BOUNDARIES,
    contains_control,
    is_proposition,
    segment,
)

NUL = "\x00"
BEL = "\x07"
DEL = "\x7f"
NEL = "\x85"
APC_END = "\x9f"


class TestControlDetection:
    def test_a_nul_is_a_control_character(self):
        assert contains_control(f"text{NUL}more") is True

    @pytest.mark.parametrize("ch", [NUL, BEL, DEL, NEL, APC_END, "\x01", "\x1b"])
    def test_c0_and_c1_controls_are_detected(self, ch):
        assert contains_control(f"a{ch}b") is True

    @pytest.mark.parametrize(
        "text",
        [
            "ordinary prose with no controls at all",
            "tabs\tand\nnewlines\r\nare layout, not corruption",
            "math: ⇡ = E[⇡] and α + β ≥ γ",
            "punctuation — “quotes”, ‘apostrophes’; em-dashes.",
        ],
    )
    def test_ordinary_text_is_not_flagged(self, text):
        assert contains_control(text) is False

    def test_whitespace_controls_are_excluded_from_the_boundary_set(self):
        # These are text layout. Treating a newline as a corruption would
        # shred every multi-line document in the corpus.
        for ch in "\t\n\r":
            assert ch not in CONTROL_BOUNDARIES

    def test_every_c0_control_except_whitespace_is_a_boundary(self):
        for c in range(0x20):
            ch = chr(c)
            if ch in "\t\n\r":
                assert ch not in CONTROL_BOUNDARIES
            else:
                assert ch in CONTROL_BOUNDARIES


class TestPropositionRejection:
    def test_a_long_segment_containing_a_nul_is_not_a_proposition(self):
        # The real failure: this fragment is far past MIN_CLAIM_CHARS and full
        # of letters, so ADR-012's length-and-letter test passed it. Only the
        # control check stops it.
        fragment = f"{NUL}1 +{NUL}2 +{NUL}3 = ... some equation fragment here"
        assert len(fragment.strip()) >= 25
        assert sum(1 for c in fragment if c.isalpha()) >= 1
        assert is_proposition(fragment) is False

    def test_control_rejection_is_independent_of_length(self):
        short = f"hi{NUL}"
        assert len(short.strip()) < 25
        assert is_proposition(short) is False  # for length, as before


class TestSegmentationFencing:
    def test_a_control_never_lands_inside_a_segment(self):
        text = "A normal claim that is long enough to qualify." + NUL + "junk here"
        for seg in segment(text):
            assert not contains_control(seg.text)

    def test_text_around_a_control_still_segments_normally(self):
        text = (
            "A perfectly normal claim that is long enough to qualify. "
            + NUL
            + " Mid "
            + NUL
            + " fragment. Another real sentence follows here."
        )
        segs = segment(text)
        kept = [s.text for s in segs if is_proposition(s.text)]
        assert kept == [
            "A perfectly normal claim that is long enough to qualify.",
            "Another real sentence follows here.",
        ]

    def test_offsets_stay_verbatim_across_a_control(self):
        text = "Before this point the text is entirely ordinary. " + NUL + " After it too."
        for seg in segment(text):
            assert text[seg.start : seg.end] == seg.text, (
                "segment offsets must index the original source exactly, "
                "including where a control character split the chunk"
            )

    def test_a_leading_control_does_not_shift_offsets(self):
        text = NUL + "A claim that begins after a control character entirely."
        segs = segment(text)
        assert len(segs) == 1
        assert segs[0].text == "A claim that begins after a control character entirely."
        assert segs[0].start == 1

    def test_consecutive_controls_do_not_produce_empty_segments(self):
        text = "A real claim of sufficient length here." + NUL * 20 + "Trailing junk."
        for seg in segment(text):
            assert seg.text.strip() == seg.text
            assert len(seg.text) > 0

    def test_a_trailing_control_does_not_truncate_the_last_segment(self):
        text = "The final sentence of this document is a real one." + NUL
        segs = segment(text)
        assert segs[-1].text == "The final sentence of this document is a real one."


class TestTheDecisionItself:
    """Pin *why* it is a fence and not a rejection or a strip.

    These are the two wrong answers. A test that only checks the happy path
    leaves both of them free to come back.
    """

    def test_a_single_bad_byte_does_not_destroy_the_document(self):
        # 172,847 characters of real record follow this NUL in the real corpus.
        # Rejecting the source over one byte is not an acceptable trade.
        after = "This text after the bad byte must still be readable. " * 500
        text = "Before the bad byte. " + NUL + after
        segs = segment(text)
        kept = [s for s in segs if is_proposition(s.text)]
        assert kept, "text after a single control byte must still produce claims"
        assert any(after[:40] in s.text for s in kept)

    def test_the_source_text_is_never_modified(self):
        # Content addressing depends on the source bytes being exactly what
        # was ingested. Stripping the NUL would be silent corruption.
        text = f"Original text{NUL}with a bad byte in it."
        segs = segment(text)
        for seg in segs:
            assert text[seg.start : seg.end] == seg.text
        assert NUL in text, "the segmenter must not mutate its input"

    def test_segmentation_without_controls_is_unchanged(self):
        # The control boundary must be inert on clean input, or every existing
        # offset in the corpus is suspect. Verified identical output with the
        # change stashed.
        #
        # Note "p.m." splits — `p` is not in ABBREVIATIONS. That is a
        # pre-existing limitation with nothing to do with controls, and
        # re-deciding it here would be scope creep in a test whose subject is
        # control characters.
        text = "Dr. Smith went to Washington. He arrived at 5 p.m. on Tuesday."
        segs = [s.text for s in segment(text)]
        assert segs == [
            "Dr. Smith went to Washington.",
            "He arrived at 5 p.",
            "m.",
            "on Tuesday.",
        ]
        assert not any(contains_control(s) for s in segs)
