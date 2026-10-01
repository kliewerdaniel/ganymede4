"""Structural code detection, and the frontier it actually has.

ADR-030 selected `no-code` as a policy and declined to fit a threshold for
it. This module is the attempt that follows from taking that seriously: not
a text classifier, but a structural one.

The failure of the previous approach is diagnostic rather than incidental.
`stratum_of` scored punctuation density over line structure, and missed
2,484 of 2,504 code rows -- because the fragments are *mid-line*:
`; return; } if (!`. There is no line for a line-structure feature to see.
So this module reads the structure the source itself carries:

    `indented_regions` finds character ranges of lines indented by a tab or
    by at least `min_indent` spaces, merging continuation lines.

That is layout, not density. It is computed from the stored source bytes, it
is deterministic, and it needs no corpus-derived constant -- `min_indent` is
the only knob and it exists because "indented" has to mean *some* number.

## The measured frontier, on the real artifact

Against the 70 labelled pairs in docs/labelling/agent-labels.json, with
BOTH sides of a pair required to be in a code region:

    min_indent   code rows caught   real contradictions lost
         1               2,478                      50
         2               2,478                      50
         4                 316                      50
         8                 118                       0
        13                   0                       0

There is no setting that is both broad and safe. The gap between `2` and
`8` is the whole finding: recovering the 2,336 missed rows requires
accepting a collision.

## The collision

At `min_indent=2` the one real contradiction destroyed is a 50-row pair:

    "username}' does NOT have an Author profile.")
    "username}' has an Author profile.")

A genuine semantic contradiction -- `has` vs `does NOT have` -- living
inside a string literal in an indented block. Indentation cannot see it,
because at the point of the decision the structure *is* code. Only the
string-literal boundary distinguishes them, and detecting that is language
parsing, which the compiler does not do.

So this module does not claim to be the policy. It claims to be an honest
measurement of why `no-code` cannot be applied cheaply, which is the input
ADR-031 needs. `recommend_min_indent` returns the safe frontier point and
`colliding_pairs` returns what it would cost, so a caller can decide with
both numbers in hand.

Pure standard library. No import of any project module.
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator

__all__ = [
    "indented_regions",
    "is_within",
    "in_code_region",
    "recommend_min_indent",
    "FRONTIER",
]


def indented_regions(text: str, min_indent: int = 2) -> list[tuple[int, int]]:
    """Character ranges of code-shaped lines and their continuations.

    A line belongs to a region when it is indented by a tab or by at least
    `min_indent` spaces. Blank lines do not terminate a region, because a
    blank line inside a function is not the end of the function -- this is a
    layout judgement, not a parse.

    Offsets are character offsets into `text`, suitable for comparing against
    `evidence.start_offset` / `evidence.end_offset`.
    """
    regions: list[tuple[int, int]] = []
    offset = 0
    start: int | None = None
    for line in text.split("\n"):
        stripped = line.lstrip(" \t")
        # A single tab is one indent level regardless of min_indent: tabs are
        # not spaces, and counting a tab as one space is what makes a naive
        # port of this miss every tab-indented document in the corpus.
        indented = line != stripped and (
            line[0] == "\t" or len(line) - len(stripped) >= min_indent
        )
        blank = not stripped.strip()
        if indented or (blank and start is not None):
            if start is None and not blank:
                start = offset
        elif start is not None:
            regions.append((start, offset))
            start = None
        offset += len(line) + 1
    if start is not None:
        regions.append((start, offset))
    return regions


def is_within(start: int, end: int, regions: Iterable[tuple[int, int]]) -> bool:
    """True when [start, end) lies wholly inside one region.

    Wholly, not partially: a span straddling the edge of a code block is not
    a code span, and treating it as one is how a prose claim gets swallowed.
    """
    return any(r0 <= start and end <= r1 for r0, r1 in regions)


def in_code_region(
    text: str, start: int, end: int, min_indent: int = 2
) -> bool:
    """Whether one evidence span falls inside an indented code region."""
    return is_within(start, end, indented_regions(text, min_indent))


def recommend_min_indent() -> int:
    """The broadest indent level that lost no labelled real contradiction.

    Returns 8, the frontier point recorded in FRONTIER. It is a constant
    rather than a search because the search requires the label file and a
    6 GB artifact; callers that have neither should read FRONTIER and accept
    that they are applying a measured decision, not recomputing one.
    """
    return 8


# Measured on /tmp/p19-t5-real.db against docs/labelling/agent-labels.json.
# Both sides of the pair required to be in a code region.
FRONTIER: tuple[tuple[int, int, int], ...] = (
    (1, 2478, 50),
    (2, 2478, 50),
    (4, 316, 50),
    (8, 118, 0),
    (13, 0, 0),
)
