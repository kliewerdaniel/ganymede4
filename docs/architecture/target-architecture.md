# Target Architecture — Sovereign Knowledge & Agent Runtime

**Status:** Draft for ratification
**Depends on:** `00-ecosystem-survey.md`, `migration-map.md`
**Date:** 2026-09-27

---

## 1. The thesis, stated as an invariant

> The model is a probabilistic component inside a governed runtime. It is never the intelligence system, and it is never the authority.

Every structural decision below is downstream of that. Where a design choice could be justified by "the model probably handles it", it is rejected.

The load-bearing cycle:

```
Human → intent → agent runtime → policy gateway → execution fabric
      → observations → evidence ledger → knowledge graph
      → retrieval/context construction → local model → next agent proposal
```

and, separately and upstream:

```
sources → parsing → normalization → entity extraction → claim extraction
       → relationships → provenance → temporal state → contradictions
       → confidence → heuristics → knowledge graph → retrieval indexes
       → memory → policies → evaluations → versioned artifact
```

**The compiler is not the runtime.** They share a substrate and a schema; they are separate binaries. The compiler is deterministic-first and produces an artifact with no runtime dependency. The runtime reads the artifact and operates on it under policy.

---

## 2. Layer map

```
┌──────────────────────────────────────────────────────────────────┐
│  L6  CONSOLE (Next.js, read-only over the artifact)              │
│      Knowledge · Claims · Sources · Entities · Graph · Memory    │
│      Heuristics · Witness · Agents · Executions · Evidence       │
│      Policies · Contradictions · Artifacts · Evaluations · Audit │
├──────────────────────────────────────────────────────────────────┤
│  L5  A2A EDGE — opaque delegation boundary, signed Agent Cards   │
├──────────────────────────────────────────────────────────────────┤
│  L4  RUNTIME — run lifecycle, context construction, agent loop   │
│      intent → plan → proposal → … → artifact → final → audit      │
├──────────────────────────────────────────────────────────────────┤
│  L3  POLICY GATEWAY  ◄── THE AUTHORITY BOUNDARY                 │
│      PermissionEngine · PolicyStore · DecompositionGuard ·       │
│      approvals · capability tokens · fail-closed                │
├──────────────────────────────────────────────────────────────────┤
│  L2  EXECUTION FABRIC — MCP tool port + pluggable sandbox       │
│      stateless MCP (2026-07-28) · (tool, argv, cwd, policy)      │
│      → (result, audit-record)                                    │
├──────────────────────────────────────────────────────────────────┤
│  L1  SUBSTRATE — the governed knowledge artifact                │
│      claims · evidence · entities · heuristics · beliefs ·       │
│      temporal intervals · provenance · ledger · indexes          │
├──────────────────────────────────────────────────────────────────┤
│  L0  COMPILER — deterministic-first, incremental, versioned      │
│      sources → … → artifact/<version>/ + manifest + Merkle root  │
└──────────────────────────────────────────────────────────────────┘
```

**The one rule that makes this a system and not a stack:** nothing in L4 can write to L1. Every mutation is a typed `Proposal` evaluated by L3 against schema and policy, recorded in the ledger, and — where it changes epistemic state — re-derived by L0's lifecycle rules. This is `ganymede3` ADR 007, already implemented in `engine/policy.py`. It is not a new idea; it is the spine.

---

## 3. The substrate (L1) — what makes it governed rather than a database

The substrate is a **content-addressed, bitemporal, graph-native** store. Five properties, each traceable to a real prior asset:

**3.1 Content addressing everywhere.** Every record is `id = <prefix>-sha256(canonical(fields))`, excluding only volatile fields. This is `ganymede3`'s `_compute_content_hash` and `hermes-atlas`'s fingerprint, and it is what makes §5 (incremental) and §4 (reproducibility) possible at all. Derived from Software Heritage's "intrinsic identifier" concept: a document is identified by its content, independent of where it came from.

**3.2 Bitemporal, and this is the load-bearing distinction.** Two independent axes:

- `valid_time` — when the proposition was *true of the world*
- `transaction_time` — when *we learned or recorded* it

A corpus reconstructed "as of March" must not be contaminated by what we learned in June. `sovereign-agent-stack`'s `TemporalAuthority` header states the invariant exactly: *"A CURRENT RECONSTRUCTION MUST NOT BECOME A RETROACTIVE RECONSTRUCTION."* Most systems keep one axis; Graphiti/Zep keep both. This is a real moat, not a checkbox.

**3.3 Invalidation, never deletion.** An update supersedes. A removal retracts. The prior state stays addressable. This is Graphiti's update policy and `ganymede3`'s `RETRACTED`-vs-`SUPERSEDED` distinction — and it is the only way `sovereign diff` between two versions can be meaningful.

**3.4 Graph-native, not metadata-on-a-vector-store.** Typed nodes and typed edges are the *primary* representation. Vector, lexical (BM25), graph traversal, temporal, and provenance are five *query mechanisms over the same substrate*, not five stores that must be kept in sync. `ganymede3` already has real BM25 (`query/keyword.py`), real embeddings (`index/`), and real graph (`claim_graph/graph.py`) — but they are three separate stores today, which is exactly the sync problem the ecosystem survey warns about.

**3.5 The ledger is the primary build output.** Lineage is not runtime observability metadata; it is the artifact. Invert the usual order.

---

## 4. Provenance and reproducibility

### 4.1 The derivation chain

Every object resolves backward:

```
answer
  → claim
    → evidence spans (source_id, char offsets, stance, quality)
      → documents
        → episode (content-addressed root)
          → corpus fingerprint
            → compiler version
              → model identity (GGUF SHA-256 + backend version + seed + grammar)
```

All five questions in the brief's §9 are answerable by walking this chain, and the walk is deterministic. The chain is the *only* way epistemic state may be established — which means the compile-time and run-time ledgers are the same structure, and `answer → claim` is itself a ledger event.

### 4.2 Model identity is four things, not one

Per the ecosystem survey, determinism must be **scoped and declared, never assumed**. vLLM explicitly guarantees reproducibility only on identical hardware *and* version, and its batch-invariance mode has an open tensor-parallel bug. So the manifest pins:

1. **model** = GGUF SHA-256 (not a display name)
2. **backend** = runtime name + build version
3. **seed** = 0
4. **grammar** = the GBNF/schema constraining the output

and the docs must state that batch scheduling can perturb floats. A determinism claim is always scoped to that tuple. `ganymede3`'s `InferenceResult` already carries `input_hash`/`output_hash`/`provider`/`model` — this adds the two it's missing (seed, grammar) and changes `model` to a hash.

### 4.3 Reproducibility is proven, not asserted

`hermes-atlas/determinism.py` is the model: FULL EQUALITY (same sources → two fresh stores → byte-identical fingerprints) and DELTA ISOLATION (recompiling an unchanged slice must move no record and keep the changelog silent). Both fail-loud. Lifts nearly verbatim, with the artifact's **Merkle root over the claim set** as the single comparable identity.

### 4.4 Provenance shape: OpenLineage, not SLSA

Three core entities + facets: **(compile-run → source-episode → derived-artifact)**, with claim-type as a facet. The survey's warning is the design rule: SLSA v1.1 was retired and narrowed to a single track, and in-toto succeeded it by generalizing *and shrinking*. Keep the provenance schema small: *artifact id, episode ids, compiler/rule/model versions, resulting claim ids, timestamp, signature*. Everything else is a facet. Wikidata's statement-level ranks are the model for conflicting claims about the same subject.

---

## 5. Incrementality — a build system for knowledge

This is the brief's §10 and it is **genuinely unbuilt** in the estate (`ganymede3` has only a batch skip-list). Design:

```
source changed
  → affected structural units (by content hash)
    → affected evidence spans
      → affected claims
        → affected graph nodes and edges
          → affected heuristics
            → stale beliefs
              → affected indexes
                → affected evaluations
                  → new artifact version + Merkle root
```

Borrow Bazel's action cache verbatim: a compile step is keyed by **(rule version, input hashes, dependency hashes)**; the output is looked up by hash. Borrow Nix's "a derivation is a pure function of its input hashes." Make **`incremental update must not require full recomputation`** an architectural invariant with a test, not an aspiration — Graphiti's stated claim, adopted as a testable property.

Crucially: the cascade must produce a **staleness report**, not a silent rebuild. A source edit that invalidates a belief must say so.

---

## 6. Protocol boundaries

### 6.1 MCP — tools and resources, stateless, 2026-07-28 spec

**Current spec is `2026-07-28`, not 2025-11-25** — most blog posts are stale. The handshake (`initialize`/`initialized`, `Mcp-Session-Id`) is **retired**; `server/discover` replaced it; Multi Round-Trip Requests replaced server-initiated sampling/elicitation/roots. **Roots, Sampling, and Logging are deprecated until ≥ 2027-07-28** — do not build on them.

What this buys us, and it is a real architectural win: MCP is now a **stateless, per-request, cacheable, gateway-routable** JSON-RPC surface with `Mcp-Method`/`Mcp-Name` headers. That means:
- **The `Mcp-Method`/`Mcp-Mame` headers are the natural place to hang policy gating** — authorize on the header, before parsing the body.
- List results carry `ttlMs`/`cacheScope` → the knowledge substrate is served with correct caching for free.
- Statelessness → the compiled artifact can sit behind a stateless port and be load-balanced, cached, and metered without session affinity.

`sovereign-agent-stack`'s 596-LOC `MCPServer` is a working starting point but predates this spec; it must be reworked, not extended.

**Do not** depend on the official MCP Registry — it is preview with published "data resets may occur."

### 6.2 A2A — opaque delegation only, v1.0.0

**Current version 1.0.0 (2026-03-12)**, Linux Foundation, 150+ orgs. v0.3→v1.0 was an 8-month gap with a 975-line migration doc.

Use it strictly as an **opaque-execution boundary** — which is literally the spec's own guiding principle ("without needing access to each other's internal state, memory, or tools"). **Never map the typed claim/entity/provenance model onto A2A's `Message`/`Part`.** A2A is for talking to *other people's* agents; inside the framework, agent-to-agent is in-process typed proposals.

Steal: proto-as-normative-source; three-bindings-must-be-equivalent (a MUST, not an aspiration); **JWS-signed Agent Cards over RFC 8785 JCS canonical JSON** — the real new trust primitive, and it maps directly onto the signed-handoff mechanism `sworker` and `right-to-compute` already have; single-header version negotiation (`A2A-Version`, empty ⇒ 0.3); the Task state machine (`submitted/working/input-required/completed/canceled/failed/unknown`).

Avoid: the entire v0.3 wire shape. Pin the version at the edge, emit 1.0-native shapes only (`ROLE_*`, member-discriminated parts), keep 0.3 read support. **Maturity caveat: the x402 payment extension is still on the 0.3 wire.** "In the spec" ≠ "implemented."

### 6.3 No A2A inside the framework

Zero A2A implementations exist in the estate, and that is the correct starting position. A2A is an *edge* protocol (L5). Internal agent coordination is typed proposals through L3.

---

## 7. The witness — formalized

Currently a metaphor and a query-expansion trick (`ganymede3/engine/investigation.py:157` is the only real hit — "identify witnesses or participants who could confirm or deny" — and `ganymede/api/app/services/query_expansion.py:47` is a synonym list). It must become a primitive.

**Definition.** A witness is a *capability view over a specific artifact version*, not an agent, not a model, not a persona. It is constructed as:

```
Witness(artifact_version, scope) -> {
  supported_claims, contradicted_claims, unresolved_claims,
  insufficient_claims, contested_claims, retracted_claims,
  derived_heuristics, declared_heuristics,
  patterns, temporal_changes, gaps, contradictions,
  boundary: { what this corpus does not contain, what was never searched }
}
```

**Hard constraints, all testable:**

1. It is bound to one `artifact_version` and one Merkle root. It cannot see a future version.
2. **It must expose its own boundary** — the `UNRESOLVED` set is a first-class output, not an error. "I searched for X and it is not here" must be distinguishable from "I did not look." This is ADR 011's whole point, promoted to the interface.
3. It never claims knowledge outside the artifact. Its answers are *retrievals plus provenance*, never completions.
4. Every claim it returns resolves to evidence, and the evidence resolves to a character offset in an identified source. No exceptions.
5. It cannot be asked a question it will answer with an epistemic state it cannot justify. Absence of evidence → `INCONCLUSIVE`/`UNRESOLVED`, never a guess.

**The researcher loop the brief describes** becomes a real protocol: Researcher interrogates the witness → states a hypothesis with provenance → requests evidence → receives claims + contradictions + gaps → forms a typed proposal → Analyst verifies against the artifact (deterministic where possible) → Operator executes under policy. The witness is the *evidence source*, not a participant in the deliberation.

---

## 8. Heuristics — first-class, both derived and declared

No repo in the estate has these. Design:

- `Heuristic` is a node in the same graph as claims, with the same provenance requirements, and a `derived_from` set pointing at the observations/claims/structures that produced it.
- **Derived**: produced by a deterministic pattern-miner over the compiled graph (recurring co-occurrence, temporal regularity, source-reliability patterns, contradiction patterns). Deterministic miner → inspectable, reproducible, hashable.
- **Declared**: authored by the user, in a general representation — the brief's `WHEN X AND Y → EXPECT Z UNLESS Q WITH confidence C` is a fine *instance*, not the schema. The schema must be: `condition` (predicate over graph state) → `expectation` (claim-shaped) → `exceptions` → `support` (evidence-derived) + `confidence` **and** an epistemic state (per §2 of the migration map, confidence never replaces state).
- Both kinds are queryable, inspectable, and failable. A heuristic that the corpus contradicts must be able to reach `CONTRADICTED` — otherwise it is an oracle, not a heuristic.

---

## 9. Beliefs

```
Belief {
  claim, status, confidence,
  supporting_evidence[], contradicting_evidence[],
  derivation, created_at, observed_at, valid_at, last_verified,
  supersedes, superseded_by
}
```

**Belief ≠ Hypothesis ≠ Claim.**
- **Claim** — a proposition in the corpus substrate. Immutably identified.
- **Hypothesis** — a claim *under test*, with falsification conditions (`sovereign-intelligence/memory/hypothesis.py`).
- **Belief** — a *held epistemic state* about a claim, owned by an agent or the runtime, that moves over time as evidence arrives and is itself recorded.

Keeping these three distinct is what makes §12 (memory includes actions and outcomes) expressible: the belief revision trail is the persistent epistemic history.

---

## 10. Memory includes actions and outcomes

Not just facts. The substrate also records: agent actions, observations, decisions, tool executions, execution outcomes, verification results, **failed hypotheses**, and revised beliefs. Concretely:

```
researched X → believed Y → executed Z → Z produced R
→ R contradicted Y → belief revised → new knowledge state compiled
```

This requires the compile-time ledger (L0) and the run-time ledger (L4) to be the same append-only DAG — `ganymede3` ADR 008's `LedgerEvent` is already the right shape for both, with `actor` distinguishing `agent:*` / `system:compiler` / `policy:*` / `user:*`.

---

## 11. The verifier stack — three tiers, no LLM in tier 1 or 2

1. **Deterministic, structural** — `sworker/verify.py`'s 13 checks (`provenance_chain`, `recompute_sum`, `schema`, `set_equality`, …). Re-derive the number from source. Never trust the model's assertion.
2. **Entailment** — NLI between claim and cited span, run **at compile time** (per the survey: attribute at compile time where you control the model, seed, and grammar; never at query time). This is what replaces `ganymede3/extraction/verifier.py`'s Jaccard keyword overlap.
3. **LLM judge** — only where 1 and 2 are provably insufficient, and only as a *reported* signal. `ganymede`'s W7 result is the standing evidence that this is a real capability gap, not a tuning problem: cross-encoder and RRF both measure topical relevance, not answer containment, and their score distributions fully overlap.

Evaluation discipline carried from `sovereign-agent-stack`: `ResearchWindow` / `HoldoutWindow`, `TemporalAuthority` invariants verbatim, DSR/PBO/CSCV for statistical claims, and **null-world / synthetic-signal controls** — a gate that cannot detect a planted signal cannot be trusted to reject noise. Absence of sufficient evidence produces `INCONCLUSIVE`, not fabricated certainty.

---

## 12. CLI and console

**CLI is authoritative.** Minimal coherent set, derived from what already exists rather than the brief's full list:

```
sovereign init       # scaffold workspace, config, policy
sovereign ingest     # add sources, hash-addressed
sovereign compile    # L0: sources -> versioned artifact + manifest + Merkle root
sovereign query      # L1 read: claims, entities, graph, temporal
sovereign explain    # why does this object exist (hermes-atlas explain_claim)
sovereign witness    # instantiate a witness at a version; interrogate it
sovereign diff       # v1 vs v2: what changed, what went stale
sovereign verify     # run the verifier stack + gates
sovereign evaluate   # ResearchWindow/HoldoutWindow, DSR/PBO
sovereign agent      # run an agent against the artifact
sovereign run        # full runtime lifecycle under policy
sovereign replay     # re-execute a recorded run deterministically
sovereign inspect    # raw object dump
```

`init/ingest/compile/query/explain/verify` are near-direct ports of existing CLI surfaces (`ganymede3/engine/cli.py`, `hermes-atlas/cli.py`, `sworker/cli.py`). `witness`, `diff`, `replay`, `evaluate` are the genuinely new verbs and map to §5, §7, §4.3, §11 respectively.

**Console is a read-only inspector** over the artifact — `ganymede3`'s two-phase artifact compiler (ADR 009, Next.js static export, no inference at view time) is already the right shape and is the donor. It visualizes mechanisms; it does not hide them behind a chat UI. All 15 named surfaces map onto artifact views, which means they can be *precomputed at compile time* and shipped as static data.

---

## 13. Sovereign by default

- **Fully local, offline-capable, air-gap capable.** No cloud dependency is architecturally required. The model adapter defaults to a local OpenAI-compatible endpoint and `sworker/inference.py`'s `NullInference` + fail-closed `is_local()` are the pattern: **an unreachable provider means the deterministic layer stands alone, never a fabricated answer.**
- **Network is an explicit, policy-governed capability.** This is why `authority-graph-compiler/crawler.py` is dropped rather than adapted.
- **Reproducible when the environment is pinned** — §4.2's four-tuple.
- **Stdlib-first.** `sworker` (12.6k LOC, 490 tests, stdlib-only) and `hermes-atlas` (4.7k LOC) prove this is achievable at real scale. Postgres/pgvector/FastAPI is not required for the framework.

---

## 14. The one-paragraph answer

> How do you turn probabilistic models into components of a deterministic, inspectable, reproducible intelligence system? By making the model the *only* nondeterministic component in a substrate that is content-addressed, bitemporal, hash-chained, dependency-tracked, and policy-gated — and by making every claim it produces carry a machine-checkable derivation back to a character offset in an identified source, so that "the model says so" is never a sufficient reason and "the corpus does not contain it" is a *provable* statement rather than an absence.
