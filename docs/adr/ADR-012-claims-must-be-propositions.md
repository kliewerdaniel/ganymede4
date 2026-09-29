# ADR-012: A claim must be a proposition, and most of them are not

Status: Ratified (2026-09-28)
Depends on: ADR-004, ADR-010, ADR-011

## Context

ADR-011 made the evaluator fast enough to run over the whole corpus: 51
evaluations per second, 422,753 claims, 135 minutes. Running it produced
**231 contradictions and 5,858 attestations**, and the first thing worth doing
with those numbers was reading them.

They are largely garbage. Sample contradictions found in the real corpus:

```
CLAIM : **6.
CLAIM : .
CLAIM : 10.
CLAIM : return None
```

The corpus is largely ChatGPT conversations containing pasted Python, shell
sessions, and stack traces. The v1 segmenter splits on sentence punctuation
without regard to what a sentence is, so `except requests.` and `**6.` become
claims. They are content-addressed, they have exact source spans, they carry
provenance, and they are **not propositions**. They then contradict each other,
because `.` contradicts `.` under any polarity heuristic that treats the token
as meaningful.

Measured on the real corpus:

| measure | count | share of 422,753 |
|---|---|---|
| claims under 25 characters | 94,359 | 22.3% |
| claims under 10 characters | 45,753 | 10.8% |
| claims containing no letters at all | 24,981 | 5.9% |

This is the most consequential finding in the project so far, and it is not a
performance problem. It is a **correctness** problem, and it is invisible to
every guarantee the system makes. The provenance is perfect. The spans resolve.
The Merkle root is stable. The claims are still junk, and the evaluator is
faithfully reporting that `.` contradicts `.`.

The reason this went unnoticed for seven phases is worth recording: every test
used hand-written prose fixtures, and prose segments cleanly. The defect only
appeared when the corpus was 44% machine output containing source code.

## Decision

1. **A claim must contain at least one letter and be at least
   `MIN_CLAIM_CHARS` long.** A segment with no alphabetic content is not a
   proposition in any language this system reads, and neither is a bare
   fragment. Both are *discarded*, not emitted with a low confidence.

2. **Discarded segments are counted and reported, never silently dropped.**
   The compiler reports `dropped_segments` and the reason per class. A
   pipeline that quietly discards 22% of its input while reporting a clean
   compile is lying by omission, and the count is the only way an operator can
   notice.

3. **The threshold is a stated constant, not a tuned parameter.** It is not
   fitted to this corpus to maximise the number of good claims. A threshold
   that was tuned would be a threshold that encodes this corpus's
   peculiarities, and the next corpus would inherit them.

4. **The provenance of a dropped segment is still resolvable.** Dropping a
   claim does not drop the source: the bytes remain in the source, still
   covered by the Merkle root, still auditable. What changes is that the
   artifact no longer *asserts* a proposition from them. The witness can still
   be asked what is at those offsets.

5. **Segmentation is not fixed in this ADR.** The deeper problem is that the
   segmenter has no notion of code blocks, Markdown fences, or quoted text,
   and will keep producing fragments from them. The character-class guard is
   the fail-closed response to that: it refuses to assert rather than guessing
   a better boundary. A fence-aware segmenter is a later change and must earn
   its own ADR with its own measurements.

## Why not just make the segmenter smarter

The tempting response is to teach the segmenter about ``` fences and Python, and
that is the right long-term direction. It is the wrong response *now*, for two
reasons.

First, it is a completeness argument in disguise. A smarter segmenter handles
the cases someone thought of; the character-class guard handles the ones nobody
did. A system that fails closed on unrecognised structure is different from one
that confidently asserts on it.

Second, and more importantly: **the guard is a statement about what a claim
is.** Whatever the segmenter becomes, the invariant "a claim contains a
proposition a person could assert" has to hold. Encoding that as a filter at
the compiler's output boundary means every future segmentation strategy inherits
the guarantee, and a segmenter that forgets it is caught immediately rather than
after a corpus has been built on it.

## Consequences

- The real corpus will compile to materially fewer claims. That is correct: a
  large fraction of the current 422,753 are not claims.
- Contradiction and attestation counts will change, and should be read as
  *more* trustworthy for being smaller. A contradiction between two code
  fragments is noise; the interesting ones are between two things someone
  actually said.
- The independent auditor gains a check: a claim that cannot be a proposition is
  a malformed record, and the auditor should say so rather than passing it.
- The witness's `UNRESOLVED` behaviour is unaffected — discarding claims makes
  absence *more* likely to be correct, and the investigation record still
  documents the search.

## Boundaries

- This is a filter, not a language model. It does not judge whether a
  proposition is *true*, only whether it is a proposition at all.
- It does not attempt to reconstruct the code blocks it discards. The bytes
  are in the source; the artifact simply declines to make claims about them.
