# ADR-004 — The deterministic compiler's authority, and the artifact manifest

**Status:** Ratified (2026-09-27)
**Supersedes:** nothing
**Depends on:** ADR-001, ADR-002, ADR-003

## Context

Phase 1 gave us a store that can hold a claim only if it has resolvable
provenance, and a 13-state epistemic vocabulary. It did not say who *writes*
those claims, or what a compile is allowed to conclude.

This is the decision that separates the project from a RAG framework, and it
is easy to get wrong in exactly one direction. A compiler that assigns
`SUPPORTED` on its own has quietly promoted a string-matching heuristic into
truth. A compiler that assigns `UNRESOLVED` on its own has quietly claimed a
search it never performed.

The `ganymede` v0.1 failure (recorded in `ganymede3/docs/phase0-audit.md`) is
the worked example: 55,308 claims existed, 23 narrative "established facts"
were emitted, **none of the 23 claim ids existed in the graph**, and all 23
shared one identical confidence value. The narrative was stitched from
unrelated snippets. Nothing in the pipeline was *wrong* at any individual
step; the defect was that a layer with no evidentiary authority emitted
authoritative-sounding prose anyway.

## Decision

### 1. A compile may not assign an epistemic state

The deterministic compiler emits **`UNEXAMINED` claims and nothing else.**
`SUPPORTED`, `VALIDATED`, `CONTRADICTED`, `CONTESTED`, `INSUFFICIENT` — none
of these are reachable from the compiler.

Rationale: every one of those states is a *judgement that evidence bears on a
proposition*. A deterministic function operating on text can establish that a
sentence exists and that other sentences exist. It cannot establish that one
is about the other, because "is about" is a semantic judgement, and every
deterministic proxy for it is a topical-relevance heuristic. The retrieval
verifier gap documented in `migration-map.md` is the measured form of this:
cross-encoder and RRF score distributions fully overlap between answerable and
answer-absent cases. Shipping that as a state assignment would be shipping a
threshold problem as a certainty.

So the compiler's output is **candidates with provenance**, and the epistemic
layer sits above it. This makes the LLM (when it arrives) *unnecessary for
validity* rather than *authoritative for truth* — which is the thesis.

### 2. `UNRESOLVED` is not a compiler output either

`UNRESOLVED` asserts a search happened. A compile over a corpus did not search
the world; it read some files. Emitting `UNRESOLVED` from a compiler would be
a false claim of exhaustiveness — the precise shape of the falsifiability hole
`target-architecture.md` §7 constraint 2 exists to close.

`UNRESOLVED` is reachable only from a **witness interrogation with a recorded
investigation** (Phase 3), which is where the "I looked and here is where I
looked" is true by construction.

### 3. Compilation is a pure function of source bytes

`compile_corpus(sources) -> Artifact` is deterministic in the strong sense:
same bytes in, byte-identical artifact out, on any machine, in any process
order, in any Python minor version. This is `hermes-atlas`'s full-equality
gate, and it is the reason `content.py` excludes `compiler_version` from the
content hash.

### 4. The manifest is a Merkle root over content-addressed leaves

The artifact version is identified by a Merkle root over sorted leaf hashes, not
by a timestamp, a run id, or a counter. Consequences:

- Two identical compiles produce the *same version id*. "Version" means
  "distinct content", so a recompile that changes nothing is not a new version.
- The root is order-independent, so ingestion order cannot change the version.
- A later version can prove exactly which leaves changed — that is the basis
  for `sovereign diff`, and for delta isolation.

`compiled_at` and `run_id` are stored in the manifest but **excluded from the
root**, so provenance is preserved without making the version non-reproducible.
This is the same identity/provenance split as `content.py`, applied to the
artifact instead of the record.

### 5. Extraction is segmentation, not summarization

The compiler's only text operation is **deterministic sentence segmentation**
with offsets, producing `UNEXAMINED` claims over exact spans. It does not
summarize, paraphrase, merge, or rank. A claim's text is always a verbatim
substring of its source — the same rule the store already enforces for
evidence, applied to the claim layer. If it were not a substring it would be
the author's sentence, not the corpus's.

### 6. Derived heuristics require a minimum support threshold and are marked

Heuristics mined in this phase are *derived* (deterministic pattern-miner over
the compiled graph), never *declared*. Each carries `supporting_claim_ids` and
is itself a claim in the graph, so it can reach `CONTRADICTED` later. A heuristic
with support below `HEURISTIC_MIN_SUPPORT` is not emitted at all — an
under-supported pattern is noise, and a wrong heuristic is worse than no
heuristic.

## Consequences

- The compiler cannot lie about certainty, because it has no vocabulary for it.
- The first thing Phase 3 must build is the component that *does* assign states
  — and that component is the witness, which must be equally unable to assign a
  state without recording the investigation that justifies it.
- `diff` between two compiles is empty when nothing changed, and a leaf-level
  set difference when something did. This is testable without a model.
- Two honest gaps remain and are **not** solved here: (a) the compiler proposes
  every sentence, so candidate volume is high and a ranking/pruning stage is
  needed before Phase 3 is usable at scale; (b) no heuristic is *declared* yet.

## Alternatives rejected

**Let the compiler assign `SUPPORTED` when a claim has ≥1 evidence span.**
Rejected: evidence magnitude is not sufficiency, and this is the exact
substitution the whole project exists to refuse. It also makes `SUPPORTED`
unfalsifiable — every future contradiction then requires rewriting history
rather than transitioning a claim.

**Version the artifact by timestamp or run counter.**
Rejected: it makes every recompile a "new version", which trains reviewers to
ignore version changes. A version must mean "the content is different".

**Let the compiler emit `UNRESOLVED` for propositions with no match.**
Rejected: the compiler did not search; it parsed. Claiming absence from
parsing is the same category of error as the v0.1 narrative, just quieter.
