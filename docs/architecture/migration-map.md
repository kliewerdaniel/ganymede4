# Migration Map — existing repos → Sovereign Knowledge & Agent Runtime

**Status:** Draft for ratification
**Date:** 2026-09-27
**Scope:** Every repository named in the brief, plus the ones actually found on disk during inspection.
**Method:** Direct source reading + test execution. Where a claim below is about "does it work", the test run is cited. Where I could not verify, it says so.

---

## 0. Method and honesty note

Three of the repos in the brief were specified by name but **two of them were not where the brief implies**:

| Named in brief | Found at | Note |
|---|---|---|
| `authority-epistemic-protocol` | `~/authority-epistemic-protocol` | Home dir, not `~/Projects/`. Next.js/TypeScript, not Python. |
| `Sovereign Worker` | `~/Documents/Projects/sovereign-worker` | Not in `~/Projects/`. Package name `sworker`. |
| `pagerank-seo` | `~/Projects/pagerank-seo` | Confirmed. |
| `Sovereign Agent Stack` | `~/Projects/sovereign-agent-stack` | Confirmed. **A second, unrelated 20k-LOC copy also exists at `~/sovereign-agent-stack`** (same LICENSE, different commit history, `README` says "rewrite README to reflect actual project state"). Two checkouts, not a subset relationship. |

**One name collision that will bite later:** there are two unrelated projects both called *knowledge-compiler-sdk*.
`~/Projects/knowledge-compiler-sdk` is a **JavaScript/Node CLI** (`@kliewerdaniel/discoverable-knowledge-sdk`, `bin/kc.js`, static-site generator, zero Python).
`~/knowledge-compiler-sdk` is the **Python** 9-pass compiler SDK with the IR schemas and `ArtifactStore`.
This must be disambiguated in every doc that mentions "the SDK".

Test-run evidence gathered for this document:

| Repo | Command | Result |
|---|---|---|
| `ganymede3` | `python3 -m pytest tests/claim_graph tests/index tests/query tests/extraction tests/test_compiler.py tests/test_artifact_ir.py tests/test_policy.py` | **163 passed in 1.10s** |
| `ganymede3` | `python3 -m pytest tests/` (full) | **TIMEOUT >180s** — the inference-dependent tests block on a live Ollama server |
| `sworker` | `env -u PYTHONPATH -u PYTHONHOME /opt/homebrew/bin/python3 -m pytest tests/ -q` | **490 passed in 44.94s** |
| `sovereign-agent-stack` | `pytest -q` on a `/tmp` venv, market-data tests excluded (they make live network calls to stooq/yfinance and hang) | **3,218 passed, 1 failed, 21.4s** — 3,289 tests collected. The single failure is `test_undocumented_dependency_detected`, a self-referential test asserting its own auditor finds undocumented deps; it finds none because the ad-hoc venv differs from the declared one. Environment artifact, not a defect. |
| `ganymede2` | `./.venv/bin/python -m pytest tests` | **TIMEOUT** — `.venv` has Python 3.11 but `fastapi` is not installed |

---

## 1. Current architecture assessment

### 1.1 What actually exists, working, and tested

**`sworker` (Sovereign Worker) — the strongest working execution fabric.**
12,588 LOC across `sworker/*.py`, 61 test files, **490 tests green in 45s**, MIT licensed, stdlib-only. This is the only repo in the estate with a *large*, *fast*, *hermetic* test suite. Load-bearing pieces, all present and tested:

- `sworker/permissions.py:333 PermissionEngine` — evaluates `(tool, args) → Decision`. Backed by a real **AST risk classifier** (`:102 _PythonRiskVisitor`, `:227 classify_python`, `:256 classify_shell`, `:282 classify`) and a `:304 DecompositionGuard` that blocks task-splitting evasion.
- `sworker/evidence.py:31 EvidenceLedger` — `from_observation`, `note`, `claim`, plus `:96 score(provenance, evidence)` and `:119 _atlas_score`.
- `sworker/verify.py` — **13 deterministic, no-LLM check types**: `recompute_sum`, `recompute_delta`, `row_count`, `file_exists`, `artifact_contains_evidence`, `provenance_chain`, `totals_match_source`, `schema`, `set_equality`, `regex`, `doc_section`. This is the "re-derive the number from source, don't trust the model's assertion" property, already implemented.
- `sworker/policy.py:34 Policy` + `:68 PolicyStore` — content-hashed, versioned, `publish/get/latest/list_versions`.
- `sworker/statemachine.py` — `IllegalTransition`, `TransitionRecord`, validated lifecycle.
- `sworker/inference.py:138 Inference` (ABC), `OllamaProvider`, llama-server, and crucially **`:203 NullInference` plus `is_local()` fail-closed non-local refusal** (`:190 from_env(allow_external=False)`).
- `sworker/knowledge.py` — a **deliberate bridge to `hermes-atlas`**, with an explicit degraded-grep fallback that is *labelled degraded and never silently fabricated*. This is the exact integration discipline the new repo needs.

**`hermes-atlas` — the cleanest small compiler, and the most under-credited asset here.**
4,684 LOC across 17 modules, 18 test files, MIT. The module that matters most is `hermes_atlas/determinism.py`, whose docstring states the thesis better than any roadmap:

> *"A system whose 'reproducible build' can drift between two runs of identical input is not reproducible — it is probably reproducible, which is the most dangerous kind of broken."*

It proves determinism **two ways**: (1) FULL EQUALITY — compile the same sources into two fresh stores, require byte-identical fingerprints; (2) DELTA ISOLATION — recompiling an unchanged slice must not move any existing record, and the changelog must stay silent. Both fail-loud. Also present: `ledger.py` (append-only changelog), `explain.py` (`explain_claim` / `format_human`), `coverage_gaps.py`, `gaps.py`, `canary.py`, `personas.py`, `contradictions.py`, `confidence.py`, `lifecycle.py`.

**`ganymede3` — the deepest epistemic design, with a duplication defect.**
9,058 LOC of Python, 163 fast core tests, 11 ADRs. The design is genuinely the best in the estate:

- **ADR 002 — 8-state claim lifecycle**: `UNEXAMINED / SUPPORTED / VALIDATED / CONTESTED / CONTRADICTED / INSUFFICIENT / UNRESOLVED / RETRACTED`. ADR 002's own *Context* section is the sharpest critique of the alternatives in the estate: *"Atlas conflates 'no evidence found' with 'evidence insufficient' — both are handled by confidence thresholds, not lifecycle states."*
- **ADR 011 — negative knowledge as four distinct first-class states**, with the taxonomy table distinguishing "I looked and didn't find anything" (`UNRESOLVED`, requires an investigation record proving a search happened) from "I found something but not enough" (`INSUFFICIENT`) from "the corpus says the opposite" (`CONTRADICTED`) from "used to know this" (`RETRACTED`).
- **ADR 008 — append-only epistemic ledger as a DAG**: 30+ event types across Corpus/Extraction/Claims/Contradictions/Investigations/Inference/Artifacts/System, each with `event_id`, `actor`, `operation`, `inputs`, `outputs`, parent links.
- **ADR 007 — agents are proposal-only**: `AgentProposal` carries `agent_id`, `agent_version`, `operation`, `inputs`, `outputs`, `rationale`, `confidence`, `policy_version`, `model`. `engine/policy.py` implements `AGENT_AUTHORIZATION` as a per-role operation allowlist and returns `ACCEPTED / REJECTED / NEEDS_REVIEW / DEFERRED`. *"No agent can directly mutate the Evidence Graph or Epistemic Graph."*
- **ADR 006 — inference sovereignty ladder**: `engine/inference.py` (754 LOC) with `InferenceProvider` Protocol, `OllamaProvider`, `OpenAICompatProvider`, and an `InferenceResult` carrying `input_hash`, `output_hash`, `provider`, `model`, `sovereignty_crossed`. Explicit rule: *"Model output is UNTRUSTED data"* and *"Inference is compile-time only and never required for compilation."*
- **ADR 009 — two-phase artifact compiler**: Epistemic Graph → Artifact IR → statically-exportable Next.js. No inference at view time.
- Content-hash gating everywhere: `_compute_content_hash` in `engine/store_evidence.py`, `source_checksum` on every evidence unit specifically for staleness detection, per-record `history[]`.

**Defect — `ganymede3` contains two competing claim models.** This is the single most important thing to resolve in migration:

- `engine/database.py:262 ClaimRecord` (SQLAlchemy, Postgres/JSONB) — `status` is the **9-value** ADR-002 string; edges live in a separate `contradictions` table; storage is `content_hash`-gated with `history[]`.
- `claim_graph/graph.py:80 Claim` (raw `sqlite3`, dataclass) — `lifecycle` is a **different 5-value** enum (`PROPOSED / VERIFIED / CONTRADICTED / DEPRECATED / MERGED`); relationships are a **different 5-value** `EdgeType` enum (`SUPPORTS / CONTRADICTS / ENTAILS / TOPIC_OVERLAP / GENERALIZES`); storage is a separate `claim_graph/claims.db`.

These are not reconciled anywhere I could find. The same repo has two incompatible epistemic vocabularies and two incompatible graph edge vocabularies living side by side, connected only by a `claim_id` string in `EvidenceUnitRecord.claim_id`. **Any framework built on top of this must pick one and delete or subsume the other.** This is the concrete, verifiable justification for the "evolution rather than rewrite" framing being necessary rather than optional.

**`sovereign-intelligence` — the richest memory semantics, no license.**
18,524 LOC, 35 test files, 604 tests claimed in the skill notes (I could not execute them — `fastapi` missing, subagent timed out). The memory layer is the most complete in the estate:

- `memory/hypothesis.py` — hypothesis lifecycle `ACTIVE → WEAKENED / CONFIRMED / FALSIFIED / DEPRECATED`, each transition an explicit evidence-linked operation.
- `memory/credibility.py` — `SourceCredibilityTracker`, per-source score in [0,1] derived from *outcomes of claims attributed to it* ("claims that get confirmed boost credibility; claims that get falsified reduce it; sources with more evidence get more stable scores").
- `memory/temporal.py` — `snapshot_at(timestamp)`, `entity_history`, `diff(t1, t2)`, `causes`, `timeline` over an event log.
- `memory/audit_store.py` — SQLite-persisted hash chain with `chain_order`, `predecessor_hash`, `entry_hash`, and `verify_plan(run_id) → (ok, reason)`. Chains survive process restart.
- `core/artifact_store.py`, `core/provenance.py`, `core/event_log.py`.
- `execution/verification.py` — 5-strategy verification (cross-reference, provenance, credibility, logical, temporal) with weighted aggregation.

**`authority-epistemic-protocol` — the exact vocabulary the brief asks for, in TypeScript.**
This repo is the source of the brief's epistemic state list. `src/schemas/types.ts:4`:
```
EpistemicState = VERIFIED | SUPPORTED | CONTRADICTED | INCONCLUSIVE | STALE | UNVERIFIED
EvidenceRelationship = SUPPORTS | CONTRADICTS | QUALIFIES | SUPERSEDES | DERIVES_FROM
GraphNodeType = CLAIM | EVIDENCE | AUTHORITY | SOURCE | IDENTITY | ARTIFACT
```
And `src/lib/epistemic/artifact.ts` has `createEpistemicArtifact(claim, authorityResolution, evidenceSummary, verificationResult, boundedAssertion)` producing a **canonicalized, hashed** artifact with a `provenance[]` chain. Its stated thesis is the same negative-architecture thesis:

> *"An authentic authority artifact can still contain a false assertion. Identity integrity ≠ claim truth."*

**`right-to-compute` — the only Apache-2.0 repo, and the best lifecycle.**
8,427 LOC, 9 test files, ADR-driven, 11 protocol objects as versioned JSON schemas in `protocol/v0/`: `Node, Capability, Workload, Authorization, Execution, Evidence, Verification, Settlement, Voucher, AuditRecord`. Lifecycle:
`REQUEST → INTENT → PLAN → SCHEDULING → AUTHORIZATION → DISPATCH → EXECUTION → OBSERVATION → EVIDENCE → VERIFICATION → SETTLEMENT → ARTIFACT → AUDIT`.
The settlement gate is stated as a fail-closed triple: *accepted verification + recorded evidence + no double-settle*. `rtc/sandbox/{container,inference,subprocess}` is a pluggable sandbox seam.

**`sovereign-agent-stack` — the sovereignty scoring model and the evaluation methodology. The best-tested code in the estate.**
~180k LOC claimed (much of it `src/sas/quant`), **3,289 tests collected, 3,218 passing in 21.4s** — a bigger and faster suite than `sworker`'s 490. Its `tests/adversarial/` directory is notable: it attacks the framework's own auditors rather than asserting their happy paths. Relevant:
- `src/sas/layers/model.py` + `model_providers.py` — `ModelProvider` Protocol with `OllamaProvider`, `OpenAIProvider`, `StubModelProvider`; `ModelIdentity` dataclass. The comment is right: *"This layer is a commodity. The intelligence is commoditized; the rent has moved up."*
- `src/sas/runtime/mcp_server.py` — 596 LOC, real `MCPServer` with `register_tool` / `list_tools` / `call_tool` / `serve` / `handle_request`, 8 tools including `_tool_query_knowledge` and `_tool_quant_provenance`. This is a **working MCP server today**, which is more than any other repo here has.
- `src/sas/quant/research/temporal.py:37 ResearchWindow`, `:95 HoldoutWindow`.
- `src/sas/quant/experiment/temporal_authority.py` — 14-line invariant header worth stealing verbatim: *"AN EVENT'S OCCURRENCE DOES NOT IMPLY ITS KNOWABILITY"* and *"A CURRENT RECONSTRUCTION MUST NOT BECOME A RETROACTIVE RECONSTRUCTION."*
- `src/sas/quant/statistics/{pbo,deflated_sharpe,cscv}.py` — the DSR/PBO machinery.
- `src/sas/quant/experiment/{signal_recovery,synthetic_worlds}.py` — null-world and synthetic-signal controls, the honest way to test whether a gate can actually detect signal.
- **No A2A implementation exists anywhere in this repo** (`grep -rl "a2a" src/sas --include=*.py` → empty).

**`knowledge-compiler-sdk` (Python, at `~/`) — the pass/IR scaffolding.**
MIT. `compiler/core/{registry,orchestrator,artifacts,diagnostics,evaluation,inference,llm_pass}.py` plus six IR families (`application / graph / markdown / ontology / reasoning / semantic`) each with a JSON Schema in `schemas/`. `ArtifactStore` persists every compiled batch as an **immutable, content-hashed artifact**. Deterministic passes run with no model; model passes go through `core.llm_pass.run_model_pass` against a local OpenAI-compatible server. Two documented traps in the skill notes worth carrying forward: evaluation scores >1.0 when a raw co-occurrence count is named `weight`, and the concept universe must come from frontmatter tags, not section headings.

**`sovereign-knowledge-compiler` — compile-time memory with a proven local run.**
MIT, 3,163 LOC, 5 test files. Real result: **153 of the author's own blog posts compiled locally with `llama3.1:8b` into 1,513 facts and 436 decisions**, rendered as a static 3D graph site. Carries the CRDT sync layer, decay/compaction, local-LLM deep synthesis, and the synthesis→decay reinforcement loop.

**`authority-graph-compiler` — structural authority, correctly disclaimed.**
3,955 LOC. `graph.py` computes standard and weighted PageRank and says the right thing in its own docstring: *"PageRank values are raw structural metrics — NOT truth scores."* `claims.py` does rule-based claim extraction and explicitly does not call an LLM for core verification. **But `crawler.py` fetches URLs** — that is architecturally incompatible with sovereign-by-default unless network becomes an explicitly-governed capability.

**`pagerank-seo`** (6,436 LOC, MIT) — same structural-authority idea, no epistemic claims. Reference-only.

### 1.2 What does NOT exist anywhere in the estate

These are the brief's actual novelties. Verified by targeted search:

1. **Heuristics as a first-class object.** `grep -rin "heuristic"` across the knowledge repos returns nothing load-bearing. No repo has a `Heuristic` type, no derived-vs-declared distinction, no heuristic provenance.
2. **The witness as a formal primitive.** `grep -rin "witness"` in `ganymede3` returns exactly one hit — `engine/investigation.py:157`, `"Identify witnesses or participants who could confirm or deny"`, i.e. *interview candidates for a research plan*. In `ganymede` the word appears once in `api/app/services/query_expansion.py:47` as a **query synonym** (`"witness", "witness testifier deponent"`) and in a hand-written `QUESTIONS` list in `api/interrogate_corpus.py`. **The witness is currently a metaphor and a search-expansion trick, not a primitive.** It must be built.
3. **Beliefs as a distinct object.** `sovereign-intelligence/memory/hypothesis.py` is the closest thing, but a hypothesis is a *claim under test*; a belief is a *held epistemic state* with `supersedes`/`superseded_by` and `last_verified`. No repo has the belief type.
4. **Temporal validity intervals on claims.** `sovereign-intelligence/memory/temporal.py` does `snapshot_at()` over an event log — that is *replay*, not *validity windows*. No repo has `valid_from` / `valid_until` on a claim. (Graphiti/Zep do, per the ecosystem survey; this is the gap.)
5. **A2A.** Zero implementations. Only MCP exists, and only in `sovereign-agent-stack`.
6. **Incremental dependency tracking with invalidation cascade.** `ganymede3/pipeline/run.py` has `is_processed(source_ids)` / `mark_processed(...)` — a *batch skip list*, i.e. incrementality at the extraction level only. There is no dependency graph saying "this source changed → these claims stale → these heuristics stale → these beliefs stale → regenerate these indexes → rerun these evaluations". The brief's §10 is genuinely unbuilt.
7. **Hash-chained knowledge provenance.** Content hashes exist per record (`ganymede3`), canonicalization+hash exists (`authority-epistemic-protocol`), hash-chained audit exists (`sovereign-intelligence`, `sworker`). But nothing chains a *claim's full derivation* (claim → evidence → source → corpus version → compiler version → model hash) as a verifiable linked structure. The ecosystem survey confirms this is white space industry-wide.
8. **The brief's exact belief vocabulary.** See §3.1 — and it is *weaker* than what already exists.

### 1.3 Three incompatible epistemic vocabularies in the estate

| Source | States | Distinguishes "looked, found nothing" from "found too little"? |
|---|---|---|
| `ganymede3` ADR 002/011 | `UNEXAMINED, SUPPORTED, VALIDATED, CONTESTED, CONTRADICTED, INSUFFICIENT, UNRESOLVED, RETRACTED` | **Yes — explicitly, and it is the ADR's stated reason for existing** |
| `authority-epistemic-protocol` | `VERIFIED, SUPPORTED, CONTRADICTED, INCONCLUSIVE, STALE, UNVERIFIED` | **No** — collapses both into `INCONCLUSIVE` |
| `sovereign-intelligence` hypothesis | `ACTIVE, WEAKENED, CONFIRMED, FALSIFIED, DEPRECATED` | Partially — no "searched and found nothing" |

Plus a *fourth* inside `ganymede3` itself (`claim_graph.ClaimLifecycle`, 5 states, §1.1 defect).

---

## 2. The push-back: the brief's belief vocabulary is a regression

The brief §4 specifies belief states:
`SUPPORTED · PARTIALLY_SUPPORTED · CONTRADICTED · UNVERIFIED · INCONCLUSIVE · STALE · DERIVED · ASSUMED`

These are almost exactly `authority-epistemic-protocol`'s six plus `PARTIALLY_SUPPORTED`/`DERIVED`/`ASSUMED`. They are **strictly less expressive than `ganymede3` ADR 002 + ADR 011**, which already exists, is already ADR-ratified, and whose entire justification was that collapsing these states is a defect.

Concretely, the brief's vocabulary cannot express:

- *"I searched for this and it is not in the corpus"* (`UNRESOLVED`) — it falls into `UNVERIFIED` or `INCONCLUSIVE`, which do not record that a search occurred. **This is the falsifiability hole.** If a witness cannot prove it looked, it cannot prove absence, and "not in the corpus" becomes unfalsifiable.
- *"There is some evidence but it is too weak to stand"* (`INSUFFICIENT`) — collapses into `PARTIALLY_SUPPORTED`, which is arguably fine, but ADR 011's point is that this is the state that should *trigger a proposed investigation*, and collapsing it loses the trigger.
- *"This was established and the evidence has since been removed"* (`RETRACTED`) — collapses into `CONTRADICTED` or `STALE`, both of which are wrong. Retraction is DELETE-driven; contradiction is evidence-driven. They demand different recovery actions.

**Recommendation:** adopt `ganymede3` ADR 002/011 as the claim-state spine — it is strictly stronger and already ratified — and treat the brief's list as a *superset proposal* to be resolved in a new ADR, not as the starting vocabulary. `DERIVED` and `ASSUMED` are genuinely new and should be **added**; `PARTIALLY_SUPPORTED` should be reconciled against `CONTESTED` and `INSUFFICIENT`.

This matters beyond bookkeeping: the brief's own §"Testing and Evaluation" says *"absence of sufficient evidence should produce INCONCLUSIVE rather than fabricated certainty."* That instinct is right, and ADR 011 already implements a sharper version of it. Take the sharper one.

---

## 3. Concept mapping

| Existing concept | Home repo | Target role in the new system | Verdict |
|---|---|---|---|
| Evidence Graph (sources, structural units, evidence units, contradictions) | `ganymede3` `engine/store_evidence.py` | The immutable provenance root. Episode layer. | **PRESERVE** |
| Epistemic Graph (claims, lifecycle, confidence terms, history) | `ganymede3` `engine/store_epistemic.py` | Versioned claim store. | **PRESERVE** |
| 8-state claim lifecycle | `ganymede3` ADR 002 | The claim-state spine. | **PRESERVE** |
| Negative knowledge taxonomy | `ganymede3` ADR 011 | Four distinct states, investigation-backed. | **PRESERVE** |
| Epistemic ledger (DAG of 30+ events) | `ganymede3` ADR 008 | System-wide causal history. | **PRESERVE** |
| Agent proposal schema + role allowlist | `ganymede3` ADR 007 / `engine/policy.py` | The only sanctioned path from agent to state. | **PRESERVE** |
| Inference sovereignty ladder | `ganymede3` ADR 006 | Model adapter (needs the ecosystem survey's four additions) | **ADAPT** |
| Evidence-span verifier (substring + bounds) | `ganymede3` `extraction/verifier.py` | Compile-time claim gate. | **ADAPT** — replace Jaccard topicality with NLI entailment |
| Artifact IR → static export | `ganymede3` ADR 009 | The web console's data source. CLI stays authoritative. | **ADAPT** |
| Determinism gate (full equality + delta isolation) | `hermes-atlas` `determinism.py` | The artifact reproducibility gate. | **PRESERVE** — non-negotiable, lift nearly verbatim |
| Append-only changelog | `hermes-atlas` `ledger.py` | Compile-time change record. | **PRESERVE** |
| `explain_claim` / `format_human` | `hermes-atlas` `explain.py` | `sovereign explain`. | **PRESERVE** |
| Coverage gaps / research gaps | `hermes-atlas` `coverage_gaps.py`, `gaps.py` | Typed `gap` edges in the graph. | **PRESERVE** |
| `PermissionEngine` + AST risk classifier + `DecompositionGuard` | `sworker` | The policy gateway. | **PRESERVE** — this is the best-tested safety code in the estate |
| `EvidenceLedger` | `sworker` `evidence.py` | Evidence from *execution* (distinct from compile-time evidence). | **PRESERVE** |
| 13 deterministic no-LLM verifiers | `sworker` `verify.py` | The verifier tier. | **PRESERVE** |
| Content-hashed versioned `PolicyStore` | `sworker` `policy.py` | Policy versioning. | **PRESERVE** |
| Lifecycle state machine | `sworker` `statemachine.py` | The run state machine. | **PRESERVE** |
| `NullInference` + fail-closed `is_local()` | `sworker` `inference.py` | Non-negotiable sovereignty default. | **PRESERVE** |
| Hermes Atlas bridge w/ labelled degradation | `sworker` `knowledge.py` | The integration *pattern* for every optional dependency. | **PRESERVE as pattern** |
| `MCPServer` (596 LOC, 8 tools, working) | `sovereign-agent-stack` `src/sas/runtime/mcp_server.py` | The MCP tool boundary. | **ADAPT HARD** — it predates the 2026-07-28 stateless spec |
| `ModelProvider` Protocol + `ModelIdentity` | `sovereign-agent-stack` `layers/model*.py` | Model adapter base. | **ADAPT** — needs GGUF-hash identity, seed, grammar, backend version |
| Sovereignty layer scoring (8 layers, owned vs rented) | `sovereign-agent-stack` `sas.yaml` + dashboard | A first-class *product surface*: "how much of this do you actually own?" | **PRESERVE** |
| `ResearchWindow` / `HoldoutWindow` | `sovereign-agent-stack` `quant/research/temporal.py` | Evaluation harness for retrieval and agent claims. | **PRESERVE** |
| `TemporalAuthority` invariants | `sovereign-agent-stack` `quant/experiment/temporal_authority.py` | Directly applicable to corpus reconstruction. | **PRESERVE verbatim** |
| DSR / PBO / CSCV | `sovereign-agent-stack` `quant/statistics/` | Guarding against overfit claims. | **ADAPT** — only for statistical claims about agents, not knowledge claims |
| Null-world / synthetic-signal controls | `sovereign-agent-stack` `quant/experiment/synthetic_worlds.py` | The honest way to test whether a gate can detect anything at all. | **PRESERVE** |
| Hypothesis lifecycle | `sovereign-intelligence` `memory/hypothesis.py` | Adjacent to Belief but distinct. | **ADAPT** → keep separate: Hypothesis = claim under test; Belief = held state |
| Source credibility tracker | `sovereign-intelligence` `memory/credibility.py` | Feeds heuristic derivation (source reliability patterns). | **ADAPT** |
| `snapshot_at` / `diff` / `causes` over event log | `sovereign-intelligence` `memory/temporal.py` | Corpus reconstruction. | **ADAPT** — needs to read the ledger, not a separate log |
| Hash-chained `AuditStore` | `sovereign-intelligence` `memory/audit_store.py` | The ledger's persistence layer. | **PRESERVE** |
| Provenance-chain verification | `sworker` `verify.py:_provenance_chain` | Compile-time provenance check. | **PRESERVE** |
| Epistemic state enum (6) + evidence relations (5) | `authority-epistemic-protocol` | Superset candidate. | **SUPERSEDED** by ADR 002/011 — see §2 |
| `createEpistemicArtifact` + canonicalize + hash | `authority-epistemic-protocol` | Artifact serialization shape. | **ADAPT** — port to Python, keep the hash-over-canonical-form idea |
| `BoundedAssertion`, `AuditEvent` schemas | `authority-epistemic-protocol` `schemas/resolution.ts` | Agent output envelope. | **ADAPT** |
| Protocol objects (11) + 13-stage lifecycle | `right-to-compute` | The A2A/execution wire vocabulary. | **ADAPT** |
| Pluggable sandbox seam (`container/subprocess/inference`) | `right-to-compute` `rtc/sandbox/` | Sandbox backend contract. | **ADAPT** — per survey, contract is `(tool, argv, cwd, policy) → (result, audit-record)` |
| Fail-closed settlement triple | `right-to-compute` | The pattern for the verify gate. | **PRESERVE as pattern** |
| 9-pass compiler + IR schemas | `knowledge-compiler-sdk` (Python) | Pass registry, IR, `ArtifactStore`. | **ADAPT** — rename the concept universe, avoid the two-SDK collision |
| `ArtifactStore` (immutable, content-hashed) | `knowledge-compiler-sdk` | Artifact versioning. | **ADAPT** — merge with `hermes-atlas` determinism semantics |
| 153 posts → 1,513 facts + 436 decisions, local, static site | `sovereign-knowledge-compiler` | Proof the compile-to-static path works at personal scale. | **PRESERVE as reference run** |
| CRDT sync + decay/compaction | `sovereign-knowledge-compiler` | Multi-device corpus convergence. | **DEFER** — not needed for v1, keep out of the spine |
| PageRank as structural authority (explicitly *not* truth) | `authority-graph-compiler`, `pagerank-seo` | Optional `structural` signal on entities/sources. | **REFERENCE-ONLY** — must never be mixed into epistemic confidence |
| HTML crawler | `authority-graph-compiler` `crawler.py` | — | **DROP** — network must be an explicitly governed capability |
| Static knowledge-site generator (`kc` JS CLI) | `Projects/knowledge-compiler-sdk` (JS) | Optional export target. | **REFERENCE-ONLY** |
| Postgres + pgvector + FastAPI stack | `ganymede` `api/` | — | **DEPRECATE** for the framework (keep the running instance as a demo) — heavyweight, and the framework should be stdlib-first |
| Book manuscript / `interrogate_corpus.py` question list | `ganymede` | — | **REPLACE** with the real witness primitive |
| 8-layer plugin registry | `sovereign-agent-stack` `src/sas/plugins.py` | Extension mechanism. | **ADAPT** — only if the framework needs plugins; v1 can skip |
| Rust core + PyO3 bridge + pure-Python fallback | `sovereign-agent-stack` `rust_bridge/` | — | **DEFER** — premature. The pure-Python-fallback-with-identical-API pattern is worth keeping in mind for a later perf phase. |

### Repo-level verdicts

| Repo | Verdict | One-line justification |
|---|---|---|
| `hermes-atlas` | **PRESERVE — the compiler seed** | 4.7k LOC, MIT, the determinism gate is the most valuable single asset in the estate |
| `sworker` | **PRESERVE — the execution fabric seed** | 490 green tests, MIT, stdlib-only, real permission/evidence/verify/policy machinery |
| `ganymede3` | **PRESERVE + FIX — the epistemic design seed** | Best ADR set; has a real two-claim-models duplication defect that must be resolved first |
| `authority-epistemic-protocol` | **ADAPT — the vocabulary reference** | Source of the brief's state list; strictly weaker than ADR 002/011, so reference not spine |
| `right-to-compute` | **ADAPT — the protocol/lifecycle reference** | Only Apache-2.0 repo; 11 objects + 13-stage lifecycle is the cleanest wire vocabulary available |
| `sovereign-agent-stack` | **ADAPT selectively — the evaluation + sovereignty-score donor** | **3,218 tests green in 21s** — the best-tested code in the estate, and `tests/adversarial/` attacks the auditors rather than asserting happy paths. Still 180k LOC of which most is `quant/`, and the `sas.yaml` 8-layer model is product surface, not framework |
| `sovereign-intelligence` | **ADAPT selectively — the memory-semantics donor** | Best hypothesis/credibility/temporal/audit-chain code in the estate; no LICENSE file, and 604 tests unverified |
| `knowledge-compiler-sdk` (Python) | **ADAPT — the scaffolding donor** | Pass registry + IR + ArtifactStore; must be disambiguated from the JS SDK |
| `sovereign-knowledge-compiler` | **REFERENCE-ONLY** | Proof of the compile-to-static path at personal scale; CRDT/decay not needed in v1 |
| `authority-graph-compiler` | **REFERENCE-ONLY (structural authority only)** | Correctly disclaims PageRank-as-truth; the crawler is architecturally wrong for a sovereign default |
| `pagerank-seo` | **REFERENCE-ONLY** | Same |
| `Projects/knowledge-compiler-sdk` (JS) | **REFERENCE-ONLY** | Unrelated project with a colliding name |
| `ganymede` (v1) | **EXTERNAL DEMONSTRATION** | The 123,221-chunk / 4,819-source / 55,308-claim run and its Phase-0 audit are the best available real-corpus evidence — including the documented fabricated-narrative failure, which is the best available justification for the whole project |
| `ganymede2` | **SUPERSEDED by `ganymede3`** | `engine/` is byte-for-byte the same module set; `ganymede3` adds `claim_graph/ extraction/ index/ query/ pipeline/ adapters/`. No reason to keep both. |
| `sovereign-intelligence-stack` | **OUT OF SCOPE** | 1M LOC, mostly generated/vendored; find-the-core before any reuse |
| `sovereignspec`, `right-to-compute/ui`, `federation-hub`, `quiln`, `vorn`, `a10` | **NOT ASSESSED** | Outside the brief's scope; not inspected |

---

## 4. Licensing — a real blocker to resolve before any public release

The brief says "coherent **open-source** developer framework". Current state:

| Repo | License |
|---|---|
| `right-to-compute` | **Apache-2.0** |
| `sworker` | MIT |
| `hermes-atlas` | MIT |
| `sovereign-agent-stack` | MIT |
| `sovereign-knowledge-compiler` | MIT |
| `pagerank-seo` | MIT |
| `knowledge-compiler-sdk` | MIT |
| **`ganymede`, `ganymede2`, `ganymede3`** | **NO LICENSE FILE** |
| **`authority-epistemic-protocol`** | **NO LICENSE FILE** |
| **`authority-graph-compiler`** | **NO LICENSE FILE** |
| **`sovereign-intelligence`** | **NO LICENSE FILE** |

Every repo with the strongest epistemic design (`ganymede3`) and the exact vocabulary the brief requests (`authority-epistemic-protocol`) is unlicensed. All unlicensed works are the author's own, so this is a decision, not a legal obstacle — but it must be made **deliberately and in writing before any code is copied**, and it is a RATIFY-fork, not an implementation detail.

**Recommendation:** new framework under **Apache-2.0**, matching `right-to-compute`. Add explicit `LICENSE` files to `ganymede3` and `authority-epistemic-protocol` (Apache-2.0 or MIT) before vendoring from them.

---

## 5. What the new framework is *not*

Stated to prevent the most likely failure modes:

- It is **not** another RAG framework. It has no `top_k` and no answer-generation path in the spine. Retrieval is a mechanism over a governed substrate, not the product.
- It is **not** a chat UI with a knowledge backend. The CLI is authoritative; the console visualizes.
- It is **not** a rewrite of Ganymede. The epistemic spine is Ganymede's, with the duplication defect fixed and the brief's genuinely-new primitives (heuristics, witness, beliefs, validity intervals, dependency-tracked incrementality) added on top.
- It is **not** a place for the model to have authority. `sworker`'s `PermissionEngine` + `ganymede3`'s ADR 007 proposal-only rule + the 13 no-LLM verifiers are the whole point.

---

## 6. Open questions for ratification

1. **License** (§4). Apache-2.0 for the framework; add LICENSE to `ganymede3` and `authority-epistemic-protocol` first?
2. **Claim-state vocabulary** (§2). Adopt ADR 002/011 as the spine and add `DERIVED`/`ASSUMED`, or keep the brief's list and accept the falsifiability regression?
3. **`ganymede3` duplicate-model resolution.** Delete `claim_graph/` and consolidate on the SQLAlchemy/Postgres model, or delete the SQLAlchemy model and go stdlib-sqlite? *Recommendation: stdlib-sqlite, to keep the framework dependency-free and match `sworker`/`hermes-atlas` character — but this is a real fork.*
4. **Name.** The brief describes "Sovereign Knowledge and Agent Runtime"; the `sovereign` prefix is already used by at least six existing repos. Needs disambiguation.
5. **Corpus for the vertical slice.** The brief says "my personal corpus". The 123,221-chunk Ganymede run is the obvious candidate but it lives behind Postgres in `ganymede/api/`. The smaller `ganymede3/corpus/sources` (4 files, 16K) is a usable smoke corpus. Which is the v1 target?
