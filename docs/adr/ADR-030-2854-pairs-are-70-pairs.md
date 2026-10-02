# ADR-030: 2,854 contradiction pairs are 70 pairs

Status: Ratified
Date: 2026-09-30
Depends on: ADR-020, ADR-024, ADR-027

Supersedes the decision half of ADR-027, which stays PROPOSED only in the
sense that its open question — "should the compiler emit code as claims?" — is
answered here. ADR-027's *sampling* work stands.

**Premise corrected by ADR-032.** The `no-code` decision below still stands, but
its stated reason is wrong. Most of the 2,854 pair rows were not contradictions
at all: 93.8% of them span two unrelated documents, matched only because they
shared a normalized bag of words. A document-scope constraint (ADR-032) removes
that noise with a principled rule and no fitted threshold, and loses zero
labelled real contradictions. Read this ADR together with ADR-032; where they
disagree about *why*, ADR-032 is right.

## Context

ADR-027 proposed a policy for code-shaped claims and refused to decide it
without a human-labelled sample. The sample was built (122 rows) and the
labelling was left to a human.

Two things are now measured that were not before, and between them they
settle the question without needing a policy to be fitted at all.

## Finding 1: the population is 70 pairs, not 2,854

Every contradicted evaluation stores its counter-claims in a `peer_groups`
row (ADR-013). Expanding those and normalising whitespace:

| | count |
|---|---:|
| contradicted subjects | 402 |
| subject/peer pair rows | 2,854 |
| **distinct unordered text pairs** | **70** |
| redundancy | **40.8×** |

The concentration is extreme:

| top N distinct pairs | share of pair rows |
|---:|---:|
| 1 | 35.9% |
| 2 | 63.9% |
| 5 | 78.8% |
| 10 | 84.5% |
| 20 | 91.9% |

A single pair — `return ''` vs `return None` inside a `JSON decoding failed`
handler — accounts for **1,026 of 2,854 pair rows**.

This is the same shape as ADR-024's finding on the support side: a small
number of real distinct findings, massively re-reported. It also means the
122-row sample is **32 distinct pairs**, and any precision computed from it
carries the redundancy of its sampling frame, not of the population.

## Finding 2: the labels, and what they cost

All 70 distinct pairs were read and labelled. Labels and per-pair rationale
are in `docs/labelling/agent-labels.json`. Full rationale for a few:

- `It is also a pyramid scheme.` / `THIS IS NOT A PYRAMID SCHEME!` →
  `real_contradiction`. The component exists for this.
- `TypeError: string indices must be integers` / `..., not 'str'` →
  `subset`. Same error, more specific wording, nothing opposing.
- `py migrate ... Applying core.` / `py migrate ... No migrations to apply.` →
  `other`. Two runs of one command at different times. Both true; neither is
  a claim about the world.
- `Specify whether to ...` / `data_augmentation: This command specifies ...` →
  `other`. Argparse help and its rendered form: one proposition, stated twice.

Population-weighted:

| label | pair rows | share |
|---|---:|---:|
| `code_fragment` | 2,504 | 87.7% |
| `other` | 164 | 5.7% |
| `real_contradiction` | 94 | 3.3% |
| `subset` | 92 | 3.2% |

Unweighted over the 70 distinct pairs: 36 code fragments (51.4%), 14 real
contradictions (20.0%), 12 other, 8 subset.

## Decision: `no-code` costs nothing

> **Corrected by ADR-032.** The decision to leave `no-code` unimplemented
> stands. The reasoning below — "the refusals are concentrated entirely in code
> fragments, and no real contradiction is lost" — was measured on labels that
> treated cross-document noise as findings. The conclusion happens to survive
> for a different and better reason, which is recorded in ADR-032.

`CONTRADICTED` is absorbing, so the asymmetry is the whole decision — a
refused verdict is recoverable, a lost real contradiction is not.

| policy | pairs refused | real contradictions lost |
|---|---:|---:|
| `no-code` | 2,504 (87.7%) | **0** |
| `no-code-or-subset` | 2,596 (91.0%) | **0** |
| `no-code-or-subset-or-other` | 2,760 (96.7%) | **0** |

**Zero real contradictions are lost under every candidate policy.** The
refusals are concentrated entirely in code fragments.

This is the decision ADR-027 was waiting for, and it arrived from the
population rather than from a threshold. **The policy to adopt is the one the
labels select, and no threshold needs fitting.**

## Finding 3: the existing lexical classifier cannot do this

The `stratum_of` heuristic, checked against the 70 labels:

| | rows |
|---|---:|
| agrees with the label | 34 of 70 pairs |
| **code fragments it misses** | **2,484 of 2,504 rows** |
| non-code it wrongly catches | 58 rows |

It misses 99.2% of the code. The reason is structural: these are **mid-line
fragments with no indentation** — `; return; } if (!`, `text) return None` —
and a density-of-punctuation classifier keyed on line structure cannot see
them.

This is the decisive result against fitting a threshold. A threshold tuned on
this corpus to catch 2,484 rows would have to be tuned to a feature the
classifier does not measure, and would be fitted to 70 pairs. That is the
threshold-fitting this project keeps declining, and now it is declined with
evidence rather than taste.

## What is NOT decided here

**The compiler is not changed.** This ADR records the measurement and the
policy selection. Whether to suppress code at the compiler, in the evaluator,
or not at all is a separate change with its own tests and its own measured
artifact effect — and per the phase's discipline it needs a failing test first.

The honest framing: `no-code` is *selected*, not *implemented*. Implementing it
requires a code detector, and Finding 3 says the existing one is inadequate.
Building an adequate one is the next task, and it should not be a
corpus-fitted heuristic.

## Consequences

- ADR-027's question is answered: code-shaped claims are 87.7% of
  contradiction pairs and 0% of real contradictions in this corpus.
- The 402 contradicted claims are not 402 findings. They are ~70, of which 14
  are real. Every downstream number that counted pairs was inflated ~40×.
- A human-labelled re-read of the 14 real contradiction pairs is still worth
  having before anything is suppressed permanently, and the labelled sheet is
  at `~/Downloads/contradiction-sample.LABELLED-BY-AGENT.csv` for that purpose.

## Provenance of the labels

These labels were produced by an agent reading all 70 distinct pairs, not by
the project's author. That is a real limitation and it is stated rather than
buried: they are a *second* opinion from a system whose judgement has not been
independently calibrated for this task.

They are recorded in the repository with per-pair rationale so they can be
audited and overridden row by row, and the scorer's own output (21/122 real in
the 122-row sheet, `prose` stratum only 35% consistent with the frame) is kept
alongside. If a human re-labels and disagrees, the disagreement is the
measurement worth having — and at 70 distinct pairs, it is now a tractable one.
