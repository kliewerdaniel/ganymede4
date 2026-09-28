# Migration Plan, Vertical Slice, and Test Strategy

**Status:** Draft for ratification
**Date:** 2026-09-27

---

## PART A — Migration plan

### A.0 RATIFY forks (blocking — no code until these are decided)

| # | Fork | Recommendation | Consequence if wrong |
|---|---|---|---|
| F1 | **License.** `ganymede3` and `authority-epistemic-protocol` have **no LICENSE file**; the framework is meant to be open source. | Apache-2.0 for the framework (matches `right-to-compute`, the most permissive-protocol-oriented repo). Add explicit LICENSE to `ganymede3` and `authority-epistemic-protocol` before vendoring. | Cannot publish. Legal exposure. |
| F2 | **Claim-state vocabulary.** Brief's 8-state list vs `ganymede3` ADR 002/011's 10-state spine. | ADR 002/011 as spine + add `DERIVED`/`ASSUMED`. The brief's list cannot express "searched and found nothing". | Loss of falsifiability — the core thesis. |
| F3 | **`ganymede3` duplicate claim models.** `engine/database.py:262 ClaimRecord` (SQLAlchemy, 9 states) and `claim_graph/graph.py:80 Claim` (sqlite3, 5 states) coexist unreconciled. | Consolidate on one. Given F4, choose the stdlib-sqlite shape and delete the SQLAlchemy model; carry the 10-state vocabulary into it. | Two epistemic vocabularies ship in v1. Unrecoverable confusion later. |
| F4 | **Storage.** stdlib sqlite3 vs Postgres+pgvector. | stdlib sqlite3. `sworker` (12.6k LOC, 490 tests) and `hermes-atlas` (4.7k LOC) both prove stdlib-first works at real scale, and it preserves the offline/air-gap property with no service dependency. | Heavier install; weaker sovereignty story. |
| F5 | **Name.** "Sovereign" prefix is already used by ≥6 existing repos. | Needs disambiguation before the repo is public. | Cosmetic, but do it now not later. |
| F6 | **v1 demo corpus.** The 123,221-chunk Ganymede run lives behind Postgres in `ganymede/api/`. `ganymede3/corpus/sources` is 4 files / 16K. | Start with the small corpus for the vertical slice, then the full run as the demonstration. Both are stated. | — |

### A.1 Phase 0 — Ground truth (no new architecture)

1. Resolve F1–F4. Write the decisions as ADRs.
2. Stand up the new repo. `git init`, `LICENSE`, `README`, `pyproject.toml` (stdlib-first, `requires-python >= 3.11`).
3. Vendor, **with license headers and attribution**, from `hermes-atlas` and `sworker` only:
   - `hermes_atlas/determinism.py` (the gate — lift nearly verbatim)
   - `hermes_atlas/ledger.py`, `explain.py`, `coverage_gaps.py`, `gaps.py`
   - `sworker/permissions.py` (PermissionEngine + AST classifier + DecompositionGuard)
   - `sworker/verify.py` (13 deterministic checks)
   - `sworker/evidence.py` (EvidenceLedger)
   - `sworker/policy.py` (content-hashed PolicyStore)
   - `sworker/statemachine.py`
   - `sworker/inference.py` (Inference ABC + NullInference + fail-closed is_local)
   - `sworker/knowledge.py` (**as the integration pattern**, not the Atlas dependency — the new framework *is* the compiler, so the degraded-fallback discipline is what transfers, not the bridge)
4. Port from `ganymede3` (design, lift carefully): ADR 002/003/004/005/006/007/008/009/011, `engine/store_evidence.py` content-hash discipline, `engine/database.py` record shapes (reshaped to sqlite3 per F3/F4), `engine/policy.py` proposal/authorization model, `engine/inference.py` provider ladder.
5. Re-run both upstream suites **inside the new repo** to prove the lift: target ≥490 (sworker) + ≥163 (ganymede3 core) green. Anything less is a broken lift.

**Gate 0:** no test count regression from the donors. If a lifted test fails, the lift is wrong.

### A.2 Phase 1 — Substrate (L1)

Typed records per `data-model.md`. sqlite3, content-addressed, JCS canonicalization, bitemporal fields on every claim, ledger with hash chain.

**Gate 1:** canonicalization is spec-tested (JCS vectors); content-address idempotence (inserting the same record twice writes nothing); ledger chain verifies.

### A.3 Phase 2 — Compiler (L0)

6-stage deterministic pipeline per `ganymede3` ADR 003, plus heuristic mining and the artifact compiler (ADR 009).

**Gate 2:** `hermes-atlas/determinism.py` FULL EQUALITY **and** DELTA ISOLATION both green on the small corpus. A manifest with a Merkle root. `sovereign diff` between two identical compiles is empty.

### A.4 Phase 3 — Witness (L1 read)

`Witness(artifact_version, scope)` with the five hard constraints in `target-architecture.md` §7. This is the differentiator and it comes **before** the runtime, because a witness nobody has interrogated is not a demonstration.

**Gate 3:** every witness answer resolves to a char offset; `UNRESOLVED` requires an investigation record; the boundary object is present and non-empty on the test corpus.

### A.5 Phase 4 — Policy gateway + execution (L3, L2)

`sworker` PermissionEngine wired to the substrate; MCP tool port per the **2026-07-28** stateless spec (not the existing `sovereign-agent-stack` MCP server, which predates it); pluggable sandbox backend with the `(tool, argv, cwd, policy) → (result, audit-record)` contract, audit record emitted **even when the sandbox is a no-op**.

**Gate 4:** fail-closed tests — an unauthorized action cannot execute, and a degraded sandbox still produces an audit record.

### A.6 Phase 5 — Runtime (L4) + A2A edge (L5)

Agent loop over the witness. A2A v1.0 at the edge only: JWS-signed Agent Cards over RFC 8785 JCS, `A2A-Version` pinned at the boundary, 1.0-native output with 0.3 read support.

**Gate 5:** Researcher → Analyst → Operator loop end-to-end with provenance intact. A2A round-trip against a reference card. A signed card whose signature does not verify is rejected.

### A.7 Phase 6 — Console (L6)

Read-only static export over the artifact, donor = `ganymede3` ADR 009 + the existing Next.js artifact views. All 15 named surfaces are precomputed artifact views.

**Gate 6:** zero inference at view time; the console renders what the CLI reports, and disagreeing is a bug.

### A.8 Explicitly out of scope for v1

A2A *inside* the framework · Rust/PyO3 perf layer · CRDT multi-device sync · distributed/local-fanout inference · any network fetch in the default pipeline · A2A x402-style payment extensions.

---

## PART B — Minimal vertical slice

The honest definition: one command produces a versioned artifact, and three roles interrogate it with provenance intact.

```
corpus (small: ganymede3/corpus/sources, 4 files)
  → sovereign ingest
  → sovereign compile          # deterministic-first; model optional
      → sources, units, evidence, claims, entities, graph,
        heuristics, provenance, indexes, policies, manifest + Merkle root
  → sovereign witness          # instantiate at version V
      → "what does this corpus contain?"        → claims + char offsets
      → "what does it NOT contain?"             → UNRESOLVED set + investigation records
      → "what is contradicted?"                 → CONTRADICTED + both sides
      → "what heuristics emerge?"               → derived, with miner provenance
      → "what changed?"                         → bitemporal diff
      → "what uncertainty remains?"            → INSUFFICIENT/CONTESTED/UNVERIFIED
  → researcher interrogates witness
      → hypothesis with provenance
      → requests evidence → receives claims + provenance + gaps
      → forms typed PROPOSAL
  → analyst verifies            # deterministic tier first; NLI tier where needed
  → operator executes           # PermissionEngine; tool via MCP port
  → evidence + verification recorded
  → belief revised
  → sovereign verify            # gates: determinism, provenance, lifecycle, isolation
  → sovereign diff V1 V2        # what changed, what went stale
```

**What makes it a real demonstration and not "ask an LLM about my documents":** every answer terminates in a character offset in an identified source; absence is provable rather than inferred; the artifact is byte-reproducible; and the agent's authority is bounded by a policy gate that fails closed. Run it with the model server stopped — the deterministic tiers still produce a versioned, verifiable artifact. **That test is the demonstration.**

---

## PART C — Test strategy

### C.1 The invariant tests (these are the product)

| Invariant | Test |
|---|---|
| Determinism | FULL EQUALITY + DELTA ISOLATION from `hermes-atlas/determinism.py`, fail-loud |
| Content addressing | Insert-twice writes nothing; canonicalization matches JCS vectors |
| Ledger integrity | Chain verifies; any mutation fails verification |
| Provenance completeness | **No claim in the artifact without a resolvable source edge.** Walk every claim to a char offset. This is the one test that must never be relaxed. |
| Absence is provable | `UNRESOLVED` requires an investigation record; a fabricated absence fails |
| Evidence magnitude ≠ sufficiency | N tangential spans do not equal 1 direct span |
| No confidence-for-truth | No code path gates an epistemic state on a confidence number; grep-enforced + test |
| Model is untrusted | Fuzz the model with hallucinated spans; verifier rejects them |
| Fail-closed policy | Unauthorized action cannot execute; network is denied by default; unreachable provider → deterministic layer stands alone, never fabrication |
| Audit always emitted | Sandbox no-op still produces an audit record |
| Bitemporal isolation | A reconstruction "as of T" is uncontaminated by later knowledge — `TemporalAuthority` invariant verbatim |
| Witness boundary | Witness cannot answer outside its artifact version; boundary object non-empty |
| Heuristic traceability | Every derived heuristic resolves to its miner + input fingerprint; declared requires an author |
| Incremental correctness | Changed source → correct affected subgraph only; staleness report emitted; unchanged records unmoved |
| Offline | Full compile + witness + policy tests pass with the network disabled and the model server stopped |

### C.2 Capability-level tests

- **Provenance correctness** — offsets, staleness propagation, `source_checksum` invalidation cascade
- **Claim extraction** — precision/recall against a hand-labelled gold set
- **Contradiction detection** — the known `ganymede` failure (contradiction graph empty despite contradictory claims existing) is a **regression test, not a new bug**
- **Temporal validity** — `valid_time` vs `transaction_time` reconstruction
- **Heuristic derivation** — miner determinism; declared vs derived
- **Retrieval correctness** — BM25, embedding, graph, temporal, provenance, and the deterministic fusion
- **Policy/permission enforcement** — the `sworker` adversarial suite, plus MCP header-gated authorization
- **Tool execution + evidence generation + verification** — the `sworker` e2e path
- **Agent handoffs, A2A, MCP boundaries** — signed-card verification, `A2A-Version` pinning, 0.3 read compat
- **Artifact reproducibility** — pinned environment, four-tuple manifest, byte-identical rebuild
- **Incremental recompilation** — §C.1
- **Offline operation** — §C.1

### C.3 Evaluation discipline

Carried from `sovereign-agent-stack`:

- `ResearchWindow` / `HoldoutWindow` — measure on held-out corpus slices, never on what you built on
- `TemporalAuthority` invariants verbatim — *"AN EVENT'S OCCURRENCE DOES NOT IMPLY ITS KNOWABILITY"*
- DSR / PBO / CSCV — for any statistical claim about agent performance
- **Null-world and synthetic-signal controls** — from `quant/experiment/synthetic_worlds.py` and `signal_recovery.py`. A gate that cannot detect a planted signal cannot be trusted to reject noise. This is the *governed vs ungoverned* finding from the estate: in a null world the ungated agent manufactured a 3.64-Sharpe strategy from pure noise.
- **Absence of sufficient evidence → `INCONCLUSIVE`/`UNRESOLVED`, never fabricated certainty**

### C.4 The honest open problem

The W7 verifier gap is a **capability gap, not a threshold problem**, and the framework must be honest about it rather than tuning around it:

- Deterministic and hybrid verifiers (cross-encoder ms-marco, RRF) both measure *topical relevance*, not *answer containment*. Their score distributions fully overlap: answerable 0.007–0.032 RRF, answer-absent 0.006–0.032.
- NLI (roberta-large-mnli, meta-hypothesis) initial: 7% recall, 20/21 answer-absent.
- Only the LLM judge (qwen3:8b) reached 76% recall, 18/21 correct on absent.

**Design consequence, stated up front:** the system must never depend on the verifier being strong. Its correctness comes from **provenance and structure** — a claim is only as good as the source span it resolves to, which is mechanically checkable — not from a model judging whether an answer is contained in a passage. The NLI threshold sweep is an open research item, tracked, not load-bearing.

---

## PART D — What I did not do

Per the brief's own instruction ("start by inspecting the repositories and produce the architecture assessment and migration map before writing substantial new code"), this pass produced **documents only**. The repo `~/Projects/sovereign-runtime` currently contains `docs/architecture/` with four files and no code. Implementation starts at Part A.1, and **not before F1–F4 are ratified** — F1 (license) in particular blocks any vendoring.
