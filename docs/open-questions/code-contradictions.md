# Open: what is a contradiction in a corpus that contains source code?

**Status:** Answered as far as this corpus permits — see ADR-030, ADR-031, ADR-032
**See also:** ADR-030, ADR-031, ADR-032, ADR-027 (PROPOSED), `docs/labelling/CONTRADICTIONS.md`
**Date:** 2026-09-29 (closed 2026-10-01)
**Related:** ADR-006, ADR-012, ADR-020

> **Closed 2026-10-01.** The 70 distinct pairs are labelled
> (`docs/labelling/agent-labels.json`): 87.7% of contradiction pair rows are
> code fragments. ADR-030 selected `no-code`. ADR-031 then built the
> structural detector and found no setting that is both broad and safe, so the
> policy is **not** implemented. What remains open is narrower and is stated
> at the end of this file. The historical measurement below is preserved as
> recorded.
>
> **Reopened and re-answered by ADR-032.** The re-read of the 14 labelled
> `real_contradiction` pairs found 12 of 14 span two unrelated documents —
> matched only on a shared bag of words ("you", "point", "pattern"). 93.8% of
> all 2,854 pair rows are cross-document. So the question was one layer too low:
> most of this population was never a contradiction. **Scope**, not layout, is
> the real filter, and ADR-032 implements it. `CONTRADICTED` subjects go
> 402 -> 156, no labelled real contradiction is lost, and **78% of what
> survives is still code fragments** — the residue `no-code` was invented for.
> The genuinely open question is now smaller: a same-document code fragment
> like `return ''` versus `return None` is a real scope-legal contradiction that
> only a parser could distinguish from a semantic one.

## The question

ADR-020 removed the spurious contradictions caused by empty term sets. The
remaining contradictions are a different problem, and this file records the
measurement rather than a decision, because the honest answer is not yet clear
and picking a threshold would be the fitted-threshold failure ADR-012 warns
about.

Classified from the in-flight full-corpus run (288 contradictions at 46%
complete, pre-ADR-020 code):

| class | count | share |
|---|---|---|
| code / indent structure | 130 | 45% |
| empty term set (ADR-020, now fixed) | 103 | 36% |
| prose | 55 | 19% |
| code keywords | 2 | 1% |

So after ADR-020, roughly **three-quarters of what remains is not a semantic
contradiction at all.**

## What the "prose" 19% actually contains

Not all of the prose class is noise. Sampling it:

**Genuine, and the component working exactly as designed:**

```
A: 'It is also a pyramid scheme.'
B: 'THIS IS NOT A PYRAMID SCHEME!'

A: '- Include bullet points or numbered lists where appropriate for readability.'
B: '- Do not Include bullet points or numbered lists where appropriate for readability.'

A: 'can you run openai or anthropic locally'
B: 'OpenAI and Anthropic you can not run locally.'
```

The evaluator independently discovered that the corpus argues both sides of
the pyramid-scheme question, and both sides of a prompt-engineering
recommendation. Nobody told it to look. This is the entire reason the
component exists, and it is worth stating plainly before discussing the noise:
**the thing works.**

**Not contradictions:**

```
A: 'com/blog/2026-02-15-building-this-blog'
B: 'com/blog/2026-02-15-building-this-blog\nNo.'          <- substring, plus "No"

A: "TypeError: string indices must be integers, not 'str'"
B: 'TypeError: string indices must be integers'           <- one is an excerpt of the other
```

These are *subset* relationships. One text contains the other plus extra
material. A term-set match plus a polarity flip fires because the excerpt
inherits the whole's polarity, but nothing in the pair asserts opposing
propositions.

**Code fragments, the largest class:**

```
A: 'error(f"JSON decoding failed: {e}")\n       return None\n   except Exception as e:\n ...'
B: 'error(f"JSON decoding failed: {e}")\n        return ''\n    except Exception as e:\n ...'
```

Same term set, different polarity word, so a real verdict by the letter of the
specification. But two versions of a function are not a contradiction; they
are two drafts of the same code, and `CONTRADICTED` — the absorbing state —
is close to the worst possible label for them.

## Why this is not being fixed in the same breath as ADR-020

Three reasons, and the third is the important one.

**ADR-012 already taught this lesson.** `MIN_CLAIM_CHARS = 25` exists with a
comment saying a threshold "that encodes this corpus's peculiarities and hands
them to the next one" is a defect. Any rule I write here — "ignore claims
containing newlines", "require ≥60% alphabetic terms" — is that rule again,
fitted to a corpus that is 44% ChatGPT output pasted from a coding assistant.
The next corpus will be prose and the rule will be wrong in the other
direction.

**`CONTRADICTED` is absorbing, so a wrong one is expensive.** Nothing in the
spine brings a contradicted claim back without explicit human action. A
filter that is too permissive destroys true claims; one that is too
restrictive only costs a missed finding. The asymmetry argues for care in
*both* directions, which argues for a decision rather than a patch.

**The honest answer may be that contradiction detection does not apply to
this corpus.** The corpus is a personal record with source code embedded in
it. Code is not a proposition, and the segmenter already has a
`MIN_CLAIM_LETTERS` notion of "contains something assertable" — it simply has
no notion of "this segment is not code." A principled fix might be a
proposition *classifier* rather than a contradiction filter: decide at
segmentation time whether a span is prose or an artifact of some other kind,
and let the evaluator decline non-prose on principle rather than by
heuristic.

That is a larger design question than a guard, it changes segmentation and
therefore every claim id and Merkle root in the corpus, and it deserves its
own ADR with its own evidence.

## What would settle it

Not a threshold. Two things:

1. **A labelled sample.** Take the 288 contradictions, hand-classify them, and
   measure precision per class. The counts above are my classification by
   heuristic, not a ground truth, and the ADR should not be written on my
   reading of a sample.

2. **A decision on non-prose segments.** Either the compiler stops emitting
   code as claims, or the evaluator is given an explicit, disclosed notion of
   "this segment is not a proposition" that it can refuse on. Both are real
   design choices with artifact-identity consequences, and both change what the
   corpus *is* — which is why this is deferred rather than decided in a
   commit message.

Until then the state is recorded, not fixed. The contradictions above are
live in the current artifact, which is the honest position: the evaluator
reports what its specification tells it to report, the specification has a
gap, and the gap is now written down instead of discovered later by someone
who assumes the numbers mean something they do not.

## What is still open (2026-10-01)

The question is answered for this corpus and the answer is unsatisfying:
**the information needed to separate code from prose is not in the
artifact.** There are zero fenced code blocks and prose-only URIs, so neither
Markdown structure nor filename helps. A real contradiction (`has` versus
`does NOT have`) lives inside a string literal in an indented block, and
indentation cannot distinguish them.

Closing this properly needs a parser, or per-source-language metadata
recorded at compile time. Not a threshold — ADR-031 measured the frontier and
there is no safe point on it.

The 14 labelled real contradiction pairs are agent-labelled rather than
human-labelled, and a human re-read of those 14 is still worth having.
