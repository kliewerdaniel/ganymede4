"""Deterministic normalization for evaluation (ADR-006 §4).

This module is the part of the evaluator that could quietly lie. Everything
else is bookkeeping; this decides whether two sentences are the *same claim*,
and an error here does not produce a crash — it produces a confident, wrong
``CONTRADICTED``, which is the single most damaging output the system can emit.

So the guiding rule is asymmetric: **when normalization is unsure, it fails
toward ``INCONCLUSIVE``.** A missed contradiction is a gap the operator can
close by hand. A manufactured one destroys a true claim, and nothing in the
epistemic spine will bring it back — ``CONTRADICTED`` only reopens to
``UNEXAMINED`` by explicit human action.

Concretely, that means:

- The stemmer strips only suffixes that are unambiguously inflectional. A
  stemmer that over-strips merges distinct claims ("provenance" and "proven")
  and manufactures contradictions out of vocabulary.
- Polarity is a **small closed list**, not a heuristic scan for anything
  word-like. An unrecognized negator means the claim's polarity is *unknown*,
  and an unknown polarity can never produce a contradiction.
- Position matters. "X is not verified" and "not everything is verified" are
  different claims, and a bag-of-terms comparison cannot tell them apart — so
  contradiction requires a term *set* match plus a polarity *flip*, and the
  sequence match for attestation is contiguous, not bag-of-words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..witness.retrieval import FUNCTION_WORDS, _TOKEN_RE

__all__ = [
    "Normalized",
    "normalize",
    "POLARITY_NEGATORS",
    "NEGATION_UNKNOWN",
    "stem",
]


#: Closed list of negators. Anything outside this set leaves polarity unknown.
#: Note what is absent: "almost", "barely", "hardly", "rarely". Those are
#: degree words, not negations, and treating them as negation would invent
#: contradictions. An unknown polarity is the safe answer, so a missing entry
#: costs a missed contradiction and nothing worse.
POLARITY_NEGATORS = frozenset(
    {
        "not", "no", "never", "none", "neither", "nor", "without",
        "cannot", "cant", "wont", "doesnt", "didnt", "isnt", "arent",
        "wasnt", "werent", "hasnt", "havent", "hadnt", "nobody", "nothing",
    }
)

#: Sentinel for a claim whose polarity could not be established. Distinct from
#: both True and False, because "this is not negated" and "I cannot tell
#: whether this is negated" must not collapse into each other.
NEGATION_UNKNOWN = "unknown"


def _undouble(base: str) -> str | None:
    """Undo stress-consonant doubling: "runn" -> "run".

    Returns ``None`` when the final pair is orthographic rather than
    phonological, so the caller leaves the base alone. "ss" and "ll" are the
    cases that matter: "addressed" must reduce to "address", not "addres",
    because a stemmer that disagrees with the uninflected word breaks the very
    agreement the term-set comparison depends on — and the second pass would
    then strip again and produce "addre".
    """
    if len(base) < 2:
        return None
    last = base[-1]
    if last != base[-2] or last in "aeiousl":
        return None
    return base[:-1]


def stem(word: str) -> str:
    """Strip unambiguously inflectional suffixes.

    Conservative by construction. Each rule is a suffix that, removed, leaves a
    token that is still plausibly the same lemma. Where a rule could destroy
    meaning it is not applied:

    - ``-ly`` is stripped (it marks an adverb from an adjective), but only when
      the stem is at least 4 characters, so "only" and "early" survive.
    - ``-ing`` is stripped only when preceded by a doubled consonant ("running"
      -> "run"), because "sing"/"ring" are words.
    - ``-s`` is stripped, but never from ``ss`` ("class" is not "cla").
    """
    if len(word) <= 3:
        return word

    # plural / third person: classes, boxes, states
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if word.endswith("ss"):
        return word
    if word.endswith("s"):
        return word[:-1]

    # adverbial -ly, guarded twice. The stem must survive at 4+ characters, so
    # "early" (5) does not become "ear" (3) — an over-strip that would merge
    # "early" with the actual word "ear" and let the evaluator manufacture a
    # contradiction between two unrelated claims.
    if word.endswith("ly") and len(word) >= 6:
        return word[:-2]

    # gerund with consonant doubling: running -> run
    if word.endswith("ing") and len(word) > 5:
        base = word[:-3]
        undoubled = _undouble(base)
        if undoubled is not None:
            return undoubled
        # Silent-e displacement again, this time for -ing: enforcing leaves
        # "enforc" and stops agreeing with "enforce". Same restoration, same
        # narrow guard, for the same reason.
        if base.endswith(("nc", "rc", "ng", "mg")) and len(base) >= 4:
            return base + "e"
        return base

    # past tense / participle: verified -> verifi -> verify
    if word.endswith("ed") and len(word) > 4:
        base = word[:-2]
        undoubled = _undouble(base)
        if undoubled is not None:
            return undoubled
        # English spells many verbs with a silent final -e that the -ed
        # inflection displaces: verify/verified, apply/applied, imply/implied.
        # Stripping "ed" leaves "verifi", which then disagrees with "verify" and
        # breaks the very agreement the term-set comparison depends on.
        if base.endswith("i") and len(base) >= 3:
            return base[:-1] + "y"
        # Same displacement for -ce/-ge verbs: enforce/enforced leaves
        # "enforc". The -ied rule above is uniform, but -ced is not ("forced"
        # drops its e, "enforced" keeps it), so only the unambiguous silent-e
        # families are restored. "enforced" disagreeing with "enforce" would
        # mean a claim and its own negation never share a term set, and the
        # evaluator would miss the contradiction it exists to find.
        if base.endswith(("nc", "rc", "ng", "mg")) and len(base) >= 4:
            return base + "e"
        return base

    return word


@dataclass(frozen=True)
class Normalized:
    """A claim reduced to what the evaluator is allowed to compare."""

    terms: frozenset[str]
    #: Ordered, for contiguous-run attestation matching.
    sequence: tuple[str, ...]
    #: True, False, or ``NEGATION_UNKNOWN``.
    polarity: bool | str

    @property
    def polarity_known(self) -> bool:
        return self.polarity is not NEGATION_UNKNOWN

    def is_contiguous_in(self, other: "Normalized") -> bool:
        """True if this claim's sequence appears as a contiguous run in ``other``.

        Contiguous, not bag-of-words. "runtime enforces provenance" and
        "provenance is enforced by the runtime" share every content term and
        are not the same assertion in the same order; requiring a contiguous
        run keeps attestation to genuine restatements and paraphrase.
        """
        n, m = len(self.sequence), len(other.sequence)
        if n == 0 or n > m:
            return False
        first = self.sequence[0]
        for i in range(m - n + 1):
            if other.sequence[i] == first and other.sequence[i : i + n] == self.sequence:
                return True
        return False


def normalize(text: str) -> Normalized:
    """Reduce a claim to comparable terms, sequence, and polarity.

    Deterministic and dependency-free: the same string always yields the same
    ``Normalized``, which is what lets the evaluator's verdicts be replayed.
    """
    raw = _TOKEN_RE.findall(text.lower())
    terms: list[str] = []
    negators: list[str] = []
    for tok in raw:
        if tok in POLARITY_NEGATORS:
            negators.append(tok)
            continue
        if tok in FUNCTION_WORDS:
            continue
        terms.append(stem(tok))

    if not negators:
        polarity: bool | str = False
    elif len(set(negators)) == 1:
        # Two different negators ("not ... without") is a scope we do not model,
        # so polarity is unknown rather than guessed either way.
        polarity = True
    else:
        polarity = NEGATION_UNKNOWN

    return Normalized(
        terms=frozenset(terms),
        sequence=tuple(terms),
        polarity=polarity,
    )
