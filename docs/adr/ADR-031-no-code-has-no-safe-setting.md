# ADR-031: `no-code` has no safe setting

Status: Ratified
Date: 2026-10-01
Depends on: ADR-027, ADR-030

## Context

ADR-030 selected `no-code` as a policy and declined to fit a threshold for
it, on the grounds that the existing classifier measures the wrong feature.
It explicitly left implementation open: "the compiler is not changed."

This records the attempt to implement it structurally, and the reason it
does not ship.

## The approach: layout, not density

`stratum_of` scores punctuation density over line structure and misses 2,484
of 2,504 code rows, because the fragments are mid-line — `; return; } if (!`
— and there is no line for a line-structure feature to see.

So the new detector reads the structure the source itself carries.
`ganymede4.compile.coderegion.indented_regions` returns the character ranges
of tab- or space-indented lines. That is layout computed from stored source
bytes: deterministic, standard-library, no corpus-derived constant.

## The measured frontier

Against the 70 labelled pairs, both sides of a pair required to be in a code
region:

| `min_indent` | code rows caught | real contradictions lost |
|---:|---:|---:|
| 1 | 2,478 | 50 |
| 2 | 2,478 | 50 |
| 4 | 316 | 50 |
| 8 | 118 | 0 |
| 13 | 0 | 0 |

**There is no setting that is both broad and safe.** The gap between 2 and 8
is the entire finding: recovering the 2,360 missed rows requires accepting a
collision.

## The collision

At `min_indent=2` exactly one real contradiction is destroyed, worth 50 pair
rows:

```
"username}' does NOT have an Author profile.")
"username}' has an Author profile.")
```

A genuine semantic contradiction — `has` versus `does NOT have` — living
inside a string literal in an indented block.

Indentation cannot see it, because at the point of the decision the
structure *is* code. Only the string-literal boundary separates them, and
detecting that is language parsing, which this compiler does not do.

## Decision

**Do not implement `no-code` as an automatic policy.** Ship
`indented_regions` as a measurement primitive, and record the frontier.

`recommend_min_indent()` returns 8 — the first setting that loses nothing —
and it catches 118 of 2,504 code rows. Applying it removes 4.7% of the code
problem and 0% of the contradiction problem. That is not worth a policy, a
behavior change, and a new class of silently-refused verdicts.

## Why this is not threshold-fitting

The tempting move is to take `min_indent=2`, accept the 50 rows, and note
that it is 5.9% of the population. That is exactly the move this project
declines: `CONTRADICTED` is absorbing, so those 50 are unrecoverable, and
the ratio 2,478:50 is itself a property of one corpus. ADR-030's asymmetry
argument still holds — it just does not resolve here, because the detector
is not good enough to apply.

## What would change this

A detector that separates string literals from code — i.e. a real parser, or
per-source-language metadata recorded at compile time. Not a threshold.

## Correction: the corpus does have fences

An earlier draft of this ADR claimed "the corpus has zero fenced code
blocks, so neither Markdown structure nor filename is available as a
signal." **That was wrong**, and a review of the repo caught it. Measured on
the real artifact:

| | count |
|---|---:|
| sources containing at least one fence marker | 1,380 of 18,930 |
| fence markers in stored source bytes | 7,145 |
| **evidence spans inside a fenced region** | **28,029 of 319,293 (8.8%)** |
| claims whose text contains a fence marker | 901 of 336,190 |

So Markdown structure *is* available. The error was conflating ADR-016's
behaviour — fences are "dropped whole" as delimiters, 5,766 -> 868 — with
the absence of fenced *content*. The bodies survive; only the fence lines
are dropped. ADR-016's own invariant holds: exactly 5 evidence spans open
with a line-start fence, the residual its regex documents.

The correction does not rescue the policy. Fenced regions catch 50.7% of
code rows and flag **59.6%** of real contradictions — *worse* than
indentation's 50. Combined with indentation (union) they reach 99.5% of code
rows and still destroy 56 real-contradiction rows.

So the decision stands, for a different and better-evidenced reason: the
signal exists, and it is not separable.

## Evidence

- `src/ganymede4/compile/coderegion.py` — the primitive and `FRONTIER`
- `tests/test_code_region.py` — 23 tests, including the frontier recomputed
  against `/tmp/p19-t5-real.db`

Sabotage-tested. Each of: partial containment accepted, blank lines ending a
region, tabs ignored, broadest setting recommended, and each of the three
falsifiable frontier cells. All caught; the combined case caught five tests.