"""Markdown block structure delimits one authored unit from the next (ADR-016).

ADR-015 fenced control characters. This fences the *other* thing the real
corpus proved is not the author's writing: the exporter's own structure.

A Reddit export is markdown. `## Parent Comment`, `---` rules, and ``` fences
are inserted by the export tool. None is a sentence terminator, so a block
whose text carries no `.` segmented as one claim spanning every unit inside
it, and the witness's top-ranked answer to "parent comment" was the literal
string "## Parent Comment".

These tests pin the property that matters: a claim spans one authored unit,
never two. The two failure modes are symmetric and both are tested — gluing
unrelated text together (manufacturing evidence) and discarding text along
with its label (losing evidence).
"""

from __future__ import annotations

import pytest

from ganymede4.compile.segment import is_proposition, segment


def texts(text: str) -> list[str]:
    return [s.text for s in segment(text)]


class TestUnitIsolation:
    """The defect: several authored units fused into one claim."""

    def test_separate_blocks_do_not_fuse_into_one_claim(self):
        # This exact string came out as ONE claim, spanning two comments by two
        # different people and attributing both to a single evidence span.
        raw = "---\n\n## Parent Comment\n\nDM\n\n---\n\n## Parent Comment\n\nWhy?"
        segs = segment(raw)
        assert len(segs) == 4, [s.text for s in segs]
        # No segment holds text from two different authored units. "Parent
        # Comment" is the exporter's label and survives as its own short
        # segment, which ADR-012's length rule then discards — the label never
        # reaches a claim, and neither does anyone else's comment attached to it.
        assert not any("DM" in s.text and "Why" in s.text for s in segs)

    def test_a_thematic_break_splits_paragraphs(self):
        raw = "First para here.\n\n---\n\nSecond para here."
        assert texts(raw) == ["First para here.", "Second para here."]

    def test_a_code_fence_splits_the_blocks_around_it(self):
        raw = "```python\nx = 1\n```\n\nAfter the fence here."
        assert texts(raw) == ["x = 1", "After the fence here."]

    def test_consecutive_block_rules_do_not_emit_empty_segments(self):
        raw = "A real claim of length here.\n\n---\n\n---\n\nAnother real one here."
        segs = segment(raw)
        assert all(s.text.strip() == s.text and s.text for s in segs)
        assert texts(raw) == ["A real claim of length here.", "Another real one here."]


class TestTextIsNotDiscarded:
    """The opposite failure: throwing away real text with its label."""

    def test_heading_text_survives_its_hashes(self):
        # The heading text is the author's. Only the `#`s are the exporter's.
        raw = "## Why were libraries burnt?\n\nBecause of the fire last night."
        assert texts(raw) == [
            "Why were libraries burnt?",
            "Because of the fire last night.",
        ]

    def test_a_heading_becomes_a_real_claim(self):
        raw = "## Why were the libraries burnt in the fire last night?"
        kept = [s.text for s in segment(raw) if is_proposition(s.text)]
        assert kept == ["Why were the libraries burnt in the fire last night?"]

    def test_nothing_is_lost_from_the_source(self):
        # Every authored word must survive segmentation somewhere. The `#`s and
        # rules are the only characters allowed to vanish.
        raw = "## A heading with real content\n\nThe body text is here."
        assert set(texts(raw)) == {
            "A heading with real content",
            "The body text is here.",
        }


class TestInlineMarkupIsNotFenced:
    """Block structure only. A fence that fires mid-paragraph destroys prose."""

    @pytest.mark.parametrize(
        "text",
        [
            "Use the # symbol when you tag a post in the archive today.",
            "The - dash is a minus sign in this arithmetic example here.",
            "Compute 3 * 4 to get twelve when writing the tutorial today.",
            "A line ending with a hyphen- continues the word across it here.",
        ],
    )
    def test_prose_containing_markup_characters_is_one_segment(self, text):
        assert texts(text) == [text]

    def test_a_list_bullet_is_not_a_thematic_break(self):
        # A single `-` opens a list item; a run of three is a rule. Getting this
        # backwards would split every bulleted list in the corpus mid-item.
        raw = "- first item here\n- second item here"
        assert texts(raw) == [raw]

    def test_a_two_character_run_is_not_a_rule(self):
        raw = "Before the line\n--\nAfter the line"
        assert "Before the line\n--" in texts(raw)[0]


class TestOffsetsStayExact:
    def test_offsets_are_verbatim_across_every_kind_of_block(self):
        raw = (
            "---\n\n## Parent Comment\n\nA first real claim here.\n\n"
            "## Another Section\n\nA second real claim here.\n\n"
            "```\ncode_line_here\n```\n\n---\n\n## Third\n\nA third real claim."
        )
        for seg in segment(raw):
            assert raw[seg.start : seg.end] == seg.text, (
                "a claim whose offsets do not index its source exactly is "
                "evidence the store will reject and the auditor will flag"
            )

    def test_offsets_are_monotonic_and_non_overlapping(self):
        raw = "## One heading\n\nFirst body text.\n\n---\n\n## Two\n\nSecond body."
        segs = segment(raw)
        for a, b in zip(segs, segs[1:]):
            assert a.end <= b.start, "segments must not overlap"


class TestOrdinaryProseIsUntouched:
    def test_plain_prose_segments_exactly_as_before(self):
        # Verified identical with the change stashed. Without this check, new
        # boundaries are indistinguishable from a silent offset shift over the
        # whole corpus.
        raw = "He arrived at 5 p.m. on Tuesday. Dr. Smith met him there."
        segs = texts(raw)
        assert "He arrived at 5 p." in segs
        assert "Dr. Smith met him there." in segs

    def test_a_hash_mid_line_is_never_a_boundary(self):
        raw = "The issue is C# and F# and other such names in this sentence."
        assert texts(raw) == [raw]
