# Data Model — Sovereign Knowledge & Agent Runtime

**Status:** Draft for ratification
**Date:** 2026-09-27

Principles: (1) every record is content-addressed; (2) every derived record carries provenance; (3) confidence never replaces epistemic state; (4) new epistemic types must be addable without schema surgery.

---

## 0. Identity and versioning

```
id       = <prefix>-sha256(canonical(record minus volatile fields))
volatile = {created_at, updated_at, fetched_at, _meta, content_hash}
```

Prefixes: `src` episode · `su` structural unit · `ev` evidence span · `clm` claim ·
`ent` entity · `con` contradiction · `heu` heuristic · `blf` belief · `hyp` hypothesis ·
`inv` investigation · `ev_` (runtime) observation · `run` compile/run · `art` artifact ·
`evt` ledger event · `pol` policy.

`canonical()` is RFC-8785-style JCS: sorted keys, no insignificant whitespace, UTF-8.
Port from `authority-epistemic-protocol/src/lib/canonicalization`.

---

## 1. Typed knowledge

The brief's twelve types, plus what the ADRs force:

| Type | Meaning | Notes |
|---|---|---|
| `SOURCE` | raw input, content-addressed | = episode. Provenance root. |
| `DOCUMENT` | normalized container | may hold many structural units |
| `ENTITY` | person / org / place / concept | mentions, aliases, evolving summary |
| `CLAIM` | atomic proposition | the central object |
| `OBSERVATION` | what an execution actually returned | runtime, not compile-time |
| `INTERPRETATION` | a reading of evidence | never a claim |
| `OPINION` | attributed stance | carries speaker/author |
| `INFERENCE` | derived from other claims | `derived_from` is mandatory |
| `HYPOTHESIS` | claim under test | falsification conditions required |
| `DECISION` | a choice made, with rationale | |
| `ACTION` | a proposed-then-executed operation | links proposal → execution |
| `OUTCOME` | what the action produced | revises belief |
| `HEURISTIC` | pattern for reasoning | derived or declared |
| `BELIEF` | held epistemic state | runtime-owned |

Extensibility rule: types are a registry, not an enum in a check. A new epistemic type is a registration plus a lifecycle declaration, not a migration.

---

## 2. Claim

```json
{
  "id": "clm-<sha256>",
  "type": "CLAIM",
  "text": "the proposition",
  "normalized": "dedup form",
  "claim_type": "fact|event|motif|transformation|entity|opinion|inference",

  "state": "UNRESOLVED",
  "state_evidence": "the check that produced this state, with inputs",
  "confidence": 0.0,
  "confidence_terms": {
    "independence": 0.0, "reliability": 0.0, "recency": 0.0,
    "corroboration": 0.0, "contradiction_penalty": 0.0
  },

  "evidence_ids": ["ev-..."],
  "source_ids": ["src-..."],
  "entity_ids": ["ent-..."],
  "contradiction_ids": ["con-..."],
  "heuristic_ids": ["heu-..."],
  "derived_from": ["clm-..."],

  "valid_time":      {"from": null, "until": null},
  "transaction_time":{"recorded_at": 0.0, "superseded_at": null, "retracted_at": null},
  "last_verified": null,

  "compiler_version": "0.1.0",
  "policy_version": "0.1.0",
  "history": [{"from": "...", "to": "...", "at": 0.0, "evidence": [...], "actor": "..."}]
}
```

### 2.1 Claim state — `ganymede3` ADR 002/011, not the brief's list

`UNEXAMINED · SUPPORTED · VALIDATED · CONTESTED · CONTRADICTED · INSUFFICIENT · UNRESOLVED · RETRACTED · SUPERSEDED`
plus **`DERIVED`** and **`ASSUMED`** as genuinely new additions.

`UNRESOLVED` **requires an `inv` investigation record proving a search occurred.** This is the falsifiability invariant: absence is only claimable if the search is recorded. Confidence is orthogonal — a `CONTRADICTED` claim can have confidence 0.99; that is exactly the situation the two-field split exists to represent.

### 2.2 Edges (typed, graph-native)

`SUPPORTS · CONTRADICTS · CONTRADICTS_COMPATIBLE · DERIVES_FROM · ENTAILS · QUALIFIES ·
SUPERSEDES · GENERALIZES · CONCERNS · MODIFIES · PRODUCES · REVISES · DEPENDS_ON · HAS_GAP`

`CONTRADICTS_COMPATIBLE` is the reconciliation of `claim_graph.EdgeType` (5 values) with
ADR 002's status model: two claims that cannot both be true *in the same validity interval*
are contradictory, not absolutely contradictory. Depends on bitemporality being real.

### 2.3 Evidence span

```json
{
  "id": "ev-<sha256>", "type": "EVIDENCE",
  "source_id": "src-...", "structural_unit_id": "su-...",
  "span_start": 0, "span_end": 0, "quote": "exact text",
  "stance": "support|contradict|qualify",
  "quality": "direct|implicit|contextual|tangential",
  "speaker": "user|assistant|system|author|null",
  "reliability": 0.0, "timestamp": null,
  "source_checksum": "<sha256>",   // staleness detection
  "parser_version": "0.1.0", "needs_revalidation": false
}
```

`quality` is `hermes`-style: **magnitude ≠ sufficiency.** Ten tangential spans are not one direct span. There is no confidence threshold for truth anywhere in this system — only for relevance ranking.

---

## 3. Episode and source

`SOURCE` is content-addressed on the *raw bytes*, normalized by `parser_version` +
`normalization_version`. `DOCUMENT` is the normalized envelope. `STRUCTURAL_UNIT` is the
addressable span container (turn / page / paragraph / file / commit / speaker_segment) with
`span_start`/`span_end` and `parent_id`.

This is `ganymede3` ADR 004 essentially unchanged, and it is correct: immutable,
content-hash gated, with `source_checksum` threaded onto every evidence span so a source
edit propagates staleness mechanically.

---

## 4. Entity

```json
{
  "id": "ent-<sha256>", "type": "ENTITY",
  "label": "...", "normalized": "...", "aliases": [],
  "entity_type": "person|org|place|concept|event|artifact",
  "mentions": 0, "source_ids": [],
  "summary": { "text": "...", "valid_time": {...} },   // evolves, never overwritten
  "structural_signals": { "pagerank": 0.0, "degree": 0 }  // NEVER epistemic
}
```

`structural_signals` is quarantined in its own field precisely because
`authority-graph-compiler/graph.py` and `pagerank-seo` compute it and both correctly
disclaim it: *"PageRank values are raw structural metrics — NOT truth scores."* Quarantine
it so no downstream code can accidentally read it as confidence.

---

## 5. Heuristic

```json
{
  "id": "heu-<sha256>", "type": "HEURISTIC",
  "origin": "derived|declared",
  "statement": "human-readable form",
  "condition":    {"predicate": "<graph predicate>", "params": {}},
  "expectation":  {"claim_ref": "clm-... or inline", "state": "SUPPORTED"},
  "exceptions":   [{"condition": {...}, "reason": "..."}],
  "support": ["clm-...", "ev-...", "con-..."],
  "confidence": 0.0,
  "state": "SUPPORTED",
  "derived_from": {"pattern_miner": "<name+version>", "input_fingerprint": "<sha256>"},
  "declared_by": null,
  "valid_time": {...}, "transaction_time": {...}
}
```

`origin: derived` requires `derived_from` to be non-empty and the miner to be deterministic.
`origin: declared` requires `declared_by`. Both are inspectable and both are failable — a
heuristic the corpus contradicts reaches `CONTRADICTED`, which is what distinguishes a
heuristic from an oracle.

---

## 6. Belief and hypothesis

```json
{
  "id": "blf-<sha256>", "type": "BELIEF",
  "claim_id": "clm-...",
  "holder": "agent:researcher|system:runtime|user:daniel",
  "state": "SUPPORTED", "confidence": 0.0,
  "supporting_evidence": ["ev-..."], "contradicting_evidence": ["ev-..."],
  "derivation": "how this holder came to believe it",
  "created_at": 0.0, "observed_at": 0.0, "valid_at": null, "last_verified": null,
  "supersedes": "blf-...", "superseded_by": null
}
```

`HYPOTHESIS` is distinct and lives in its own table: `claim`, `falsification_conditions`,
`status ∈ {ACTIVE, WEAKENED, CONFIRMED, FALSIFIED, DEPRECATED}`, `evaluation_history[]`.
Ported from `sovereign-intelligence/memory/hypothesis.py`.

A belief is a *held state*; a hypothesis is a claim *under test*. Conflating them is why most
memory systems lose the revision trail.

---

## 7. Ledger

`ganymede3` ADR 008's `LedgerEvent`, used for both compile-time and run-time:

```json
{
  "event_id": "evt-<sha256>",
  "event_type": "SOURCE_INGESTED|EVIDENCE_EXTRACTED|CLAIM_PROPOSED|CLAIM_SUPPORTED|
                 CLAIM_CONTRADICTED|CLAIM_RETRACTED|CLAIM_SUPERSEDED|CONTRADICTION_DETECTED|
                 INVESTIGATION_STARTED|INVESTIGATION_COMPLETED|MODEL_INVOKED|MODEL_SKIPPED|
                 ARTIFACT_COMPILED|GATE_PASSED|GATE_FAILED|ACTION_PROPOSED|ACTION_EXECUTED|
                 VERIFICATION_RECORDED|OUTCOME_RECORDED|BELIEF_REVISED",
  "actor": "agent:researcher|system:compiler|policy:lifecycle-gate|user:daniel",
  "operation": "...", "inputs": {}, "outputs": {},
  "parents": ["evt-..."],
  "at": 0.0, "predecessor_hash": "<sha256>", "entry_hash": "<sha256>"
}
```

`predecessor_hash`/`entry_hash` make it a **verifiable hash chain** — `sovereign-intelligence/memory/audit_store.py`'s `AuditStore` design, made the substrate's primary build output rather than runtime metadata. `INVESTIGATION_COMPLETED` with `outcome: "unresolved"` is the only legal path to `UNRESOLVED`.

---

## 8. Artifact layout

```
artifact/
  manifest.json          # version, corpus fingerprint, Merkle root,
                         # compiler/policy version, model identity 4-tuple
  sources/               # episode registry + normalized docs
  units/                 # structural units, addressable
  evidence/              # evidence spans, sharded
  claims/                # claim records, sharded
  entities/
  graph/                 # typed edges, adjacency + reverse adjacency
  memory/                # beliefs, hypotheses, decisions, outcomes
  heuristics/            # derived + declared
  provenance/            # derivation chain index
  policies/              # versioned policy set
  indexes/               # BM25, embedding, temporal, provenance
  evaluations/           # ResearchWindow/HoldoutWindow results
  audit/                 # ledger segments
  models/                # pinned model identity + grammar
  agents/                # agent cards (A2A-compatible, JWS-signable)
```

Not `artifacts` plural — **the artifact is the unit of version identity.** Compiling produces a new version; nothing is mutated in place. `sovereign diff` is a comparison of two manifests.

---

## 9. Retention and versioning

- Compile → new version dir, new Merkle root, new manifest. Prior versions retained (invalidation, not deletion).
- Incremental compile → action-cache hit for unchanged derivations; affected subgraph only; **staleness report emitted as a first-class output**.
- Claim identity is stable across versions: the same claim text from the same source fingerprint yields the same `clm-` id, so `diff` is a set operation on ids.

---

## 10. Open design forks

1. **Storage.** stdlib `sqlite3` (matches `sworker`/`hermes-atlas` character, dependency-free) vs Postgres+pgvector (matches `ganymede` v1, heavyweight). *Recommendation: sqlite3, with the graph as adjacency tables.*
2. **Claim-state spine.** ADR 002/011 (recommended) vs the brief's 8-state list. See `migration-map.md` §2.
3. **Confidence formula.** `ganymede3/engine/confidence.py`'s five terms are a reasonable starting point; `confidence` must never gate truth, only relevance.
