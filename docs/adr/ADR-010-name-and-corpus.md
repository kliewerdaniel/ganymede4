# ADR-010: The project is ganymede4, and its corpus is its author's own record

Status: Ratified (2026-09-28)
Depends on: ADR-001, ADR-004, ADR-005

## Decision 1 — the name

The project is **ganymede4**.

This follows `ganymede`, `ganymede2`, and `ganymede3` in the same lineage, so
the name is descriptive rather than new: it is the fourth attempt at the same
problem, and the succession is the point. It is not a fork of any of them.
`ganymede3` remains an unlicensed read-only design reference per ADR-001, and
nothing has been copied from it. `ganymede4` is Apache-2.0 and clean-room.

## Decision 2 — the corpus

The demo corpus is the author's own public record:

| Source | Documents | Location |
|---|---|---|
| Reddit comments | 2,178 | `~/Projects/Chris/reddit/comments/` |
| Reddit submissions | 676 | `~/Projects/Chris/reddit/submissions/` |
| ChatGPT conversations | 562 | `~/Projects/Chris/openai/conversations_markdown/` |
| **Total** | **3,416** | 65 MB |

Account confirmed as `KonradFreeman` (1,374 references across the export).
Subreddit distribution is concentrated exactly where this project's subject
lives: `ArtificialInteligence` (460), `u_KonradFreeman` (309), `aiwars` (186),
`vibecoding` (140), `LocalLLaMA` (115).

This is a good corpus and a bad one, and both facts matter.

**It is good** because it is the natural test of the thesis. A corpus of one
person arguing with strangers about whether models are the intelligence system
— and simultaneously a record of that person doing exactly that argument with
models — is a real argument with a real conclusion, not a fixture. A
synthetic corpus would let every component pass while proving nothing about the
real case.

**It is bad** in one specific and serious way, which is the rest of this ADR.

## The hazard: this corpus contains the previous system's own output

The ChatGPT half contains **12,185 assistant turns against 7,579 user turns**.
The majority of the conversational corpus is *not* the author speaking — it is
a language model's prior output, captured verbatim.

Ingesting that as ordinary source text would be a category error with real
consequences, and it is precisely the error this project exists to detect in
others. A model asserting "the sky is blue" in a 2024 conversation, captured
to a file, becomes — under naive ingestion — a *compiled claim* with a valid
source span, a Merkle root, and a provenance chain. The evidence would be
structurally perfect and epistemically worthless. Every guarantee the compiler
provides would hold, and the artifact would be a machine's confident
unsubstantiated output laundered into the record as though the author had said
it.

That is not a hypothetical. It is the single most likely way this project
could produce a false artifact, and the substrate would certify it.

So: **speaker attribution is part of the corpus schema, not a nicety.** A
`SourceSpec` carries a `source_type`, and this corpus uses it:

| `source_type` | Meaning | Epistemic treatment |
|---|---|---|
| `reddit_comment` | The author, writing | First-person testimony; `UNEXAMINED` |
| `reddit_submission` | The author, writing | First-person testimony; `UNEXAMINED` |
| `chatgpt_user` | The author, writing | First-person testimony; `UNEXAMINED` |
| `chatgpt_assistant` | **A model's prior output** | **Not testimony. Never grounds a claim about the world.** |

The last row is the decision. Assistant turns are ingested and indexed — they
are part of the record and the witness must be able to find them, because
"What did I argue to a model in 2024?" is a legitimate question about the
record. But they are **typed as a different kind of source**, and any claim
whose sole evidence is an assistant turn is not testimony. The provenance
auditor reports the distinction; a future evaluator phase must not let a
`chatgpt_assistant` span alone satisfy attestation.

The alternative — dropping assistant turns — was rejected. It would produce a
corpus that silently omits most of what the author actually read and was told,
which is a distortion in the opposite direction, and it would make the corpus
look more epistemically clean than it is. The honest move is to keep the turns
and mark them, so that the artifact records the full history *including* the
part that is a machine talking.

## Finding: the evaluator does not scale to this corpus

Running the full pipeline on the real corpus surfaced a defect that no amount
of fixture testing would have found, which is the argument for using real data
in the first place.

`Evaluator.evaluate` ends its common case with:

```python
hits = self._index.search(text, limit=len(self._index) or 1)
```

It asks the retriever for **every document in the index** in order to decide
which are *neighbours* of the claim being judged. For one claim that is
tolerable. For `evaluate_all` it is the whole corpus, per claim, so the pass is
super-linear in corpus size.

Measured on synthetic corpora of identical structure, cost per source:

| sources | ms per source |
|---|---|
| 50 | 3.0 |
| 100 | 2.5 |
| 200 | 4.1 |
| 400 | 7.7 |

Per-source cost roughly doubles as the corpus doubles, which is the signature
of the quadratic term. On the real corpus this does not degrade gracefully —
after 17 minutes it had written **77 evaluation records out of 422,753
claims**, and I killed it. The compile itself is fine: 18,930 sources produced
422,753 claims with 84,587+ evidence rows and an identical Merkle root on a
reversed-order recompile.

The fix is obvious and was deliberately not applied blind: a neighbour search
does not need the whole index, and the honest question is what bound preserves
the *semantics* rather than just making the number smaller. Narrowing the
`limit` to a constant would be the quick version, and it would quietly change
what "there is material in the neighbourhood" means — turning a completeness
property into a top-k approximation, in a component whose entire justification
is that it is sound and does not over-claim. That trade needs to be decided on
the merits, in its own ADR, with the recall consequence measured rather than
assumed.

Until then: `evaluate_all` is **not** run against the full corpus, and
`scripts/compile_corpus.py` requires `--no-evaluate` for a full run. The
component is correct and tested at fixture scale; it is not yet correct at
corpus scale, and the README says so rather than implying otherwise.

This is recorded as a known limitation rather than quietly fixed because the
fix trades a completeness guarantee for speed, and that is a decision about
meaning, not performance.

## Consequences

- The corpus is **personal**. It contains the author's broken ribs, his
  brother's death, his girlfriend, his mental health, his employment, and his
  Reddit disputes. It is local-only and never leaves the machine. No network
  fetch is permitted against it, and the default pipeline is offline, which is
  now a data-protection property rather than only an engineering one.
- The corpus is large relative to the smoke corpus the tests use. Tests keep
  using small deterministic fixtures so the suite stays fast and hermetic; the
  real corpus is compiled by an explicit script and its result is inspected,
  not asserted on every test run.
- Because the author is the author, contradictions in the corpus are expected
  and often substantive. That is a feature: it is the real input the evaluator
  was designed for, rather than a synthetic conflict invented to make a test
  pass.
- 12,185 assistant turns also means this corpus is a large sample of *model
  failure modes in the wild*. Future phases should mine it for the same
  recurrence analysis the compiler already performs.

## Boundaries

- The corpus is not committed to the repository. It is read from its existing
  location, and the build records the path and a content digest.
- No claim derived from this corpus is treated as established fact about the
  author's life. The compiler emits `UNEXAMINED` and the witness reports what
  the record says, not what is true.
- The narrator here is Daniel, who is both the corpus's author and the
  operator of the system reading it. That is not a neutral vantage, and no
  component is permitted to treat it as one.
