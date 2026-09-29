# ADR-015 — A control character is a boundary, not content

Status: Ratified (2026-09-29)
Depends on: ADR-004, ADR-012, ADR-014

## Context

ADR-014 found that the auditor's span check had been reading source text
through SQLite's `substr()`, which is NUL-terminated. One ChatGPT export
contains a literal NUL, so 1,434 spans read as empty and the audit reported
1,434 defects on an artifact that had none.

The fix there was to compare in Python. That is correct and it is also a
patch to a symptom. The underlying fact is that **the corpus contains a NUL
byte**, and nothing in the pipeline objected. Every future tool that touches
this corpus through a C-string path will rediscover it independently.

So: what is the byte, and what should the system do about it?

## What the byte actually is

One source of 18,930. One NUL, at character 242,506 of 415,353. In context:

```
... (4.64)\n\n (a) Successor Features (b) Generalized Policy Improvement
⇡ =E⇡ ⇥\x001 +\x00\x002 +\x002\x003 +...
```

`⇡` is the Unicode private-use glyph a PDF extractor emits for ψ. The text is
a paper on successor features, pasted into a conversation. The NUL is where
the PDF's font encoding lost a glyph index — an artifact of how the document
was *produced*, not something the author wrote.

It produced three claims, all of them fragments of a figure:

```
'64)\n\n (a) Successor Features (b) Generalized Policy Improvement ⇡ =E⇡ ⇥\x001 +...'
'⇡i\nNew task: Get milk\n ⇡fridge ⇡fridge\nQwmilk \x00=\n wmilk\n.'
'Max\n⇡drawer\n\x00 = ⇡drawer wmilk Qwmilk\nt\n⇡milk\n  ⇡1\nState features...'
```

These are exactly the fragments ADR-012 was built to stop. They survived it
because the test is *length and letters*: these fragments are long and
alphabetically rich. Being garbage is not the same as being short.

## Decision

**A C0/C1 control character is a hard segment boundary. A segment containing
one is never a proposition.**

- `CONTROL_BOUNDARIES` = all of C0 except `\t\n\r`, plus DEL, C1 NEL, and the
  C1 APC-string terminator.
- Tab, newline, and carriage return are excluded: they are text layout, and
  the corpus is full of them. Treating a newline as corruption would shred
  every multi-line document.
- Controls are folded into the *existing* boundary regex rather than scanned
  in a second pass. Two sorted boundary streams have to be merged, and merging
  offset streams is where offset bugs live. One alternation keeps all the
  arithmetic in one place.
- A control ends the segment and is excluded from it, so the byte cannot land
  inside a claim's text even before the proposition check runs.

## The two wrong answers

**Reject the source at load.** One bad byte at 58% through a 415,353-character
document would discard 172,847 characters of real record — including a
complete RL paper the author read. A defect in transcription is not a reason
to delete the transcription, and "fail closed" must not mean "throw away
everything downstream of one broken byte."

**Strip or replace the byte at load.** This is worse, because it looks
harmless. In a content-addressed store, source content *is* the identity.
Editing it after the fact invalidates every content id derived from it, and
the edit is invisible — the store would report a clean audit of source bytes
that no longer exist. ADR-014 rejected this by name for the same reason.

The fence preserves what each layer is actually for. The store keeps the bytes
exactly as ingested and remains content-addressed. The segmenter refuses to
build claims across a byte that is a production artifact. Neither layer has to
know about the other's concern.

## Consequences

- The three figure-fragment claims are gone. They were noise; the corpus loses
  nothing.
- The 172,847 characters after the NUL compile normally. Verified: text after
  a single control byte still produces claims.
- Segmentation output is byte-identical on clean input. Verified by stashing
  the change and diffing. Without that check, a boundary added to a segmenter
  is indistinguishable from a silent offset shift across the whole corpus.
- ADR-014's auditor fix stays. The auditor is right to compare in Python
  regardless of what the segmenter does, because the auditor must be able to
  check artifacts compiled by *older* versions of the segmenter, and because
  an auditor that depends on an upstream invariant is not independent.

## Alternatives rejected

- **Add a length/letter threshold tuned to also exclude these fragments.** The
  fragments are 100+ characters of letters. There is no threshold; there is a
  category of non-content, and character-class is the honest test for it.
- **Filter control characters in the witness or the evaluator.** Too late. The
  claim would already exist, already have a content id, and already be
  answerable. Downstream filtering means the artifact contains a thing the
  system has decided is not a thing.
- **Treat every non-printable as a source-level error and fail the whole
  compile.** Fail-closed at the *source* level is right when the source is
  untrustworthy in a way that makes its claims unsafe — which is a different
  decision, about a different property, and not one this byte earns.
