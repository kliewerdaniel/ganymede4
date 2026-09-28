# ADR-003 — Storage: stdlib sqlite3, single claim model

**Status:** Ratified (2026-09-27)
**Context:** `migration-map.md` F3, F4

## Context

The estate uses six different storage approaches: Postgres+pgvector (Ganymede 1,
Docker), SQLAlchemy models (Ganymede 3 engine), raw `sqlite3` (Ganymede 3
claim_graph, index, hermes-atlas, sovereign-intelligence, sworker), TypeScript
stores (AEP), content-addressed stores (knowledge-compiler-sdk).

Worse, **`ganymede3` ships two incompatible claim models** joined only by a
`claim_id` string:

1. `engine/database.py:262 ClaimRecord` — SQLAlchemy, 9 states
2. `claim_graph/graph.py:80 Claim` — `sqlite3`, 5 states

Their epistemic and edge vocabularies disagree. Nothing enforces agreement. This
is a design defect, not a compatibility layer, and it is the direct cause of the
`docs/phase0-audit.md` finding where `established_facts.json` cited 23 claim IDs
that **did not exist** in the 55,308-claim graph.

## Decision

**stdlib `sqlite3`.** No ORM, no service dependency, no daemon.

**One claim model, in one store.** Per ADR-001, `ganymede3` is read not
vendored, so the duplicate does not propagate — the new `Claim` is written once
against `data-model.md` and carries the ADR-002 spine.

**Everything is content-addressed.** Primary keys are `sha256` over canonical
bytes. Inserting the same record twice is a no-op, because the key *is* the
identity. This makes idempotence a property of the schema rather than a rule
someone has to remember to enforce.

**Postgres/pgvector remains a documented future adapter**, not v1. The adapter
seam is the query interface, so a later vector backend is an implementation
detail — not a migration.

## Consequences

**Positive.** The offline / air-gap property is total: no service to start, no
port to bind, no version to match. `sworker` (12.6k LOC, 490 tests) and
`hermes-atlas` (4.7k LOC) both prove stdlib-first works at real scale, so this
is not a hope — it is a measured result from the same estate.

Identity cannot drift, because it is a hash. A dangling `claim_id` is
unrepresentable; that is the v0.1 fabrication class, closed at the schema level.

**Negative.** Single-writer. No concurrent multi-process writes. Mitigation:
one writer process, WAL mode, and the ledger is append-only anyway so contention
is narrow. Heavy analytical query performance will trail Postgres. Acceptable:
v1 is a compiler, not an OLAP server.

**Neutral.** Migrations are hand-written `ALTER TABLE` since there is no ORM
migration tool. This is tedious and it is also the point — a reader can audit
every schema change in the repo history.

## Alternatives

1. **Postgres + pgvector from day one.** Rejected: requires a running service,
   which breaks the core sovereignty property and the offline acceptance test.
   The 123k-chunk Ganymede run is the evidence that this is *sufficient* for
   scale and also that it is *heavy*.
2. **SQLAlchemy ORM.** Rejected: an ORM hides the schema, and this project's
   thesis is that the schema must be inspectable. It also cannot express
   "primary key is a content hash" naturally.
3. **Keep both claim models with an adapter.** Rejected: two vocabularies with
   nothing enforcing agreement is the defect. An adapter is a promise to
   reconcile them later; better to have one.

## Acceptance

- [x] No ORM, no `sqlalchemy` import anywhere in the package
- [x] `Claim` is defined exactly once
- [x] Every record's primary key is a content hash
- [x] Inserting an identical record twice writes nothing and mutates no row
- [x] A claim referencing a non-existent evidence ID is rejected at write time
      (FK enforcement, `PRAGMA foreign_keys=ON`)
- [x] The full compile + witness + policy suite passes with networking disabled
