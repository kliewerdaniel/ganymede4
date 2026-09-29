# ADR-014 — The auditor must run, and must not invent defects

Status: Ratified (2026-09-29)
Depends on: ADR-002, ADR-006, ADR-013

## Context

ADR-013 made the full-corpus evaluation finish. The next step was the obvious
one: run the independent provenance auditor on the 797 MB artifact it produced.
The auditor was the last thing standing between "the evaluator says this is
fine" and "something that did not write the evaluator says this is fine."

It did not run. It was still going after 15 minutes.

## Two defects, in the thing that verifies the thing

### 1. The auditor was quadratic

`audit()` looped over every claim and issued a `SELECT` per claim. Three of
those queries had no supporting index:

- `evaluations WHERE subject_id = ?` — no index at all
- `claim_evidence WHERE claim_id = ?` — the primary key is `(claim_id, evidence_id)`,
  so this one was actually covered
- `peer_groups WHERE id = ?` — primary key, covered

The unindexed one is the whole story: 324,951 claims × a full scan of 308,935
evaluations is ~10^11 row visits.

Fixed in two places, because both were wrong:

- **The auditor** is now set-based. `EXISTS`/`NOT EXISTS` subqueries and joins,
  one pass each, no per-claim query.
- **The store** gained `idx_evaluations_subject` and
  `idx_claim_evidence_claim`. The store's own `evaluations_for` runs the same
  query and had the same problem; the auditor merely noticed it first.

These are indexes, not columns. They cannot change a content id, a Merkle root,
or a schema version, so an artifact compiled before them still verifies against
a manifest compiled after.

Measured on the real artifact: **index build 1.5 s, the previously-unfinishable
query 1.1 s, full audit 7.7 s.**

### 2. The auditor reported 1,434 defects that did not exist

The set-based rewrite compared spans with SQLite's `substr()`:

```sql
WHERE substr(s.content, e.start_offset + 1, e.end_offset - e.start_offset) = e.text
```

`substr()` operates on a NUL-terminated C string. One ChatGPT export contains a
literal NUL at character 242,506, so every span past that point reads as the
empty string. The audit reported **1,434 span mismatches on a perfectly
provenance-clean artifact** — `resolved` fell from 308,935 to 307,500 and every
"mismatch" had `actual: ""`.

The provenance was correct in all 1,434 rows. The checker was wrong.

This one is worth more than its cost. An auditor that invents defects is worse
than no auditor: it trains its readers to ignore it, and a reader who has
learned to ignore it will also ignore the output when a real defect appears. A
noisy checker does not degrade to useless — it degrades to *anti-useful*.

The fix is to do the span comparison in Python, where slicing is a language
semantic and a NUL is just a character. That is a deliberate refusal to do it
in SQL despite SQL being faster, and the comment in the code says so.

## Decision

1. **The auditor is set-based.** No per-row query inside a loop over the
   artifact. It has to be able to check the thing it exists to check.

2. **Span comparison happens in Python, not SQL.** `substr()` is NUL-terminated
   and therefore wrong on any real text export. A correctness requirement
   overrides a performance preference, and the performance cost is 7.7 seconds.

3. **The store indexes what it queries.** `evaluations(subject_id)` and
   `claim_evidence(claim_id)`. Found by the auditor, fixed in both places.

4. **The auditor's own tests are adversarial in both directions.** It must
   catch a planted defect *and* it must not invent one. The NUL case is now a
   permanent test, because the second failure mode is the one that has no
   natural alarm.

5. **A check that cannot run is reported as not run.** If the JSON1 extension
   backing `json_each` is missing, peer membership is appended to the report as
   `NOT CHECKED` rather than skipped silently. An audit that quietly stops
   checking is the same failure as one that invents findings, in the other
   direction.

## Result

| | before | after |
|---|---|---|
| full audit, 324,951 claims | did not finish in 15 min | **7.7 s** |
| false span mismatches | 1,434 | **0** |
| clean artifact | — | **exit 0, all 308,935 spans resolved** |
| one source byte changed | — | **exit 1, 3 mismatches, resolved drops by 3** |

Verified end to end on `/tmp/ganymede4-corpus.db`, the real corpus: 324,951
claims, 308,935 evidence spans, 151,488 peer groups, zero orphans, zero
unresolved attestations, zero span mismatches.

## Alternatives rejected

- **Raise the time limit on the old auditor.** It was not slow, it was
  quadratic. A longer budget postpones the same failure.
- **Add indexes and keep the row-by-row loop.** Fixes the constant, keeps the
  shape that made the problem possible, and keeps the auditor one schema change
  away from being quadratic again.
- **Strip NUL bytes from sources at load.** Makes the false positive go away
  without making the auditor correct, and silently mutates source content —
  which is the one thing a content-addressed store must never do.
- **Cap the number of reported failures and exit 0 past the cap.** A cap on
  *examples* is fine; a cap that changes the verdict is a checker that passes
  artifacts it did not finish reading.
