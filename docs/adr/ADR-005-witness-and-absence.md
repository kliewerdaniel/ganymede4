# ADR-005 — The witness: what a read may assert, and how absence is proven

**Status:** Ratified (2026-09-27)
**Depends on:** ADR-002, ADR-004

## Context

ADR-004 took away the compiler's ability to assert certainty. That decision
leaves a hole by design: nothing in the system can move a claim off
`UNEXAMINED`, and no component can ever emit `UNRESOLVED`. A store where every
claim is `UNEXAMINED` forever is honest and useless.

This ADR defines the component that closes the hole without reopening the
fabrication risk, and it is the project's differentiator rather than a utility.
The five constraints in `target-architecture.md` §7 are the specification; what
follows is the decision about **how those constraints are enforced**, because
"it must expose its boundary" is a requirement and not yet a mechanism.

The thing being designed around is `ganymede3`'s state: `grep -rin "witness"`
returns exactly one hit, and it is a string in an investigation prompt
("identify witnesses or participants who could confirm or deny"). In
`ganymede` the word appears only as a query-expansion synonym. The witness is
currently a metaphor, and a metaphor is what produced the v0.1 narrative.

## Decision

### 1. A witness is a read-only view bound to one artifact version

`Witness(store, manifest)` is constructed over a specific `Manifest` and refuses
to serve any claim not present in that version's leaf set. It is not an agent,
not a persona, not a model. It has no write path to claims, no method that
changes epistemic state, and no method that returns free text.

Constraint 1 ("cannot see a future version") is enforced by the binding, not by
convention: an id outside the version's leaves raises `OutOfVersion`.

### 2. Answers are typed. There is no string return.

Every answer is a `WitnessAnswer` with an explicit `EpistemicState`, the claim
ids, the resolved evidence, and the source offsets. There is no
`answer() -> str`, because a method that returns a string invites a caller to
print it as an answer, and a printed string carries no state. The absence of
that method is the enforcement.

### 3. `UNRESOLVED` is the witness's native output, and it costs a written search record

This is the heart of the ADR. The compiler could not emit `UNRESOLVED` because
it had not searched. The witness **has** searched: it has a query, a lexical
method, a scope, and a version. So it may assert absence — but only by writing
an `Investigation` first that names all four, and attaching it. ADR-002's
`MissingInvestigation` guard does the rest.

So absence is not returned as a special case. It is returned as a normal answer
whose state happens to be `UNRESOLVED`, carrying the same provenance machinery
as every other answer. There is exactly one code path, and the honest answer is
not a different shape from the dishonest one — it is the same shape with a
different state.

### 4. The search method is disclosed in the investigation record, not hidden

A keyword search finding nothing is weaker than "it is not there". The
investigation record therefore carries the method (`lexical-bm25`, in this
phase) alongside the query and the scope, and `WitnessAnswer` surfaces it in its
boundary. A consumer reading `UNRESOLVED` learns that the corpus was searched
*lexically*, not semantically — which is the honest description, and is exactly
what the retrieval verifier gap predicts will matter as the corpus grows.

A future semantic search does not replace this; it adds a second method to the
record, and `UNRESOLVED` from a lexical search and from a semantic one are
distinguishable claims. Collapsing them would be the falsifiability hole
`migration-map.md` §2 names, entered from the other direction.

### 5. Retrieval ranking is not an epistemic judgement

The witness returns claims ranked by BM25, and the ranking is exposed as a
`rank` field so a caller can see it was lexical. Ranking never promotes a claim
to a stronger state, and the ADR-004 wall keeps the compiler from pre-baking
`SUPPORTED` into what the witness can find. A claim the corpus states
emphatically is still `UNEXAMINED` until something evaluates it, and in this
phase nothing does. The witness reports what is *in* the corpus; it does not
rule on what is *true*.

### 6. The boundary is a first-class return value

`Witness.boundary()` is not derived from failures or omitted on success. It
reports, as data: which sources the version contains, which states are present,
which queries have been asked and what they returned, and — explicitly — what
the witness has *not* been asked. An empty `asked` list on a fresh witness is
itself the answer to "what does this not know?".

## Consequences

- The store can leave `UNEXAMINED` legitimately: a witness interrogation is the
  event that moves it, and only with a recorded investigation behind it.
- A caller cannot get a confident-sounding string out of the witness. It gets a
  state, or an exception.
- The lexical/semantic distinction is carried in data from day one, so adding a
  real retriever later is additive rather than a migration.
- **Honest gap:** the witness has no evaluator, so nothing it returns ever
  leaves `UNEXAMINED`. Phase 3 as specified makes absence provable but does not
  make support provable. An `evaluate` component that assigns
  `SUPPORTED`/`INCONCLUSIVE` from a real entailment check is the next ADR, and
  it must satisfy the same rule this one does — no state without a record.

## Alternatives rejected

**Return `UNRESOLVED` as a flag on the answer instead of a real claim state.**
Rejected: it creates a parallel channel, so "unresolved-ness" stops being
queryable and starts being a rendering concern. The falsifiability hole is
exactly what happens when absence lives outside the state machine.

**Let the witness return the top-k claim texts and let the caller judge.**
Rejected: that is the brief's RAG shape with a provenance field bolted on. The
caller has no way to distinguish "rank 1 of 400" from "the only match", and will
present them the same way.

**Have the witness assign `SUPPORTED` when a lexical match is exact.**
Rejected: an exact substring match is precisely the thing that is easy to
manufacture and hard to falsify, and the v0.1 failure was built from sentences
that were all present in the corpus. Presence is not support.
