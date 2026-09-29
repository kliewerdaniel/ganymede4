# ADR-020 — Two claims that share nothing are not a contradiction

**Status:** Ratified
**Date:** 2026-09-29
**Depends on:** ADR-002, ADR-006, ADR-012, ADR-019

## Context

ADR-019 fixed duplicate attestation and, in the course of a full-corpus
evaluation, surfaced a second and independent defect in the same module. This
one is narrower, easier to fix, and more dangerous in kind, because
`CONTRADICTED` is the most damaging verdict the system can emit.

The finding, from the real 336,190-claim corpus:

```
'Я - гангстер, я - настоящий босс, ...'   contradicted by
'But that's not what this was.'
```

A line of Russian rap lyrics is recorded as **contradicted** by an English
function-word sentence. The two have no terms in common, no shared language,
and no shared meaning. They collide because of how the evaluator decides that
two claims say the same thing.

## The defect

`_contradictions` requires two conditions: an exact normalized *term set*
match, and a polarity flip.

```python
if norm.terms == subject.terms and norm.polarity != subject.polarity:
```

An empty term set is a perfectly valid term set, and `frozenset() ==
frozenset()` is `True`. So any two claims that normalize to *nothing* and have
opposite polarity are, to this predicate, the same proposition with opposite
meaning.

That is not a rare edge. Two independent conditions make it common:

**`normalize()` drops every non-ASCII token.** `_TOKEN_RE` is `[a-z0-9]+`, so
a Cyrillic, Greek, CJK, Hebrew, or Devanagari sentence tokenizes to the empty
string. Measured across the real corpus:

| script | claims | empty term set |
|---|---|---|
| Latin/other | 335,914 | 2 |
| Cyrillic | 221 | **186** |
| Greek | 47 | 0 |
| CJK | 8 | 6 |

186 of 221 Cyrillic claims are invisible to the evaluator. Not mis-evaluated —
*invisible*, indistinguishable from one another and from an empty claim.

**Boilerplate survives segmentation.** `is_proposition()` requires
`MIN_CLAIM_CHARS = 25` and `MIN_CLAIM_LETTERS = 1`. A 33-character sentence of
nothing but English function words clears both: `Looking forward to the
conversation!` and `But that's not what this was.` are both accepted as
propositions, and both normalize to an empty term set.

The collision, in full:

```
EMPTY term set, polarity=True  : 1
EMPTY term set, polarity=False : 193
```

**One claim — `But that's not what this was.` — is marked `CONTRADICTED` by all
193 others**, which are 193 mutually unrelated sentences spanning Russian
lyrics, sign-offs, and fragments of any language the tokenizer cannot read.

## Why the module's own design did not prevent this

ADR-006 states the governing asymmetry:

> when normalization is unsure, it fails toward `INCONCLUSIVE`. A missed
> contradiction is a gap the operator can close by hand. A manufactured one
> destroys a true claim, and nothing in the epistemic spine will bring it
> back.

`normalize.py` implements that asymmetry carefully everywhere it applies to
*ambiguity*: an unrecognized negator yields `NEGATION_UNKNOWN` rather than a
guess, and `is_contiguous_in` requires contiguity rather than bag-of-words.
The stemmer's docstrings are unusually honest about what a wrong decision
costs.

But "unsure" was never defined to include "has nothing to compare." A claim
with an empty term set is not an *uncertain* normalization of a proposition; it
is the absence of a proposition as far as this normalizer is concerned, and the
code has no representation for that. The result is that the total function
`normalize()` returns a valid-looking `Normalized` whose `terms` is empty, and
every downstream equality test treats that emptiness as a confident answer.

The failure is the same shape as ADR-014's `substr()` and ADR-018's digest
check: a check that cannot distinguish *"I found these to be equal"* from
*"I have nothing to compare, so everything is equally not-not-equal."* An empty
set is the identity element for equality, which makes it the one value a
set-equality predicate is guaranteed to get wrong.

## Decision

**An empty term set cannot establish a contradiction. Ever.**

```python
if not subject.terms:
    return []
```

in `_contradictions`, plus the same guard on the peer. This is the minimal
change and it is sufficient: a contradiction requires a shared term set, and
with no terms there is nothing shared, so the verdict is `INCONCLUSIVE` — the
documented safe direction. The claims are not deleted, not downgraded, and not
re-processed; they simply stop manufacturing verdicts about each other.

Three properties, each deliberate:

**It fails toward `INCONCLUSIVE`, as ADR-006 requires.** The 186 Cyrillic
claims and the function-word boilerplate become `INCONCLUSIVE` with their
neighbourhood recorded. That is the honest reading: material was found, it
establishes nothing.

**It does not paper over the tokenizer.** Making the empty set non-comparable
stops the *false contradiction*; it does not make Cyrillic evaluable, and it
should not pretend to. `_TOKEN_RE` is ASCII-only by design — the stemmer is
English-only, and silently applying an English stemmer to Russian would
manufacture *worse* errors than admitting ignorance. Non-Latin claims are
correctly stored, correctly provenanced, and correctly marked as not
evaluable by this method. Making them evaluable is a separate decision with a
separate ADR and a real tokenizer requirement; this one refuses to guess.

**The same guard is not added to attestation.** ADR-019 already handles it:
attestation requires `peer.sequence != subject.sequence`, and two empty
sequences are equal, so empty-term claims already cannot attest each other.
That fix was made for a different reason and covers this case as a consequence
— recorded here so the reasoning is not rediscovered as a bug later.

### A second, smaller decision

`is_proposition()` accepts a 33-character sentence of pure function words. The
real defect there is that "is a person asserting something" was decided by
length and a letter count, neither of which asks whether any *content* word is
present. But changing the segmentation threshold is exactly the fitted-
threshold failure `MIN_CLAIM_CHARS`'s own comment warns against ("a threshold
that encodes this corpus's peculiarities and hands them to the next one"), and
it would change every claim count and Merkle root in the corpus. **Not done
here.** The contradiction guard already prevents the harm, and the threshold
question is recorded as open rather than answered by picking a number.

## Consequences

**Contradiction counts drop.** The 211 contradictions observed at 34% of the
run are dominated by two causes — this empty-set collision and code fragments
(`return None` vs `return analyzed_data`, which are genuine term-set matches
with a polarity flip but not semantic contradictions). The first is fixed here.
The second is a deeper question about what "contradiction" means for a corpus
containing source code, and is **not** addressed by this ADR; `CONTRADICTED` is
effectively absorbing, so a wrong one is expensive.

**A limitation is now explicit rather than latent.** The evaluator reports
`INCONCLUSIVE` for every claim it cannot compare, and after this ADR that
includes all non-Latin-script claims. An operator reading a fully-evaluated
artifact will see 186 Cyrillic claims that appear to have been assessed and
were not. That is a real gap in coverage, made visible instead of hidden.

**The generalizable lesson, recorded because it has now recurred three times**
(ADR-014 `substr()`, ADR-018 the digest, and here): *a check needs a way to
say "I cannot tell this apart from that," distinct from "I can tell, and they
match."* Absent that state, an identity element — `""` for a string, `0` for a
count, `frozenset()` for a set — silently becomes a confident match.
