"""Deterministic sentence segmentation with exact character offsets.

The compiler's only permitted text operation (ADR-004 §5). It splits text into
sentence-like units and returns each with the offsets at which it occurs in the
original, so every claim the compiler emits is a **verbatim substring** of its
source. Nothing here summarizes, paraphrases, merges, or ranks.

Why offsets rather than just text: the store already refuses an evidence span
whose text does not match its source at the stated offsets. If segmentation
returns exact offsets, a claim can be proven verbatim by the same check, and a
claim that is the author's sentence rather than the corpus's sentence is
rejected by construction.

The segmenter is deliberately dumb. It does not know what a sentence is; it
knows where a terminator is. Abbreviations that would trip a naive splitter
("Dr.", "e.g.") are handled by a small deny-list because a compiler that
mis-splits a citation into two claims is manufacturing evidence, which is the
one thing it must never do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

__all__ = ["Segment", "segment", "iter_segments", "TERMINATORS", "ABBREVIATIONS"]

#: Sentence terminators, longest-first so that "?!" and ".\"" are matched
#: before "." and the offset arithmetic stays correct.
TERMINATORS = (".", "!", "?", "…", "。", "！", "？")

#: Tokens ending in "." that are not sentence ends. Keeping this list short is
#: deliberate: a large, clever abbreviation list is itself a source of silent
#: mis-segmentation, and every entry is a place the compiler can go wrong
#: without failing loudly.
ABBREVIATIONS = frozenset(
    {
        # "no" is deliberately absent. It was in an earlier revision as
        # "number", and it split 'He said "no."' into a fragment plus an
        # orphan — the quoted "no" is the commoner case and the numbered
        # reference the rarer one, and a deny-list entry that breaks ordinary
        # prose costs more than the entry earns. Same reasoning removed "ca"
        # ("circa") and "no"; "al" survives only because "al." is vanishingly
        # rare in this project's corpora.
        "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc",
        "e.g", "i.e", "cf", "al", "fig", "vol", "pp", "approx",
        "inc", "ltd", "co", "u.s", "u.k",
    }
)

_TERMINATOR_RE = re.compile("(" + "|".join(re.escape(t) for t in TERMINATORS) + ")")


#: The shortest a claim may be and still be a proposition someone could assert.
#:
#: This is a stated constant, not a tuned parameter. It was not fitted to this
#: corpus to maximise the number of good-looking claims — a fitted threshold
#: encodes this corpus's peculiarities and hands them to the next one.
MIN_CLAIM_CHARS = 25

#: A claim must contain at least one alphabetic character.
#:
#: `except requests.`, `**6.`, and `.` all pass a length test on some corpora
#: and none of them is a proposition in any language this system reads. A
#: segment with no letters is not a weak claim, it is not a claim.
MIN_CLAIM_LETTERS = 1


def is_proposition(text: str) -> bool:
    """True if ``text`` is something a person could assert.

    The fail-closed boundary of ADR-012. A claim that fails this test is
    *discarded*, not emitted with a caveat — because a caveat is a claim, and
    the whole problem is that these segments were being asserted at all.

    The check lives at the compiler's output boundary rather than inside the
    segmenter on purpose. A smarter segmenter that understands code fences and
    Markdown would handle the cases its author thought of; this handles the
    ones nobody did. And because it sits after segmentation, every future
    segmentation strategy inherits the guarantee instead of having to remember
    it.
    """
    stripped = text.strip()
    if len(stripped) < MIN_CLAIM_CHARS:
        return False
    letters = sum(1 for ch in stripped if ch.isalpha())
    return letters >= MIN_CLAIM_LETTERS


@dataclass(frozen=True)
class Segment:
    """One sentence-like unit of a source.

    ``start``/``end`` are offsets into the *original* source text, so
    ``source.content[start:end] == text`` holds by construction.
    """

    text: str
    start: int
    end: int
    ordinal: int

    def __post_init__(self) -> None:
        # Invariant, not validation: if this fails the segmenter is broken and
        # every downstream claim inherits a false provenance claim.
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"segment offsets are not ordered: [{self.start}:{self.end}]")


def _is_abbreviation(text: str, dot_index: int) -> bool:
    """True if the "." at ``dot_index`` terminates a known abbreviation."""
    # Walk back over the token immediately preceding the dot.
    i = dot_index - 1
    while i >= 0 and (text[i].isalnum() or text[i] in "._'"):
        i -= 1
    token = text[i + 1 : dot_index].lower()
    return token in ABBREVIATIONS


def _is_ellipsis(text: str, dot_index: int) -> bool:
    """True if the "." at ``dot_index`` is part of a "..." run.

    Used to *end* a segment rather than to skip past it. A trailing ellipsis is
    the writer signalling an unfinished thought, so "Wait... what happened?"
    is two segments — the fragment and the resumed sentence. Treating it as a
    continuation instead merged them into one span, which is the mirror image
    of the abbreviation bug: there, text was dropped; here, unrelated text was
    glued together and attributed to a single evidence span.
    """
    before = text[dot_index - 1] if dot_index > 0 else ""
    after = text[dot_index + 1] if dot_index + 1 < len(text) else ""
    return before == "." or after == "."


def iter_segments(text: str) -> Iterator[tuple[int, int]]:
    """Yield ``(start, end)`` spans of sentence-like units, without whitespace.

    The span bounds are trimmed to the non-whitespace content, so ``end - start``
    is the length of ``text[start:end]`` and never includes the trailing
    newline. Leading whitespace is excluded too — a claim that begins with a
    space is fine but is a worse identity, since the same sentence indented
    differently would hash differently.

    Two cursors, because they answer different questions. ``cursor`` is the
    start of the chunk being accumulated and must **not** move when a
    terminator turns out to be an abbreviation or an ellipsis dot; ``search``
    is where the next terminator is looked for and does move. Collapsing them
    into one variable silently drops the skipped text: an earlier version
    turned "Dr. Smith arrived." into "Smith arrived.", losing a token of the
    source and therefore misattributing the evidence span.
    """
    cursor = 0
    search = 0
    n = len(text)
    while search < n:
        match = _TERMINATOR_RE.search(text, search)
        if match is None:
            chunk = text[cursor:n]
            stripped = chunk.strip()
            if stripped:
                lead = len(chunk) - len(chunk.lstrip())
                yield (cursor + lead, cursor + lead + len(stripped))
            return

        end = match.end()
        # An abbreviation dot is not a boundary. Advance only the *search*;
        # `cursor` stays put so the text up to the real boundary (abbreviation
        # included) stays in this chunk. An ellipsis, by contrast, does end the
        # segment — see `_is_ellipsis`.
        if match.group() == "." and _is_abbreviation(text, match.start()):
            search = end
            continue

        # Consume any run of terminators and closing quotes/brackets so that
        # '"no."' is one segment and the quote is not orphaned onto the next.
        while end < n and (text[end] in TERMINATORS or text[end] in '"\')]}'):
            end += 1

        chunk = text[cursor:end]
        stripped = chunk.strip()
        if stripped:
            lead = len(chunk) - len(chunk.lstrip())
            yield (cursor + lead, cursor + lead + len(stripped))
        cursor = end
        search = end


def segment(text: str) -> list[Segment]:
    """Segment ``text`` into ordered :class:`Segment` records."""
    out: list[Segment] = []
    for ordinal, (start, end) in enumerate(iter_segments(text)):
        out.append(Segment(text=text[start:end], start=start, end=end, ordinal=ordinal))
    return out
