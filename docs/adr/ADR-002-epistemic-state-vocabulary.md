# ADR-002 — Epistemic state vocabulary

**Status:** Ratified (2026-09-27)
**Context:** `migration-map.md` F2, F3; `ganymede3` ADR 002 / 011

## Context

The brief proposed an 8-state belief vocabulary. The estate contains four
mutually incompatible ones:

| Source | States |
|---|---|
| Brief / AEP | `VERIFIED SUPPORTED CONTRADICTED INCONCLUSIVE STALE UNVERIFIED` |
| `ganymede3` `engine/database.py:262` | 9-state, SQLAlchemy |
| `ganymede3` `claim_graph/graph.py:80` | `PROPOSED VERIFIED CONTRADICTED DEPRECATED MERGED` (5) |
| `ganymede3` ADR 002 | "8-state superset" — 9 labels visible |
| `sovereign-intelligence` | `ACTIVE WEAKENED CONFIRMED FALSIFIED DEPRECATED` |
| `hermes-atlas` | `candidate supported validated contested invalidated superseded` |

The brief's list **cannot express "we looked and found nothing."** That
distinction is the entire thesis: absence must be *provable*, not inferred. A
system that collapses "unresolved" into "inconclusive" cannot tell a question it
never asked from one it asked and failed to answer.

## Decision

**`ganymede3` ADR 002/011 is the spine.** It is the most rationalized model in
the estate. On top of it:

- Add `DERIVED` (inferred by a heuristic miner, not asserted by a source)
- Add `ASSUMED` (axiom, declared by an author, no evidence required)

**Resolve the ADR 002 "8-state" label** — it enumerates 9. The label is wrong;
the enumeration is authoritative.

**`UNRESOLVED` and `INCONCLUSIVE` stay distinct.**

- `UNRESOLVED` — the system searched, and no answer exists in this artifact.
  Requires an investigation record naming what was searched.
- `INCONCLUSIVE` — evidence was found, and it does not support a conclusion.

**Confidence never gates epistemic state.** A state is a claim about the
*relationship between evidence and proposition*. Confidence is a claim about
*the estimator*. They are different types. Merging them is the defect that
produced the v0.1 fabrication: 23 "established facts" all carrying the identical
confidence `0.47636587698586486`.

## The spine (13 states)

```
UNEXAMINED   never searched
UNRESOLVED   searched, no answer in artifact   (requires investigation record)
INSUFFICIENT evidence found, not enough       (evidence magnitude ≠ sufficiency)
INCONCLUSIVE evidence found, does not support a conclusion
ASSUMED      declared axiom, no evidence
DERIVED      heuristic-derived, traces to miner
SUPPORTED    evidence bears the claim
VALIDATED    independently reproduced
CONTESTED    credible evidence on both sides
CONTRADICTED direct contradicting evidence
RETRACTED    withdrawn by its author
SUPERSEDED   replaced by a newer claim
INVALIDATED  falsified
```

`MERGED` (from the 5-state model) is not an epistemic state — it is a *graph
operation* on two claims. It moves to the edge vocabulary, not the state enum.

## Consequences

**Positive.** Absence is representable and therefore provable. The v0.1 failure
mode is structurally excluded. `derives_from` chains are inspectable.

**Negative.** 13 states is a lot of surface. Mitigation: a state **registry**
with legal-transition edges, so illegal transitions are rejected by the type
system rather than by convention, and the graph is enumerable and testable.

**Neutral.** A 13th epistemic type can be added without schema surgery — states
are data, not a CHECK constraint.

## Alternatives

1. **The brief's 8 states.** Rejected: loses "searched and found nothing."
2. **The 5-state `claim_graph` enum.** Rejected: no `INSUFFICIENT`, no
   `UNRESOLVED`, no `CONTESTED`. Directly contradicts the thesis.
3. **One merged mega-enum across all four sources.** Rejected: 20+ overlapping
   labels that mostly mean the same thing under different names. The migration
   map carries an explicit crosswalk for the losers.

## Acceptance

- [x] All 13 states defined with legal predecessor transitions
- [x] `UNRESOLVED` cannot be set without an investigation record
- [x] `confidence` is a separate type that no state-transition function reads
- [x] Illegal transitions raise
- [x] A crosswalk maps all four upstream vocabularies onto the spine
