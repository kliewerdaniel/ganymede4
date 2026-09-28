# Sovereign Knowledge & Agent Runtime — Open-Source Ecosystem Survey
Date: 2026-09-27. Primary sources (specs, repos, papers) only; vendor marketing flagged as such.

---

## 1. MCP (Model Context Protocol)

**Current spec: `2026-07-28`** (https://modelcontextprotocol.io/specification/versioning) — *not* 2025-11-25, which is the previous revision. Most third-party blog posts in circulation still describe 2025-11-25; treat them as stale.

What changed in 2026-07-28 (https://modelcontextprotocol.io/specification/2026-07-28/changelog, https://blog.modelcontextprotocol.io/posts/2026-07-28/):
- **Handshake retired.** `initialize`/`initialized` and the `Mcp-Session-Id` header are gone (SEP-2575/2567). The protocol is now **stateless request/response**. Every request carries `_meta.io.modelcontextprotocol/protocolVersion`, `clientCapabilities`, `clientInfo`.
- **`server/discover` is a mandatory RPC** advertising supported versions, capabilities, identity. Optional for the client to call, mandatory for the server to implement.
- **Per-request version negotiation.** Unsupported version → `UnsupportedProtocolVersionError` (list of supported versions); retry. Same value mirrored into the `MCP-Protocol-Version` HTTP header.
- **Multi Round-Trip Requests (MRTR)** (SEP-2322) replaces server-initiated `sampling/createMessage`, `elicitation/create`, `roots/list`. Server returns `resultType: "input_required"` with pending questions; client **retries the original request** with answers. All results now carry required `resultType` (`complete` | `input_required`).
- **Header-based routing:** `Mcp-Method` and `Mcp-Name` HTTP headers required on Streamable HTTP — gateways can route/authorize without parsing bodies.
- **Cacheable lists:** `tools/list`, `prompts/list`, `resources/list`, `resources/read` carry `ttlMs` + `cacheScope` and deterministic ordering.
- **Error-code allocation policy:** −32000..−32019 implementation-defined; −32020..−32099 reserved for MCP.
- **Authorization hardening:** RFC 9207 `iss` validation mandatory; **DCR formally deprecated in favor of Client ID Metadata Documents (CIMD)**; credentials bound to issuing AS.

**Deprecated in 2026-07-28, earliest removal on/after 2027-07-28** (https://modelcontextprotocol.io/specification/2026-07-28/deprecated): **Roots, Sampling, Logging, DCR**. Sampling's stated migration path is "integrate directly with LLM provider APIs." HTTP+SSE transport deprecated since 2025-03-26.

**Transports:** two, and only two, standard bindings — `stdio` (newline-delimited JSON over a client-launched subprocess) and **Streamable HTTP** (one HTTP POST per message; reply as JSON object or request-scoped SSE). Custom transports permitted but MUST preserve JSON-RPC + message patterns + per-request metadata.

**Primitives (server):** tools, resources (+ resource templates, subscribe), prompts, completions, logging (deprecated), tasks (extension). **Primitives (client):** elicitation (via MRTR), roots (deprecated), sampling (deprecated). Utilities: progress, notifications, subscriptions/listen.

**Discovery/registration:** the **official MCP Registry is still in preview** — "breaking changes or data resets may occur before GA" (https://modelcontextprotocol.io/registry/about). It stores `server.json`, and defines an **OpenAPI spec** other registries can implement. Discovery is therefore a *metadata* problem, not a stable *lookup* problem. "Server Cards" / `.well-known` discovery is a 2026 roadmap item, not shipped spec.

**Clean way to use MCP as a boundary:** MCP is now essentially a stateless, cacheable, gateway-routable JSON-RPC-over-HTTP tool/resource protocol. That makes it a good *port* boundary for the runtime's tool surface: put your own compiled artifact behind a stateless MCP server, and the host can load-balance / cache / meter it for free. The `Mcp-Method`/`Mcp-Name` headers are the natural place to hang policy gating.

**Steal:** the `_meta` per-request capability/version pattern, the `resultType` explicit-completeness field, `ttlMs`/`cacheScope` on list results, the error-code partition, and the deprecation registry (12-month minimum window + published registry).
**Avoid:** building anything on Roots/Sampling/Logging — they are deprecated with a 2027 removal date. Don't design around sessions; the spec now punishes that. Don't depend on the registry as a stable source of truth.

---

## 2. A2A (Agent2Agent)

**Current version: 1.0.0**, released 2026-03-12; v1.0.1 on 2026-05-28. Previous: 0.3.0 (2025-07-30), 0.2.6, 0.1.0. (https://a2a-protocol.org/latest/specification/)

Under the Linux Foundation; 150+ orgs. v0.3.0 → 1.0.0 was an 8-month gap and a **975-line migration doc** — the wire format changed almost everywhere.

Key 1.0 changes (https://a2a-protocol.org/latest/whats-new-v1/):
- **Normative source is `spec/a2a.proto`.** All bindings (JSON-RPC, gRPC, HTTP+JSON) are mappings of that one model, serialized per ProtoJSON. Section 5.1 makes functional equivalence across bindings a **MUST**, not an aspiration.
- **`Part` unified:** `TextPart`/`FilePart`/`DataPart` and the `kind` discriminator are **removed**. One `Part` with a `oneof` (`text` | `raw` | `url` | `data`), plus `mediaType` and `filename`.
- **Agent Card restructured:** top-level `url`, `preferredTransport`, `additionalInterfaces`, `protocolVersion` **removed**, consolidated into `supportedInterfaces[]` (each entry has its own `url`, `protocolBinding`, `protocolVersion`). `supportsExtendedAgentCard` → `capabilities.extendedAgentCard`.
- **`role` enum values changed** from `"user"`/`"agent"` to `ROLE_USER`/`ROLE_AGENT`.
- Ops renamed: `message/send` → `SendMessage`, `tasks/get` → `GetTask`. **New: `ListTasks`** (cursor-based, not page-based). HTTP paths lost the `/v1` prefix.
- **Errors standardized on `google.rpc.Status`**; HTTP+JSON content type is `application/json`, not `application/problem+json`.
- **Signed Agent Cards** — cards MAY carry a `signatures[]` array of JWS (RFC 7515) over a payload canonicalized with **RFC 8785 JCS**. This is the real new trust primitive.

**Task/message model:** Task is stateful, client-created, with `id`, `sessionId`, `status`, `history`, `artifacts`. `TaskState`: `submitted`, `working`, `input-required`, `completed`, `canceled`, `failed`, `unknown`. Message has `role` + `Parts`. Three update mechanisms: streaming, `get`-based, and push notifications to a client webhook. Version negotiation is a single `A2A-Version` header; empty value must be treated as 0.3.

**Maturity — be skeptical:** the payment extension (x402) is still written against the 0.3 wire format and has not been migrated; every literal in it is stale (third-party audit, llm4agents.com). `application/a2a+json` is *not* IANA-registered — validators should treat it as vendor-specific. Agent Card signature verification is specified but not broadly implemented.

**How a framework should treat it:** as an **opaque-execution boundary only** — that's literally the spec's guiding principle ("without needing access to each other's internal state, memory, or tools"). Do not map your typed claim/entity/provenance model onto A2A's `Message`/`Part`. Use it for delegation between *other people's* agents. Pin the version at the edge, read `A2A-Version`, refuse to guess, and emit 1.0-native shapes (`ROLE_*`, member-discriminated parts, `statusUpdate` wrappers) with dual 0.3 read support.

**Steal:** proto-as-normative-source, the three-bindings-must-be-equivalent rule, JCS + JWS signed capability claims, single-header version negotiation, the Task state machine.
**Avoid:** the whole v0.3 wire shape; extension-ecosystem assumptions (the payment extension is proof this fails).

---

## 3. GraphRAG (Microsoft)

**What it is:** extract an LLM-built knowledge graph from raw text, run **Leiden community detection** to build a community hierarchy, generate per-community summaries, then query four ways (https://microsoft.github.io/graphrag/): Local Search (KG entities + raw text chunks), Global Search (map-reduce over *all* community reports — the library itself calls this "resource-intensive"), DRIFT Search (community-guided follow-up question expansion), and a "rudimentary" basic vector RAG included only for A/B comparison. Paper: arXiv:2404.16130.

**Cost:** indexing is the problem, and it is structural. GraphRAG issues LLM calls for entity/relationship extraction, then **another** LLM summarization pass per community, and community count scales superlinearly. Microsoft's own community post has to explain GraphRAG costs (https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/graphrag-costs-explained-what-you-need-to-know/4207978). A secondary writeup of the original paper estimates indexing at ~an order of magnitude more tokens than naive RAG. Global Search is map-reduce over every community report at query time — cost scales with corpus, not query.

**What it does NOT give you:**
- **No provenance.** Community reports are lossy LLM summaries. There is no claim→source edge you can audit. KGGen's comparison notes both GraphRAG and OpenIE do attach source chunks to relations, but the *community summary* layer discards it.
- **No temporal validity.** GraphRAG's own comparison table (in Graphiti's README) rates its temporal handling "Basic timestamp tracking." No validity windows, no invalidation, no "as of" queries.
- **No determinism.** LLM extraction + LLM summarization + map-reduce = three sources of nondeterminism baked in. Irreproducible across rebuilds.
- **No incremental build.** Reindex is the workflow. Static-corpus assumption throughout.

**Steal:** the community-hierarchy idea for *global/synthesis* questions (a question no chunk can answer), and the explicit multi-mode query interface — but as a query strategy over your own compiled graph, not as your compiler.
**Avoid:** making your compiler a GraphRAG-shaped pipeline. You cannot get provenance, temporal validity, or reproducible builds out of it, and all three are your stated goals.

---

## 4. LightRAG, HippoRAG, and dual-level retrieval

**LightRAG** (HKUDS, arXiv:2410.05779, EMNLP 2025 Findings). Core idea: **dual-level key-value retrieval.** Extract entities and relations into a schema-constrained graph, then store two parallel stores — *low-level* keys are specific entities, *high-level* keys are themes/relations — and **merge both levels' retrieved context** into one answer. Their own ablation: removing high-order retrieval (-High) causes "significant performance decline across nearly all datasets," because low-level-only over-focuses on entities and immediate neighbours. Claims better accuracy and efficiency than GraphRAG, and cheaper because it skips the community-summarization layer entirely.

**Limitation:** provenance is a `source_id` field, not a hash-chained evidence object. No temporal validity — the graph is overwritten in place on re-ingest. No versioned artifact. Query quality still depends on LLM-based merge and merge is order-dependent.

**HippoRAG** (OSU-NLP, NeurIPS 2024; HippoRAG 2 as `ianliuwd/HippoRAG2`). Core idea: **RAG + KG + Personalized PageRank.** Build a KG, connect passages as nodes via LLM-extracted entity mentions, then seed PPR from the query's retrieved passages. Inspiration is human associative memory, and it explicitly enables *continuous* knowledge integration across documents.

**Limitation:** PPR traversal scores are not evidence. There is no claim object, no validity interval, no stable identity for a fact across ingests. Incremental update is a research contribution, not a guaranteed-content-addressed property.

**Neo4j GraphRAG** (`neo4j-graphrag-python`): a thin library of retriever/builder components (VectorCypherRetriever, KG builder, `LLMGraphTransformer`) over Neo4j. It is a *retrieval toolkit*, not a compiler. It inherits Neo4j as a hard dependency and has no artifact/versioning concept.

**Shared limitation of all three:** they are **retrieval strategies over a mutable graph**. None of them is a deterministic compiler producing a versioned, inspectable artifact. None can answer "why do you believe this, and was it true then?" That gap is exactly the project's differentiator.

**Steal:** LightRAG's two-level key scheme (specific + thematic) as a *query-time* strategy over your compiled artifact; HippoRAG's PPR as a cheap graph-expansion operator; DRIFT-style iterative question decomposition.
**Avoid:** adopting any of them as the compile target. They answer "what should I put in the prompt," not "what is true, typed, versioned, and attributable."

---

## 5. Graph memory systems & the temporal KG data model

**Graphiti / Zep** (getzep/graphiti, arXiv:2501.13956) — the most directly relevant prior art. This is the reference implementation of a *bitemporal* knowledge graph:
- **Episodes** = raw ingested data. The ground-truth stream. *Every derived fact traces back to an episode.*
- **Entities (nodes)** with **summaries that evolve over time**.
- **Edges/facts with validity windows** — when the fact became true and when (if ever) it was superseded. Contradiction handling is *automatic fact invalidation with history preserved*, not LLM judgment.
- **Hybrid retrieval:** semantic + keyword + graph traversal, typically sub-second (vs GraphRAG's "seconds to tens of seconds").
- **Prescribed vs learned ontology:** Pydantic-defined entity/edge types, or let structure emerge.
- Incrementality is architectural: "without requiring complete graph recomputation."
- Zep's *paper* benchmarks beat MemGPT on DMR (94.8% vs 93.4%) and gain up to 18.5% on LongMemEval at 90% lower latency — but that engine is **proprietary**. Graphiti is the OSS layer; the graph DB is pluggable (Neo4j, FalkorDB, Kuzu).

**Mem0** (arXiv:2504.19413) — extract-and-update memory: an LLM decides ADD/UPDATE/DELETE/NOOP against a memory store, plus a graph variant. Simple, strong on its benchmarks. Limitation: memory items are flat strings; the "update" operation is an LLM decision, so history is lossy and non-reproducible.

**Letta / MemGPT** — the *agent server* approach: memory is core agent architecture (core memory blocks, archival memory, recall memory, sleep-time compute). The control plane, not just memory. Limitation: heavyweight; you adopt their agent loop, and the data model is tuned for one agent's context window rather than a corpus artifact.

**cognee** (topoteretes/cognee, arXiv:2505.24478) — graphs, custom ontologies, and a `recall`/`improve`/`forget` lifecycle. Notable and worth stealing: the **COGX exchange format** for migrating memory between Mem0/Letta/Zep/Graphiti, and first-class **local Ollama** support including a local embedding model. Limitation: same class as above — mutable, no versioned artifact, no claim-level provenance chain.

**The actual data model for temporal KGs** (this is the reusable core):
```
Entity      — stable identity + evolving summary
Episode     — content-addressed raw input; root of provenance
Fact/Edge   — (subject, predicate, object) + valid_time interval
              + transaction_time interval  ← bitemporal
              + provenance → Episode(s)    ← lineage
Invalidation — supersedes, never deletes
```
Bitemporal (both *when it was true* and *when we learned it*) is the load-bearing distinction and the thing most systems get wrong by keeping only one axis. Graphiti calls its tracking "explicit bi-temporal." Note the terminology inconsistency across vendors: Graphiti/Zep say "bitemporal"; most engines (Kuzu, Neo4j) ship no first-class temporal layer — you implement the intervals yourself.

**Steal:** episode-as-provenance-root, validity intervals, **invalidation-not-deletion** as the update policy, prescribed-or-learned ontology, hybrid retrieval, and the claim that incremental update must not require recomputation (make that an architectural invariant).
**Avoid:** letting an LLM decide fact updates in the store (Mem0's pattern) — it destroys determinism and reproducibility. Avoid the proprietary-graph-DB tier (Zep's engine). Avoid adopting an agent-server framework's memory model as your artifact model.

---

## 6. Provenance-aware RAG / claim-level attribution

**The benchmark: ALCE** (arXiv:2305.14627, EMNLP 2023, princeton-nlp/ALCE). First end-to-end benchmark for automatic citation evaluation. Three metric dimensions: **fluency, correctness, citation quality**. Citation quality splits into **Citation Recall** (fraction of claims that have support) and **Citation Correctness** (precision of the cited evidence), where correctness is measured with an NLI entailment check (the TRUE/T5-XXL approach) between the claim and its cited passage. Code + data are public.

The headline number matters: **"on the ELI5 dataset, even the best models lack complete citation support 50% of the time."** This is the canonical evidence that a claim-level attribution layer is genuinely unsolved, which is good news for a project that wants to make it a first-class artifact property.

**The field survey:** *Attribution, Citation, and Quotation: A Survey of Evidence-based Text Generation with LLMs* (arXiv:2508.15396, Schreieder/Schopf/Färber). 134 papers, 300 evaluation metrics across 7 dimensions, 19 frameworks, 231 datasets, 11 benchmarks. Key finding: **the field is fragmented** — inconsistent terminology, isolated evaluation, no unified benchmark. RAG is only *one of seven* closely related approaches; studies aim to *cite* (75% of papers), *attribute* (62%), or *quote* (13%). Dataset: github.com/faerber-lab/AttributeCiteQuote.

**Claim-level tooling that exists:** NLI-based citation-faithfulness evaluators (ALCE reproductions, e.g. `pedromussi1/cite-faithfulness`); post-processing citation repair (**CiteFix**, ACL Industry 2025, https://aclanthology.org/2025.acl-industry.23.pdf); **ALPAGAIN** (Self-RAG, arXiv:2310.11511) trains the model to emit `[Attributed]` / `[Supported]` / `[Not Enough Info]` critique tokens — i.e. a learned per-claim support classifier, not a system guarantee.

**Hash-chained evidence:** I found **no** OSS system that hash-chains RAG evidence. What exists is adjacent:
- **W3C PROV-DM / PROV-O** (https://www.w3.org/TR/prov-dm/) — the standardized provenance ontology: Entity, Activity, Agent, and derivation relations. Aged but still the cleanest vocabulary for "claim derived from episode via agent."
- **in-toto** (CNCF graduated, https://in-toto.io/) — supply-chain attestation layout: link metadata, threshold signing, per-step inspection.
- **SLSA v1.1** — the `about` page now shows **Status: Retired**; v1.1 has a single Build track. Read the *concept* (levels, tracks, build provenance), don't depend on the version.
- **Sigstore / DSSE** — envelope + in-toto statement signing.

None of these are applied to knowledge. **This is a genuine white space and a real differentiator.**

**Steal:** ALCE's three-dimension metric split (fluency / correctness / citation quality) and the NLI-entailment mechanism for correctness — use it as your *verifier*, run at compile time, not query time. PROV-DM's Entity/Activity/Agent derivation vocabulary. The `Self-RAG` critique-token idea as a cheap per-claim support prior.
**Avoid:** doing attribution **post-hoc at query time** via prompting. Attribute at *compile* time, where you control the model, the input, and the seed. Never let a claim exist in the artifact without a resolvable source edge.

---

## 7. Local inference & the model-adapter surface

**llama.cpp server** (ggml-org/llama.cpp, `tools/server/README.md`) — by far the best-supported local surface, and notably broader than "just chat completions":
- **OpenAI-compatible** `chat/completions`, **`responses`**, and **embeddings** routes
- **Anthropic Messages API**-compatible chat completions
- **Reranking endpoint** (#9510)
- Parallel decoding, continuous batching, multimodal, speculative decoding
- **Schema-constrained JSON response format** (GBNF grammars — `grammars/json.gbnf`)
- **Function calling / tool use** for ~any model (not just instruct-tuned)
- Assistant **prefill**, sleep-on-idle, `/props`, `/models`, `/health`, `/metrics`

**GGUF** is the model container format (ggml/docs/gguf.md) — a documented, versioned, portable header + tensor layout. This is your model-pinning mechanism: a GGUF file plus its SHA-256 is a reproducible model identity.

**vLLM / SGLang**: both expose OpenAI-compatible servers (vLLM `/v1/chat/completions`, SGLang same). vLLM V1 defaults `seed=0` and states results are consistent per-run, **but only "on the same hardware and the same vLLM version"** (https://docs.vllm.ai/en/latest/usage/reproducibility.html). There's a `VLLM_BATCH_INVARIANT=1` batch-invariance mode, and a known open bug where it is *not* deterministic under tensor parallelism (vllm#51290).

**Ollama**: easiest, but its OpenAI-compat layer is a partial emulation; tool-calling support is model-dependent. Good for dev, not for a determinism story.

**MLX** (Apple silicon): real prompt caching and native unified memory, but the OpenAI-compat surface is thinner and a 512-token cache trap has been documented. (Third-party blog, treat as anecdote.)

**Minimum viable adapter interface.** The standard that actually exists is `/v1/chat/completions` — adopt it as the *wire* contract, but do **not** stop there. Your adapter must add four things the OpenAI surface cannot express:
1. **Model identity as a hash.** `model` = GGUF SHA-256, not a display name. Without this the artifact is not reproducible.
2. **Pinned decoding contract.** `temperature: 0`, fixed `seed`, fixed backend build, recorded in the artifact. Pin backend version alongside model.
3. **Grammar-constrained output.** `response_format` / JSON schema → GBNF, so extraction is structurally typed rather than hoped-for.
4. **A native in-process path.** `llama.cpp` exposes a C API; `mlx` and `llama-cpp-python` bind it. Keep a direct native adapter for compile-time work (no HTTP, no serialization loss) and use the HTTP OpenAI surface for everything else.

**Steal:** `/v1/chat/completions` as the external contract; llama.cpp's `responses` + embeddings + rerank + grammar routes; GGUF-hash model identity; the Anthropic-compatible route as a cheap second-compat check.
**Avoid:** promising bit-identical outputs from vLLM across versions or hardware. It explicitly does not guarantee that. Any determinism claim in your docs must be scoped to (model hash + backend version + seed + grammar), and you must state that batch scheduling can perturb floats.

---

## 8. Agent control planes & sandboxing

**E2B** (e2b-dev/E2B, 14k stars, Apache-ish OSS + proprietary hosted control plane): microVM-based sandboxes, self-hostable via `e2b/infra`, but the orchestrator (`orchestrator`, `envd`, proxy, traffic-token auth) is the piece that matters and it is cloud-shaped. Cold-start-optimized, and its recent commit history is dominated by browser-runtime and token-auth edge cases — a signal that the control plane is the hard part, not the VM.

**Daytona**: pivoted to a dev-environment-first product; self-hosting exists but the OSS/cloud boundary is murkier than E2B's.

**Modal**: hosted; not a serious local-first option.

**gVisor** and **Firecracker** are the real primitives: gVisor = userspace kernel (fast, container-compatible, needs its own sandbox bootstrap on non-Linux hosts); Firecracker = KVM microVM (strongest isolation, but **Linux/KVM only — no macOS**, so unusable in this local-first dev context). `firecracker-containerd` lets containerd manage Firecracker microVMs.

**The offline/local gap is real.** On macOS the practical local isolation options are: gVisor runtimes, plain containers (Docker/Podman/Colima — but Colima is a Linux VM, so you're back to virtualization), **macOS Seatbelt (`sandbox-exec`) profiles**, or a per-agent scratch VM. The Google Gemini CLI project has been actively hardening Docker/containerd socket isolation inside macOS Seatbelt (google-gemini/gemini-cli#28935) — that PR is a good concrete reference for the actual threat model on this platform.

**Dapr** is a service-mesh sidecar (pub/sub, state, service invocation) with a documented air-gapped deployment path (docs.dapr.io/operations/hosting/self-hosted/self-hosted-airgap/). Useful as an *event/state plumbing* precedent, wrong shape for an agent runtime — it assumes k8s.

**"Agent OS" attempts:** academic framing (arXiv:2607.25076, "Towards an Agent Operating System — Lessons from Classical and Cloud OS"), the Rivet `agentOS` commercial product, and open orchestrators. None has converged on a local-first, offline, deterministic control plane. The gap is real.

**Recommendation for this project:** design the runtime so the **policy gate is the isolation boundary**, and the OS-level sandbox is a *pluggable backend* underneath it. Concretely: local dev on macOS Seatbelt + container runtime; production on gVisor or Firecracker where available. Make the gate fail-closed and make the backend swappable — the spec of a sandbox backend should be "given (tool, argv, cwd, policy) return (result, audit-record)", with the audit record always produced even when the sandbox is a no-op.

**Steal:** E2B's `Sandbox` lifecycle API shape and the traffic-token auth model; Firecracker's microVM boundary as the strong tier; Gemini CLI's macOS Seatbelt isolation of runtime sockets as the concrete local recipe; Dapr's air-gapped deployment checklist.
**Avoid:** making Firecracker a hard dependency (kills macOS). Adopting k8s-shaped orchestration for a single-user local runtime. Treating a hosted control plane as the reference architecture.

---

## 9. Content addressing / reproducible builds applied to knowledge

**Nix** (content-addressed derivations) — outputs addressed by hash of the *build plan* + inputs; content-addressed derivations. The transferable idea: **a derivation is a pure function of its input hashes**, so identical inputs provably yield the identical output, and the store dedupes globally. Note Nix's *intensional* model is subtle and has a long history of sharp edges (https://fzakaria.com/2025/03/08/demystifying-nix-s-intensional-model) — steal the store semantics, not the evaluator.

**Bazel remote caching / action cache** — actions keyed by (action, inputs, deps); remote cache hit means "don't rebuild, just download the recorded output." Steal the action-cache concept almost verbatim: a compile step is identified by its inputs + rule version, and the output is looked up by hash.

**in-toto** (CNCF graduated) — attestation layout: link metadata per supply-chain step, threshold signing, the final product verifies by inspecting the recorded steps. This is precisely the shape of "how was this claim derived, by which model, in which order."

**SLSA v1.1** — read for the *provenance-predicate* shape (build definition, run details, builder id, materials). The `about` page is now marked **Retired** with only a Build track; do not bind your schema to the version.

**Software Heritage** — the best existing precedent for content-addressed *knowledge* rather than code: intrinsic identifiers (SHA-1 of normalized content), full origin/version graph, and stable SWHIDs. Its "intrinsic data" concept is the answer to "how do I identify this document independent of where I got it."

**Also relevant:** IPFS/libp2p (CID = content hash of a Merkle DAG) for a Merkle-root-over-claims structure; Verkle trees if claim sets get large.

**Key structural insight to take seriously:** SLSA v1.1 being retired-and-narrowed, and in-toto succeeding it, says that **supply-chain attestation matured by generalizing and shrinking**. A single-artifact, single-predicate spec beat the grand multi-track vision. Design the knowledge-provenance schema small: *artifact id, input episode ids, rule/model versions, resulting claim ids, timestamp, signature*. Everything else can be a facet.

**Steal:** intrinsic content IDs (Software Heritage) for episodes; Merkle root over the claim set for the artifact; in-toto's link layout for the derivation record; Bazel's action-cache lookup for incremental compile; Nix's "hash of the plan" identity.
**Avoid:** binding to a retired SLSA version; designing a grand provenance vision. Keep it small and content-addressed.

---

## 10. Build-system-for-knowledge precedents

**OpenLineage** (LF AI & Data Foundation **Graduate** project) — the strongest precedent, and directly on point. Core model: **Job**, **Run**, **Dataset** as the three core entities, each with a **naming strategy** for stable identity, extended by **facets** — "an atomic piece of metadata attached to one of the core entities," user-defined. Spec is OpenAPI-defined. Integrations: Airflow, Spark, Flink, dbt, Trino, Hive, Feast, Great Expectations. Supports **column-level lineage**. The stated motivation is exactly yours: stop every project instrumenting its own custom lineage integration, and stop integrations breaking on upstream version bumps.

**DataHub** — ingests OpenLineage and renders lineage; mature, but it is a *catalog/observability UI*, not a compiler. Its lineage graph is metadata about data, not a content-addressed derivation graph.

**Marquez** — the OpenLineage reference UI; renders dbt→Marquez lineage nicely.

**dbt** — the strongest *practical* precedent. It won by making a small explicit DAG of models with a manifest, ref materialization, tests, and incremental materialization. Its real contribution to steal: **the manifest is a checked-in, diffable, human-inspectable description of the compiled artifact's structure** — exactly the "executable, inspectable, versioned" goal.

**Apache Atlas** — metadata + lineage, heavyweight, Hadoop-ecosystem-era; borrow the classification/tagging vocabulary at most.

**Wikidata provenance** — the reference implementation of *statement-level* provenance at scale: each statement (claim) carries its own reference/qualifier set, and ranks + "preferred" ordering resolve conflicting values. This is the closest existing model to "typed claim + provenance + belief."

**KET-RAG** (arXiv:2502.09304) is worth a look as a cost-efficency counterpoint to GraphRAG indexing blowup.

**Steal:** OpenLineage's three-entity core (Job/Run/Dataset) + facet extension mechanism — map to (compile-run / source-episode / derived-artifact) with claim-type facets. dbt's checked-in **manifest** as the inspectable artifact descriptor. Wikidata's **statement-level** provenance with ranks for conflicting claims. DataHub's ingestion-of-external-lineage pattern if you ever consume OpenLineage.
**Avoid:** Atlas-style heavyweight catalogs. Treating lineage as *observability metadata generated at runtime* rather than as the *primary build output* — that inversion is the single mistake to avoid.

---

# The 8 findings that should change architectural decisions

1. **MCP is stateless now — and Roots/Sampling/Logging are deprecated until ≥2027-07-28.** The `initialize` handshake is gone; `server/discover` replaced it; MRTR replaced server-initiated requests. Design the tool boundary as **stateless, per-request, cacheable JSON-RPC with `Mcp-Method`/`Mcp-Name` headers** — that gives you gateway-level policy gating, list caching (`ttlMs`/`cacheScope`), and round-robin load balancing for free. Do not build on sampling or roots; they have a published removal date. Registry is preview-only, so keep your own server inventory as source of truth.

2. **A2A 1.0 (2026-03-12) is a mature protocol with a stale extension ecosystem.** Proto is normative, three bindings must be functionally equivalent, Agent Cards are JWS-signable over RFC 8785 canonical JSON. Use it strictly as an **opaque delegation boundary** — never as your internal data model. Pin `A2A-Version` at the edge, emit 1.0-native shapes only, and keep 0.3 read support. The x402 payment extension still on the 0.3 wire is the proof that "in the spec" ≠ "implemented."

3. **GraphRAG is a query strategy, not a compiler, and it is structurally hostile to your goals.** LLM extraction + LLM community summarization + map-reduce = no provenance, no temporal validity, no determinism, no incremental rebuild. Do not shape your compiler around it. Borrow community-hierarchy synthesis and DRIFT as *query-time* operators over your own artifact.

4. **The bitemporal claim+episode model is the core reusable invention, and Graphiti proves it works.** Entities with evolving summaries, content-addressed episodes as provenance root, facts with `valid_time` **and** `transaction_time` intervals, and **invalidation-not-deletion** as the update policy. Make incremental-update-without-recomputation an explicit architectural invariant rather than an aspiration. Adopt prescribed-or-learned ontology. Note that Zep's production graph engine is proprietary and no mainstream engine has a first-class temporal layer — you implement the intervals yourself, which is a real moat.

5. **Claim-level attribution is genuinely unsolved and unserved by hash-chained evidence.** ALCE's own result — best models lack complete citation support 50% of the time on ELI5 — plus a 134-paper survey concluding the field has no unified taxonomy or benchmark. No OSS system hash-chains RAG evidence; in-toto, PROV-DM, SLSA, and Sigstore are all supply-chain, none is knowledge. **Attribute at compile time, never at query time** — you control the model, seed, and grammar there but not at query time. Use ALCE's fluency/correctness/citation-quality split and NLI entailment as a compile-time verifier.

6. **Determinism must be scoped and declared, not assumed.** vLLM explicitly guarantees reproducibility only "on the same hardware and the same vLLM version," and `VLLM_BATCH_INVARIANT=1` has an open tensor-parallel bug. So the model adapter must pin four things as artifact metadata: **GGUF SHA-256, backend version, seed (0), and grammar**. Make `/v1/chat/completions` the external wire contract (llama.cpp covers chat + responses + embeddings + rerank + GBNF grammar + tool calling — unusually complete), keep a native in-process llama.cpp path for compile-time work, and document that batch scheduling perturbs floats.

7. **Provenance/lineage should be OpenLineage-shaped, not SLSA-shaped, and small.** Adopt the three-entity core (Job→Run→Dataset, mapping to compile-run→source-episode→derived-artifact) with the **facet** extension mechanism, dbt's checked-in **manifest** as the inspectable artifact descriptor, Wikidata's **statement-level** provenance-with-ranks for conflicting claims, and Nix/Bazel content-addressing for incremental rebuilds. SLSA v1.1 is marked **Retired** and narrowed to a single track — in-toto's generalization-then-shrinking is the cautionary precedent. Invert the usual order: lineage is the **primary build output**, not runtime observability metadata.

8. **Local offline sandboxing is a real gap, and macOS is the hard case.** Firecracker is the strongest tier but is Linux/KVM-only and kills the macOS dev story. E2B/Daytona control planes are cloud-shaped. The workable design: **policy gate as the isolation boundary, sandbox as a swappable backend** — Seatbelt + container runtime locally (see the Gemini CLI's containerd-socket isolation PR for the concrete recipe), gVisor/Firecracker in production. The backend contract should be `(tool, argv, cwd, policy) → (result, audit-record)`, with the audit record emitted even when isolation is degraded. No open "agent OS" has converged on local-first, offline, deterministic control plane.

---

## Skepticism ledger
- Blog posts describing MCP "2025-11-25 as current" are **stale**; the current revision is 2026-07-28.
- A2A community/audit content is reliable on spec facts, advocacy about what to build.
- Zep's benchmark numbers (94.8% DMR) are **vendor-produced** on a vendor benchmark; the graph engine behind them is closed.
- llama.cpp feature lists are from the repo (primary) — trustworthy. Ollama/MLX OpenAI-compat gaps come from third-party blogs (anecdotal).
- `llm4agents.com` A2A 1.0 audit is third-party but verifiable against the repo; cited here as evidence of ecosystem lag, not spec truth.
