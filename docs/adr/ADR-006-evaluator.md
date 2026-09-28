# ADR-006: The evaluator — sound, incomplete, and never a guess

- Status: **Proposed**
- Date: 2026-09-28
- Depends on: ADR-002, ADR-004, ADR-005

---

## 1. The problem

The compiler writes `UNEXAMINED` and refuses to do more (ADR-004). The witness
reads and refuses to certify (ADR-005). Between them, nothing can ever move a
claim out of `UNEXAMINED`, so the 13-state spine is a vocabulary with exactly one
reachable value.

Something has to be able to say "this evidence bears this claim." The obvious
candidate is a model. We know from the Ganymede W7 work what that costs: a
cross-encoder and RRF both measure *topical relevance*, not *answer containment*,
and their score distributions over answerable and answer-absent questions
**fully overlap**. Every threshold we tuned found a point where recall went up
and precision fell off a cliff, because the signal was never there. NLI
(`roberta-large-mnli`, meta-hypothesis formulation) came back at 7% recall. This
is a **capability gap, not a threshold problem**, and no amount of tuning
converts it into one.

So: a deterministic evaluator, and an explicit statement of what it is allowed
to conclude.

## 2. Decision

**The evaluator may only assign states it can decide without a semantic model,
and its default answer is `INCONCLUSIVE`.** It is *sound but incomplete*: every
state it assigns is correct, and it will miss contradictions a human would spot.
We take that trade deliberately, because the alternative — a plausible-looking
verdict on every claim — is the v0.1 fabrication with a confidence score
attached.

The evaluator's output is a **relation**, not a score. No number is produced,
stored, or thresholded.

### 2.1 The three relations it may return

| Relation | Decided by | Resulting state |
|---|---|---|
| `ATTESTED` | candidate's normalized token sequence occurs as a **contiguous run** in a corpus claim's sequence | `SUPPORTED` |
| `CONTRADICTED` | identical normalized **term set** and **opposite polarity** | `CONTRADICTED` |
| neither, but retrieval hit something | — | `INCONCLUSIVE` |
| nothing retrieved | writes an `Investigation`, as ADR-005 requires | `UNRESOLVED` |

### 2.2 The rule that makes this honest

**A claim may never be its own evidence.**

The compiler emits claims that are *verbatim segments of a source span*
(ADR-004 §3). So the candidate claim and its own evidence span contain the same
tokens by construction, and a naive containment test would mark **every claim in
every corpus `SUPPORTED`**, forever, with perfect recall and zero information.

That is the v0.1 failure in a new outfit: 23 "established facts", all equally
established, all equally meaningless.

Therefore the evaluator evaluates a candidate **only against claims that are not
its own**, and it records which claim ids attested it. `SUPPORTED` with an empty
attestation list is not a state the evaluator can produce.

## 3. Why contradiction is the real product

Anyone can accumulate supporting citations — it is cheap, it feels like progress,
and it is what a RAG system does by default. The scarce, decisive signal in a
corpus is finding that **two claims cannot both be true**, because that is the
one finding that requires actually reading.

Contradiction is also the one semantic relation that *is* decidable without a
model, under a narrow grammar. Two normalized claims with an identical term set
and opposite polarity cannot both be true. That is logic, not similarity.

So the evaluator's headline capability is negative. It is built to say *no*.

## 4. Determinism

Normalization is total and fixed: lowercase → split on non-alphanumerics → drop
`FUNCTION_WORDS` (ADR-005) → conservative suffix-strip → term set + polarity.
No corpus-frequency table, no learned parameters, no seed. Two runs on the same
artifact produce byte-identical verdicts, which the tests assert directly.

The stemmer is deliberately crude. `enforces` → `enforce`, `enforcement` →
`enforcement`. A stemmer that over-strips would merge distinct claims and
manufacture contradictions, so it only strips suffixes that are unambiguously
inflectional, and **anything it cannot normalize confidently is left alone** —
which pushes the claim to `INCONCLUSIVE`, the safe direction.

## 5. Rejected alternatives

**A model as evaluator.** Rejected for now, and the W7 result is the reason: we
cannot measure when it is right, so we cannot gate it. A future ADR may admit a
model *as an additional attester* whose verdicts are recorded as claims in their
own right — never as a state transition. The store must never accept a state
change whose only support is a model opinion.

**Similarity thresholds.** Rejected outright. W7 measured this exact idea and the
distributions overlap completely. A threshold here would be a number that looks
like rigor and is actually a guess with extra steps.

**Aggregation across many evidence spans** ("three sources agree, therefore
supported"). Rejected. Evidence magnitude is not sufficiency — that is ADR-002's
own rule, and it applies to counts as much as to confidence. Three restatements
of one unsourced assertion is one assertion, said three times.

**Promoting to `VALIDATED`.** Not reachable by this evaluator. `VALIDATED`
means independently reproduced, which needs a second artifact or a second
process, not a second citation within one.

## 6. Consequences

- `SUPPORTED` becomes reachable, but only for a proposition that another claim
  in the same corpus literally states. Rare, and correct when it happens.
- `CONTRADICTED` becomes reachable, and is the evaluator's most useful output.
- `INCONCLUSIVE` becomes the common case, which is the honest description of
  lexical retrieval's relationship to entailment.
- The evaluator writes an `Evaluation` record per verdict, symmetric with the
  `Investigation` record that proves absence. **Absence must show its work;
  support must too.**

## 7. Enforced by

- `evaluate()` returns a `Verdict` with a `relation`, never a float.
- `SUPPORTED` requires a non-empty `attested_by`.
- Self-attestation is structurally impossible: the candidate is excluded from
  its own comparison set.
- Every state assignment is gated by `check_transition`, so the spine still
  governs.
- An `Evaluation` row is written for every verdict; a state change with no
  record is a store error, exactly as `UNRESOLVED` with no investigation is.
