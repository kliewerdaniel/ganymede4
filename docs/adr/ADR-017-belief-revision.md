# ADR-017 — A retracted claim takes its dependents with it

**Status:** Ratified (2026-09-29)
**Depends on:** ADR-002 (epistemic state vocabulary), ADR-006 (evaluator), ADR-013 (evaluation record size)

## The gap

`README.md` advertises this pipeline:

```
corpus → compiler → versioned artifact → witness → agent interrogation
       → proposal → policy gate → execution → evidence → verification
       → belief revision → next version
```

Eleven phases built every arrow except one. There is no belief-revision
component: grepping `src/` for `revision` returns two English uses of the word
in comments and nothing else. The diagram has been describing a system with a
hole in it.

The hole is not cosmetic. Consider what the corpus already contains:

- 16,897 `DERIVED` heuristics, each linked by a `DERIVED_FROM` edge to the
  claims that produced it.
- 319,293 source-derived claims, all `UNEXAMINED` until an evaluation pass
  moves them (the current artifact is built `--no-evaluate`).

A `DERIVED` heuristic is a generalization drawn from its supporting claims. If
one of those supporting claims is later retracted, the heuristic does not
become weaker — it becomes *unsupported*, and nothing in the current system
notices. The artifact keeps asserting it. That is a stale derived claim
laundering a retraction, and it is precisely the failure this project exists to
prevent.

**Measured on the real corpus, and worth recording because it is not what the
edge count suggests:** the 100,723 `DERIVED_FROM` edges resolve to 100,723
*distinct* supports, each with exactly one dependent, and the deepest chain is
one level. The mined heuristics form a flat bipartite graph, not a hierarchy.
On this corpus revision is therefore shallow by construction, and the
transitivity requirement below is a guarantee for corpora where it is not — a
different corpus, a future heuristic miner, or a hand-authored graph. The
uniformity is itself the point: revision does not assume the shape of the graph
it walks.

## The decision

Belief revision is a **deterministic, bounded, transitive closure over claim
dependencies**, driven by the existing `LEGAL_TRANSITIONS` table. It is not a
model, and it never reasons about meaning.

### What counts as a dependency

Two relations, both already in the store:

| Relation | Meaning | Direction |
|---|---|---|
| `DERIVED_FROM` | a heuristic rests on these claims | dependent → support |
| `ATTESTED_BY` | a claim was supported by these claims | dependent → support |

`ATTESTED_BY` does not exist as an edge today — attestation lives in the
`evaluations` row and in the `peer_groups` table. Revision needs to walk it
as a graph, so it reads the evaluation records rather than inventing a
parallel edge that could disagree with them. **The evaluation record is
authoritative; the graph is derived from it.**

### The rule

When a claim enters a terminal negative state —
`RETRACTED`, `INVALIDATED`, or `SUPERSEDED` — then every claim that depends on
it transitively is moved to `INVALIDATED`.

Three properties, each deliberate:

**It is transitive.** One level of propagation leaves the bug one level deep.
A heuristic derived from a heuristic must go too.

**It fails closed.** A dependent with no legal path to `INVALIDATED` is
**refused**, not skipped. The transition table already encodes the
non-monotonic states; if a dependent cannot legally be invalidated, the
revision record says so and the dependent keeps its state. Silently dropping
it would recreate exactly the laundering this ADR exists to prevent, one level
up.

**It never promotes.** Revision only ever moves claims *down* the table, to
`INVALIDATED`. It cannot resurrect a retracted claim, cannot promote
`INVALIDATED` → `SUPPORTED`, and cannot reach `VALIDATED` at all. A later
compile produces a new artifact version; revision never edits one.

### Why not the model

A model would be better at deciding whether a heuristic *survives* the loss of
one of its supports — that is a judgement about meaning. It would also be
undeterministic, unauditable, and free to invent the dependency graph it is
reasoning over. The thesis already settled this: the model is one
probabilistic component, and policy plus deterministic verification decide
what is accepted. Revision is verification.

The honest cost, stated in the ADR rather than discovered later: revision is
**conservative**. Losing one of five supports invalidates a heuristic that
four supports still justify. That is the wrong answer for a human. It is the
right answer for a system whose claim is that a stale belief must never
survive its own refutation, and it can be relaxed later by a policy that is
itself audited.

## Consequences

- The pipeline diagram in the README becomes true.
- `audit_provenance.py` gains a check: no claim may be `SUPPORTED` or
  `DERIVED` while depending transitively on a terminal-negative claim. The
  auditor stays a separate process importing nothing from the package.
- A revision record is content-addressed and names the triggering claim, the
  closure it computed, and the artifact version — so "why is this invalidated"
  has the same answer as "why is this supported".
- The `DERIVED` claim policy the witness never made explicit (open since
  Phase 3) is settled as a side effect: derived claims are revisable through
  their `DERIVED_FROM` edges, and the auditor's derived-claim exemption from
  direct-evidence checks stands.

## Rejected alternatives

**Recompile instead of revising.** A new corpus version re-derives everything
from scratch. Correct, and expensive: the 18,930-document corpus takes ~135s
to compile. Revision exists for the case where a single claim is retracted and
the artifact must reflect it without a full rebuild. It is not a substitute
for recompilation.

**Leave it, and let the next compile fix it.** This is what happens today, and
it is a real defect, not a simplification. Between the retraction and the next
compile, the artifact asserts something the evidence no longer supports. An
artifact is supposed to be inspectable *now*.

**Propagate only one level.** Simpler, and wrong. It moves the staleness
rather than removing it.
