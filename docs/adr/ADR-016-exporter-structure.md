# ADR-016 — Exporter structure is not the author

Status: Ratified (2026-09-29)
Depends on: ADR-004, ADR-012, ADR-015

## Context

ADR-015 fenced control characters: bytes that no tool should have emitted. This
fences the other category of non-author text, and the real corpus contains far
more of it.

A Reddit export is markdown. The export tool writes `## Parent Comment` above
each quoted comment, `---` between blocks, and ```` ``` ```` around code. None of
that is a sentence terminator. The segmenter only knows where a terminator is,
so a block whose text carried no `.` at all came out as **one** claim:

```
'---\n\n## Parent Comment\n\nDM\n\n---\n\n## Parent Comment\n\nWhy?'
```

One claim, two comments by two different people, attributed to a single
evidence span. The visible symptom:

```
Q: 'parent comment'   ->  '---\n\n## Parent Comment\n\nDM\n\n---\n\n## Parent Comment\n\nWhy?'
```

The witness's top-ranked answer to "parent comment" was the literal string
`## Parent Comment`.

Scale: 9,096 claims contain a `## ` heading somewhere other than position 0, and
2,155 contain a mid-text `---`. Claim lengths were otherwise healthy — 187,188
under 100 characters, only 347 over 2,000 — so this is a localized fault, not a
general segmentation failure.

## Decision

**Block-level markdown is a segment boundary. Only block level.**

Three structures, all anchored to the start of a line with `re.MULTILINE`:

- **ATX headings** (`#`…`######`) — the hashes are dropped, the heading's text
  survives as its own segment.
- **Thematic breaks** (`---`, `***`, `___`, three or more) — dropped whole.
- **Code fences** (```` ``` ````, `~~~`) — dropped whole.

Two constraints shaped this more than the list did.

**The authored text must survive.** `## Why were libraries burnt?` is the
author's question wearing a label. Dropping the line would discard real
content; the label is the only part that isn't content. So the match consumes
the `#`s and nothing more, and the heading text becomes its own verbatim segment
at its own exact offsets. Symmetrically, a heading with no terminator still
ends at its newline — markdown defines an ATX heading as exactly one line, and
failing to split there would re-create the merge bug with a heading attached.

**Inline markup must be untouched.** A `#` in `C#`, a `-` as a minus sign, a `*`
in `3 * 4`, a hyphen in a line-broken word — all ordinary prose. Any rule that
fired mid-paragraph would destroy sentences, so the match is line-anchored and
thematic breaks require three or more of the same character. A single `-` is a
list bullet; treating it as a rule would split every bulleted list in the
corpus mid-item.

Like ADR-015, all boundaries live in one alternation rather than a separate
scan: merging sorted offset streams is where offset bugs live.

## Consequences

- Claims spanning two authored units become claims of one.
- Measured on the real corpus, against the pre-ADR-016 build:

  ```
  mid-claim '## ' :  9,096 -> 1,021
  '---' rules     :  2,155 ->    53
  code fences     :  5,766 ->    868
  claims          : 324,982 -> 336,190
  root            : 219a5b92284b3ae5 -> 177a5ee5afaf63e8
  ```

- The residue is not missed boundaries. Classified: of the 1,021, 884 are
  mid-line (`f"## Comment"` inside a Python string), 39 are escaped (`\#` —
  a character the author typed), and 98 are a `## ` at a line start with no
  preceding terminator. Verified directly against a source: a real line-start
  ``` fence is detected, excluded from the segment, the body beneath it is
  captured verbatim, and zero segments open with a line-start fence.
- Non-verbatim offsets across all 434,091 segments of the real corpus: 0.
- Independent auditor: clean, exit 0.
- Full-corpus segmentation: 3.8s, faster than the 8.7s baseline before this
  change. Compile: 135.1s, down from 647.3s with the first (quadratic) version.
- 417 tests passing, stdlib-only, offline.
- Segmentation on clean input is unchanged, verified by stashing the change and
  diffing.

## An implementation note worth keeping

This was first written as one regex alternation joined to `_BOUNDARY_RE`, on the
reasoning that a single sorted stream of boundaries is where offset bugs are
least likely. That reasoning was right about the *cursor* and wrong about the
*pattern*. Four successive versions each failed invisibly:

1. A zero-width lookahead branch — the scan resumed at the same offset forever.
2. An optional capture group that matched empty, so `group("hashes")` was `None`
   for a heading and the cursor ran the heading's text into the body below it.
3. An alternation whose empty-match branch was tried first, so fences never
   matched and headings emitted a bare `'##'`.
4. A thematic-break test reading "rest of the text" instead of "rest of the
   line", which called a perfectly ordinary `---` at offset 0 prose.

None is visible by reading the pattern; each only shows up as text that merges
or disappears. The final form is `_block_boundary_at`, three explicit cases in
Python, with the two scans merged inside `iter_segments` so the cursor still
advances in exactly one place. That is shorter than the commentary needed to
explain why the cleverer pattern was wrong.

Profiling the first working version then found a 13× slowdown: the block scan
rescanned forward from every terminator, 1.47M `_block_boundary_at` calls over
the corpus. Carrying the pending boundary forward — the same reason the
terminator scan keeps its own `search` cursor — made it a single pass.

## Alternatives rejected

- **Strip markdown before segmenting.** Rejected for the ADR-015 reason,
  sharpened: in a content-addressed store the source *is* the identity, and a
  pre-stripped pipeline would make the offsets provable only against a
  transformed string that no longer exists in the store. The auditor would
  have nothing real to check.
- **Treat `## ` as a terminator wherever it appears.** Fires inside
  `C#`/`F#`, and would truncate a segment mid-word while claiming exact
  offsets for the remainder.
- **Drop anything containing a heading.** Discards the author's sentence
  because the exporter wrapped it in a label. The defect was never that the
  heading existed; it was that the heading merged two people's words.
- **Fix this in the witness or retriever.** The claim already existed, already
  had a content id, and was already answerable. Downstream filtering means the
  artifact contains a thing the system has decided is not a thing.

## Known limitation

A code fence's language tag is dropped with the fence line, since the match
consumes to end-of-line. Recovering it would mean matching and re-emitting the
tag, which complicates the boundary stream to preserve a token that is metadata
about code rather than a claim about the world. Recorded rather than fixed.
