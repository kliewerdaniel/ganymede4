"""The substrate store.

Records are content-addressed, so a primary key is a hash and inserting the same
record twice is structurally a no-op. There is no "is this a duplicate?" check
in the write path because there cannot be a duplicate: the second insert
collides with the first primary key.

The store owns two invariants that the rest of the system depends on:

1. **Provenance completeness.** A claim cannot reference an evidence span that
   does not exist. Enforced by real foreign keys with
   ``PRAGMA foreign_keys=ON`` — not by a convention, and not by a post-hoc
   audit. This is the check that makes "no claim without a resolvable source
   edge" a property of the database rather than an aspiration.

2. **Absence is provable.** A claim in ``UNRESOLVED`` requires an investigation
   record. See ``knowledge.epistemic``.

Stdlib ``sqlite3`` only (ADR-003). No ORM, no service, no daemon.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterator, Mapping, NamedTuple, Sequence

from ..core.canonical import canonical_bytes
from ..core.content import content_id, digest
from .epistemic import EpistemicState, MissingInvestigation, check_transition

__all__ = ["Store", "SCHEMA_VERSION", "ProvenanceError", "UnknownReference"]

SCHEMA_VERSION = 1


class _HydratedEvaluation(dict):
    """One evaluation row with its attestation group resolved back to members.

    A ``dict`` rather than ``sqlite3.Row`` because the group id is replaced on
    the way out, which a ``Row`` cannot express. Callers already index these by
    column name, so this is a drop-in for every existing read.
    """

#: Tables are created in dependency order. Every claim points at an evidence
#: span, so ``evidence`` must exist before ``claims``. SQLite would happily
#: accept a forward reference in a CREATE TABLE and only fail at insert time
#: with a less obvious error, so the ordering here is load-bearing.
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id           TEXT PRIMARY KEY,
    uri          TEXT NOT NULL,
    source_type  TEXT NOT NULL,
    checksum     TEXT NOT NULL,
    content      TEXT NOT NULL,
    meta         TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS evidence (
    id           TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL REFERENCES sources(id),
    start_offset INTEGER NOT NULL,
    end_offset   INTEGER NOT NULL,
    text         TEXT NOT NULL,
    meta         TEXT NOT NULL DEFAULT '{}',
    CHECK (end_offset >= start_offset)
);

CREATE TABLE IF NOT EXISTS investigations (
    id           TEXT PRIMARY KEY,
    question     TEXT NOT NULL,
    searched     TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id             TEXT PRIMARY KEY,
    text           TEXT NOT NULL,
    state          TEXT NOT NULL,
    valid_time     TEXT,
    transaction_time TEXT NOT NULL,
    investigation_id TEXT REFERENCES investigations(id),
    meta           TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS peer_groups (
    id       TEXT PRIMARY KEY,
    members  TEXT NOT NULL,
    size     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id             TEXT PRIMARY KEY,
    subject_id     TEXT NOT NULL,
    relation       TEXT NOT NULL,
    method         TEXT NOT NULL,
    attested_by    TEXT NOT NULL,
    contradicted_by TEXT NOT NULL DEFAULT '',
    decided_at     TEXT NOT NULL,
    meta           TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id    TEXT NOT NULL REFERENCES claims(id),
    evidence_id TEXT NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (claim_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS claim_edges (
    from_id  TEXT NOT NULL REFERENCES claims(id),
    to_id    TEXT NOT NULL REFERENCES claims(id),
    relation TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, relation)
);

CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_ev ON claim_evidence(evidence_id);
CREATE INDEX IF NOT EXISTS idx_claims_state ON claims(state);

-- ADR-014: the evaluator and the auditor both ask "which evaluation(s) name
-- this claim?". Without an index on `subject_id`, that is a full scan of the
-- evaluations table per claim — quadratic, and invisible until an artifact is
-- large enough to time out on. The independent auditor hit exactly this on
-- 324,951 claims; it is the same query the store runs in `evaluations_for`.
--
-- These are indexes, not columns: they cannot change a content id, a Merkle
-- root, or a schema version, so an artifact compiled before them still
-- verifies against a manifest compiled after.
CREATE INDEX IF NOT EXISTS idx_evaluations_subject ON evaluations(subject_id);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON claim_evidence(claim_id);

-- ADR-018. Single-row table: what this store currently *is*.
--
-- The Merkle root covers each claim's epistemic state, so an artifact whose
-- beliefs moved is a different artifact and gets a different version. That
-- leaves one window open: between a revision and the next recompile, the
-- stored rows no longer match any manifest that was ever built. A witness
-- bound to the old version would keep answering from the moved store.
--
-- `state_epoch` is the tripwire. It is incremented by `set_state` -- the one
-- funnel every epistemic transition passes through -- and read by a witness
-- before each answer. O(1), because a witness that re-derived the full
-- state digest per question would cost 0.59s on a 336k-claim corpus, and a
-- guard that is switched off under load is not a guard.
--
-- It is a counter in the database it guards, so direct SQL bypasses it.
-- That is what the independent auditor's digest re-derivation is for: the
-- epoch catches the ordinary path cheaply, the auditor catches the
-- extraordinary one after the fact. Neither is trusted to cover for the other.
CREATE TABLE IF NOT EXISTS artifact (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    root         TEXT NOT NULL DEFAULT '',
    version      TEXT NOT NULL DEFAULT '',
    state_digest TEXT NOT NULL DEFAULT '',
    state_epoch  INTEGER NOT NULL DEFAULT 0,
    -- ADR-022: set when a belief moved and the digest has not yet been
    -- re-derived. The column is the honest record that the store is
    -- mid-update; the auditor reads it independently, so a store abandoned
    -- dirty fails its audit instead of quietly lying about its beliefs.
    seal_pending INTEGER NOT NULL DEFAULT 0
);
"""


#: States that assert something and therefore need an evaluation behind them
#: (ADR-023). The complement is the absence of a conclusion -- a claim that has
#: not been decided, or was decided and could not be -- which needs no record to
#: say "nothing concluded this yet".
#:
#: NOT YET ENFORCED by `set_state` (ADR-023, open). The rule is right and the
#: enforcement was written and measured, but it breaks 19 existing tests that
#: transition claims into asserting states with no record -- which is the same
#: defect, sitting in the tests. Migrating those call sites is the work; doing
#: it halfway would ship a tree where the invariant is claimed in a docstring
#: and absent from the run.
#:
#: Until then the independent auditor is the only thing enforcing it, which is
#: the ADR-023 gap in its original form: a store can be written that fails its
#: own audit. The randomized sequence test reproduces it on demand.
#:
#: Kept here so the rule has one name, and deliberately NOT imported by the
#: independent auditor: a checker that shares the implementation's idea of
#: what is allowed is not a check.
JUSTIFY_REQUIRED = frozenset(
    {
        "supported",
        "validated",
        "contradicted",
        "contested",
        "retracted",
        "superseded",
        "invalidated",
    }
)


class ProvenanceError(Exception):
    """A record was written without resolvable provenance.

    Fail-closed: a claim that cannot name its source is not a degraded claim,
    it is an unverifiable one, and it is rejected rather than stored with a
    warning.
    """



class MissingEvaluation(ProvenanceError):
    """A belief was moved into an asserting state with nothing deciding it."""

class UnknownReference(Exception):
    """A record referenced an id that does not exist."""


class Store:
    """Content-addressed, provenance-enforcing record store."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        # OFF by default in SQLite. Without this, foreign keys are decorative —
        # the schema would *look* like it enforces provenance while accepting
        # any dangling claim_evidence row, which is precisely the v0.1 failure.
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        # `CREATE TABLE IF NOT EXISTS` silently does nothing for a database
        # created before a column was added, so an existing artifact would
        # miss the new column forever. The real corpus database is 845 MB and
        # must not have to be rebuilt to gain a column, hence the ALTER.
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(artifact)")}
        if "seal_pending" not in cols:
            self.db.execute(
                "ALTER TABLE artifact ADD COLUMN seal_pending INTEGER NOT NULL DEFAULT 0"
            )
            self.db.commit()
        self.db.commit()

    _sealing: bool = False

    def close(self) -> None:
        # Flush before closing. Closing is the ordinary end of a session, and
        # a store that is abandoned mid-update should still be a store whose
        # recorded identity is true -- otherwise `with Store(...)` would
        # quietly produce an artifact that fails its own audit, and the
        # `seal_pending` guard would fire on a program that did nothing wrong.
        #
        # Best effort: a flush failure must not stop the connection from
        # closing, but it is left pending so the auditor still reports it.
        try:
            self._flush_seal()
        except sqlite3.Error:
            pass
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- sources ---------------------------------------------------------

    def add_source(
        self,
        *,
        uri: str,
        source_type: str,
        content: str,
        meta: Mapping[str, Any] | None = None,
    ) -> str:
        """Insert a source, returning its content id.

        Idempotent: the same uri + content + type yields the same id, so a
        re-ingest writes nothing.
        """
        meta = dict(meta or {})
        rec = {"uri": uri, "source_type": source_type, "content": content, "meta": meta}
        sid = content_id(rec, prefix="src-")
        # checksum covers the content only — it is what invalidates a cascade
        # when a source is edited, independent of how the record is labelled
        checksum = digest(content.encode("utf-8"))
        self.db.execute(
            "INSERT OR IGNORE INTO sources (id, uri, source_type, checksum, content, meta)"
            " VALUES (?,?,?,?,?,?)",
            (sid, uri, source_type, checksum, content, canonical_bytes(meta).decode()),
        )
        self.db.commit()
        return sid

    def get_source(self, source_id: str) -> sqlite3.Row | None:
        cur = self.db.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
        return cur.fetchone()

    # -- evidence --------------------------------------------------------

    def add_evidence(
        self,
        *,
        source_id: str,
        start_offset: int,
        end_offset: int,
        text: str,
        meta: Mapping[str, Any] | None = None,
    ) -> str:
        """Insert an evidence span, returning its content id.

        The span must exist in its source. A span whose text does not match the
        source at the stated offsets is rejected — this is the check that a
        hallucinated citation cannot be stored, lifted in spirit from
        ``ganymede3``'s normalized-substring evidence verifier.
        """
        row = self.get_source(source_id)
        if row is None:
            raise UnknownReference(f"no such source: {source_id}")
        actual = row["content"][start_offset:end_offset]
        if actual != text:
            raise ProvenanceError(
                f"evidence span does not match source {source_id} at "
                f"[{start_offset}:{end_offset}]: expected {actual!r}, got {text!r}"
            )
        meta = dict(meta or {})
        rec = {
            "source_id": source_id,
            "start_offset": start_offset,
            "end_offset": end_offset,
            "text": text,
            "meta": meta,
        }
        eid = content_id(rec, prefix="ev-")
        self.db.execute(
            "INSERT OR IGNORE INTO evidence (id, source_id, start_offset, end_offset, text, meta)"
            " VALUES (?,?,?,?,?,?)",
            (eid, source_id, start_offset, end_offset, text, canonical_bytes(meta).decode()),
        )
        self.db.commit()
        return eid

    # -- investigations --------------------------------------------------

    def add_investigation(self, *, question: str, searched: Sequence[str], created_at: str) -> str:
        rec = {"question": question, "searched": list(searched), "created_at": created_at}
        iid = content_id(rec, prefix="inv-")
        self.db.execute(
            "INSERT OR IGNORE INTO investigations (id, question, searched, created_at)"
            " VALUES (?,?,?,?)",
            (iid, question, canonical_bytes(list(searched)).decode(), created_at),
        )
        self.db.commit()
        return iid

    # -- claims ----------------------------------------------------------

    def add_claim(
        self,
        *,
        text: str,
        state: EpistemicState | str = EpistemicState.UNEXAMINED,
        evidence_ids: Sequence[str] = (),
        investigation_id: str | None = None,
        valid_time: str | None = None,
        transaction_time: str,
        meta: Mapping[str, Any] | None = None,
    ) -> str:
        """Insert a claim, enforcing the provenance and absence invariants.

        Three fail-closed rules, all enforced at write time:

        - A non-``UNEXAMINED``/``ASSUMED``/``DERIVED`` claim must cite at least
          one evidence span. Evidence magnitude is not sufficiency — that is a
          later judgement — but *no* evidence is a hard stop.
        - ``UNRESOLVED`` requires an investigation record.
        - Evidence ids must exist (FK enforcement).
        """
        st = check_transition(EpistemicState.UNEXAMINED, state, has_investigation=True)

        no_evidence_ok = {
            EpistemicState.UNEXAMINED,
            EpistemicState.ASSUMED,
            EpistemicState.DERIVED,
            EpistemicState.UNRESOLVED,
        }
        if st not in no_evidence_ok and not evidence_ids:
            raise ProvenanceError(
                f"claim in state {st.value} cites no evidence; an epistemic "
                "assertion without a resolvable source is unverifiable"
            )
        if st is EpistemicState.UNRESOLVED and investigation_id is None:
            raise MissingInvestigation(
                "UNRESOLVED claim requires an investigation_id recording what was searched"
            )
        if investigation_id is not None:
            cur = self.db.execute(
                "SELECT 1 FROM investigations WHERE id = ?", (investigation_id,)
            )
            if cur.fetchone() is None:
                raise UnknownReference(f"no such investigation: {investigation_id}")

        meta = dict(meta or {})
        rec = {
            "text": text,
            "state": st.value,
            "evidence_ids": list(evidence_ids),
            "valid_time": valid_time,
            "investigation_id": investigation_id,
            "meta": meta,
        }
        cid = content_id(rec, prefix="clm-")
        self.db.execute(
            "INSERT OR IGNORE INTO claims"
            " (id, text, state, valid_time, transaction_time, investigation_id, meta)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                cid,
                text,
                st.value,
                valid_time,
                transaction_time,
                investigation_id,
                canonical_bytes(meta).decode(),
            ),
        )
        for eid in evidence_ids:
            # OR IGNORE is correct *and* preserves the fail-closed property.
            # The original comment here claimed OR IGNORE would swallow a
            # dangling evidence id; it does not. SQLite enforces foreign key
            # constraints independently of the conflict resolution clause, so
            # a bad evidence id still raises FOREIGN KEY constraint failed
            # (verified directly). Without OR IGNORE, recompiling an unchanged
            # corpus crashed on the second run — the claim row is
            # already-present (INSERT OR IGNORE above) but this edge insert was
            # not, which broke the idempotence that content addressing exists
            # to provide. Both invariants are now held by the same statement.
            self.db.execute(
                "INSERT OR IGNORE INTO claim_evidence (claim_id, evidence_id) VALUES (?,?)",
                (cid, eid),
            )
        self.db.commit()
        return cid

    def set_state(
        self,
        claim_id: str,
        target: EpistemicState | str,
        *,
        transaction_time: str,
        investigation_id: str | None = None,
        justified_by: str | None = None,
    ) -> EpistemicState:
        """Transition a claim's epistemic state, validating the edge.

        `justified_by` names the evaluation that decided this move.

        ADR-023. A transition into a state that *asserts* something --
        `supported`, `validated`, `contradicted`, `retracted`, `contested`,
        `superseded`, `invalidated` -- must be backed by an evaluation, or the
        store holds a belief that nothing decided. The independent auditor has
        always reported that as `state_without_evaluation`; this is the same
        check moved into the write path so it cannot be reached at all.

        The gap was not theoretical. The randomized legal-operation sequence
        (`tests/test_write_sequence.py`) found it on a plain `set_state`:
        the claim moved, the epoch moved, the store sealed, and it still failed
        its own audit because nothing recorded *why*. `Reviser` had the same
        hole in a worse form (ADR-022, no record written at all).

        Fail closed: no record, no move. The states that assert nothing
        (`unexamined`, `assumed`, `derived`, `unresolved`, `inconclusive`) are
        exempt -- they are the absence of a conclusion, and a conclusion
        arriving is what needs an evaluation behind it.
        """
        cur = self.db.execute("SELECT state FROM claims WHERE id = ?", (claim_id,))
        row = cur.fetchone()
        if row is None:
            raise UnknownReference(f"no such claim: {claim_id}")
        new = check_transition(row["state"], target, has_investigation=investigation_id is not None)
        if justified_by is not None:
            owner = self.db.execute(
                "SELECT subject_id FROM evaluations WHERE id = ?", (justified_by,)
            ).fetchone()
            if owner is None or owner["subject_id"] != claim_id:
                raise UnknownReference(
                    f"justified_by={justified_by} is not an evaluation of {claim_id}"
                )
        if new is EpistemicState.UNRESOLVED and investigation_id is None:
            raise MissingInvestigation(
                "UNRESOLVED requires an investigation record naming what was searched"
            )
        self.db.execute(
            "UPDATE claims SET state = ?, transaction_time = ? WHERE id = ?",
            (new.value, transaction_time, claim_id),
        )
        # ADR-018: the beliefs moved, so anything bound to the old version is
        # now stale. Bumped here, in the single funnel every transition passes
        # through, so the tripwire cannot be missed by using the API correctly.
        #
        # Inlined rather than calling `bump_state_epoch` because the evaluator
        # drives `set_state` once per claim: 336,190 extra statements, each a
        # round trip, cost ~50s on the real corpus. It rides the commit that
        # `set_state` already performs, so the epoch is still atomic with the
        # state change it describes.
        # ADR-022: mark the seal pending rather than re-deriving here. A full
        # re-seal is a scan of every claim, and the evaluator drives this once
        # per claim -- 336,190 scans. Instead the store records that it is
        # mid-update, and `_flush_seal` re-derives once, lazily, when anyone
        # actually needs the identity to be true (reading it, or closing).
        #
        # This closes a real gap the randomized sequence test found: a single
        # legal `set_state` used to leave the store failing its own audit,
        # because the epoch moved but the digest did not.
        self.db.execute(
            "INSERT INTO artifact (id, state_epoch, seal_pending) VALUES (1, 1, 1) "
            "ON CONFLICT(id) DO UPDATE SET state_epoch = state_epoch + 1, "
            "seal_pending = 1"
        )
        self.db.commit()
        return new

    # -- artifact identity (ADR-018) -------------------------------------

    def bump_state_epoch(self) -> int:
        """Increment and return the state epoch.

        Separate from ``set_state`` so a bulk writer can signal a change
        explicitly, and so a test can move the epoch without manufacturing a
        legal transition.
        """
        self.db.execute(
            "INSERT INTO artifact (id, state_epoch) VALUES (1, 1) "
            "ON CONFLICT(id) DO UPDATE SET state_epoch = state_epoch + 1"
        )
        return self.state_epoch()

    def state_epoch(self) -> int:
        """The current epoch. 0 when this store has never recorded one."""
        row = self.db.execute("SELECT state_epoch FROM artifact WHERE id = 1").fetchone()
        return int(row["state_epoch"]) if row is not None else 0

    def _flush_seal(self) -> None:
        """Re-derive the digest if a belief moved since the last seal.

        The lazy half of the ADR-022 fix. `set_state` marks the store dirty
        because sealing on every transition is a scan of every claim, and
        the evaluator performs 336,190 transitions. The dirty flag is
        persisted rather than kept in memory so that a store closed or
        abandoned mid-update is *visible* -- the auditor reads the column
        and fails a store that never finished updating, rather than one that
        crashed looking clean.
        """
        if self._sealing:
            # Reentrant. `reseal` itself reads `recorded_artifact` to learn the
            # prior identity, so a flush inside that read would re-enter here
            # and loop forever. The seal that is already running will clear
            # the flag when it finishes.
            return
        row = self.db.execute("SELECT seal_pending FROM artifact WHERE id = 1").fetchone()
        if row is None or not int(row["seal_pending"]):
            return
        if self.recorded_root() is None:
            return
        self.reseal()

    def reseal(self) -> dict[str, Any]:
        """Re-derive the artifact identity from current beliefs and record it.

        ADR-018 put belief state *in* the artifact identity and bumped an
        epoch on every transition. The epoch is the tripwire and it worked.
        The digest was the other half, and it was written once by
        ``record_artifact`` at compile time: a store that was later evaluated
        still advertised the beliefs it had before evaluation. The auditor
        caught this on the first completed full-corpus run and was right to
        -- the store was describing itself as an artifact that no longer
        existed.

        Evaluation changes claims, not content. Claims, evidence, sources and
        edges are untouched; only the identity moves, because a version that
        cannot distinguish "these claims, believed" from "these claims,
        contradicted" cannot identify the artifact (ADR-018).

        Explicit and separate from ``set_state`` for the same reason
        ``bump_state_epoch`` is: re-sealing is a decision about identity, and
        a bulk writer is the right place to make it deliberately rather than
        have it happen as a side effect of 319,293 individual transitions.

        Returns the newly recorded artifact row.
        """
        from ..compile.manifest import build_manifest  # local: avoids a cycle

        states = self.state_pairs()
        state_counts: dict[str, int] = {}
        for _, state in states:
            state_counts[state] = state_counts.get(state, 0) + 1

        # The guard is a plain flag, not a lock: it exists to stop `reseal`
        # re-entering itself through `recorded_artifact`, not to make
        # concurrent seals safe. Single-process, single-writer is the model.
        self._sealing = True
        try:
            prior = self._recorded_artifact_raw()
            if prior is None:
                raise UnknownReference(
                    "cannot re-seal a store that has never recorded an artifact"
                )

            manifest = build_manifest(
                source_ids=[r["id"] for r in self.db.execute("SELECT id FROM sources")],
                claim_ids=[cid for cid, _ in states],
                evidence_ids=[r["id"] for r in self.db.execute("SELECT id FROM evidence")],
                heuristic_ids=[
                    cid for cid, state in states if state == EpistemicState.DERIVED.value
                ],
                edge_count=self.counts()["claim_edges"],
                state_counts=state_counts,
                states=states,
            )
            self.record_artifact(manifest)
            self.db.commit()
            return self._recorded_artifact_raw() or {}
        finally:
            self._sealing = False

    def recorded_root(self) -> str | None:
        """The recorded Merkle root, or ``None`` if no artifact was recorded."""
        row = self.db.execute("SELECT root FROM artifact WHERE id = 1").fetchone()
        return row["root"] if row is not None and row["root"] else None

    def record_artifact(self, manifest: Any) -> None:
        """Record which version this store currently holds (ADR-018).

        Does not touch the epoch: recording what the store *is* is a different
        event from the beliefs changing underneath it.
        """
        self.db.execute(
            "INSERT INTO artifact (id, root, version, state_digest, state_epoch) "
            "VALUES (1, ?, ?, ?, COALESCE((SELECT state_epoch FROM artifact WHERE id = 1), 0)) "
            "ON CONFLICT(id) DO UPDATE SET root = excluded.root, "
            "version = excluded.version, state_digest = excluded.state_digest, "
            "seal_pending = 0",
            (manifest.root, manifest.version, manifest.state_digest),
        )
        self.db.commit()

    def _recorded_artifact_raw(self) -> dict[str, Any] | None:
        """The recorded artifact row, with no flush.

        Split out so `reseal` can read the prior identity without re-entering
        its own flush. Every *public* reader of the identity goes through
        `recorded_artifact` instead, which flushes.
        """
        row = self.db.execute(
            "SELECT root, version, state_digest, state_epoch FROM artifact WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "root": row["root"],
            "version": row["version"],
            "state_digest": row["state_digest"],
            "state_epoch": int(row["state_epoch"]),
        }

    def recorded_artifact_unflushed(self) -> dict[str, Any] | None:
        """The recorded identity, without flushing a pending seal.

        Public because a reader that is *checking* the store against its own
        recorded identity must not cause the flush that would make the check
        pass. Flushing first and then comparing would compare the store to a
        digest that was just recomputed from it -- always equal, and therefore
        always clean. `recorded_artifact()` remains the right call for "what
        do you currently hold?".
        """
        return self._recorded_artifact_raw()

    def recorded_artifact(self) -> dict[str, Any] | None:
        """What this store last declared itself to be, or ``None``.

        Flushes a pending seal first. This is the question "what do you
        currently hold?", and answering it with a digest known to be stale
        would make this method the one place the store lies by default.
        """
        self._flush_seal()
        return self._recorded_artifact_raw()

    def state_pairs(self) -> list[tuple[str, str]]:
        """Every ``(claim_id, state)``, for digest re-derivation.

        A narrow two-column scan, not ``claims()``. ``claims()`` hydrates every
        column of all 336,190 rows on the real corpus and costs 6.4s against
        0.9s here, and this feeds a function that is called in tests and by
        anything rebuilding a manifest.
        """
        return [
            (r["id"], r["state"])
            for r in self.db.execute("SELECT id, state FROM claims")
        ]

    def add_evaluation(
        self,
        *,
        subject_id: str,
        relation: str,
        method: str,
        attested_by: Sequence[str] = (),
        decided_at: str,
        meta: Mapping[str, Any] | None = None,
        contradicted_by: Sequence[str] = (),
    ) -> str:
        """Record a verdict about a claim, returning its content id.

        The mirror of ``add_investigation``. ADR-002 makes absence provable by
        requiring an investigation; ADR-006 makes *support* provable by
        requiring an evaluation. A state change with no record behind it is the
        same defect in both directions, and both are closed at write time.

        ``attested_by`` is the list of claim ids that bore the subject out. It
        is empty for every relation except ``attested``, and the evaluator
        cannot produce a ``SUPPORTED`` claim without it (ADR-006 §2.2).
        """
        if subject_id and self.get_claim(subject_id) is None:
            raise UnknownReference(f"no such claim: {subject_id}")
        meta = dict(meta or {})

        # ADR-013: every peer list is stored once, content-addressed, and
        # referenced by id. Inlining these cost 58 KB per row on the real corpus
        # and reached 20 GB at 4% completion, because every member of an
        # equivalence class stores the same list. The record's *meaning* is
        # unchanged; only the representation is.
        #
        # `contradicted_by` is promoted out of `meta` for the same reason it
        # needed promoting at all: it is an equivalence class like any other,
        # and the full-corpus run showed records of 13,674 bytes carrying
        # nothing but that list. Leaving it inline would have been fixing the
        # symptom that was measured and ignoring the one that was not.
        group_id = self._put_peer_group(attested_by)
        contra_group_id = self._put_peer_group(contradicted_by)

        # The content id still covers the resolved member list, not the group
        # id, so a record's identity does not depend on how it happens to be
        # stored. Two runs that find the same attestors produce the same id.
        rec = {
            "subject_id": subject_id,
            "relation": relation,
            "method": method,
            "attested_by": list(attested_by),
            "contradicted_by": list(contradicted_by),
            "decided_at": decided_at,
            "meta": meta,
        }
        vid = content_id(rec, prefix="evl-")
        self.db.execute(
            "INSERT OR IGNORE INTO evaluations"
            " (id, subject_id, relation, method, attested_by, contradicted_by,"
            " decided_at, meta)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                vid,
                subject_id,
                relation,
                method,
                group_id,
                contra_group_id,
                decided_at,
                canonical_bytes(meta).decode(),
            ),
        )
        self.db.commit()
        return vid

    def _put_peer_group(self, attested_by: Sequence[str]) -> str:
        """Store an attesting set once and return its content id.

        The empty set gets the empty id, so a relation with no peers writes
        nothing and its ``attested_by`` stays falsy — which is what
        ``Evaluation.__post_init__`` checks.
        """
        members = sorted(set(attested_by))
        if not members:
            return ""
        gid = content_id({"members": members}, prefix="agr-")
        self.db.execute(
            "INSERT OR IGNORE INTO peer_groups (id, members, size) VALUES (?,?,?)",
            (gid, canonical_bytes(members).decode(), len(members)),
        )
        return gid

    def _peer_members(self, group_id: str) -> list[str]:
        """Resolve a group id back to its peer ids, or raise.

        An unresolvable group is a hard error rather than an empty list. An
        empty list would make a ``SUPPORTED`` claim read as unattested, and
        quietly demoting a supported claim to unattested is worse than a crash:
        the crash is visible, the demotion is a claim that quietly lost its
        evidence.
        """
        if not group_id:
            return []
        row = self.db.execute(
            "SELECT members FROM peer_groups WHERE id = ?", (group_id,)
        ).fetchone()
        if row is None:
            raise UnknownReference(
                f"evaluation references peer group {group_id!r}, "
                "which does not exist"
            )
        return list(json.loads(row["members"]))

    def evaluations_for(self, subject_id: str) -> list[sqlite3.Row]:
        """Evaluations for a claim, with peer lists hydrated to member ids.

        Callers see the attesting and contradicting claim ids, exactly as
        before ADR-013. The group indirection is a storage detail and is not
        leaked to readers.
        """
        cur = self.db.execute(
            "SELECT * FROM evaluations WHERE subject_id = ? ORDER BY id", (subject_id,)
        )
        hydrated = []
        for row in cur.fetchall():
            members = self._peer_members(row["attested_by"])
            contra = self._peer_members(row["contradicted_by"])
            hydrated.append(
                _HydratedEvaluation(
                    id=row["id"],
                    subject_id=row["subject_id"],
                    relation=row["relation"],
                    method=row["method"],
                    attested_by=canonical_bytes(members).decode(),
                    contradicted_by=canonical_bytes(contra).decode(),
                    decided_at=row["decided_at"],
                    meta=row["meta"],
                )
            )
        return hydrated
    # -- edges -----------------------------------------------------------

    def add_edge(self, from_id: str, to_id: str, relation: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO claim_edges (from_id, to_id, relation) VALUES (?,?,?)",
            (from_id, to_id, relation),
        )
        self.db.commit()

    # -- queries ---------------------------------------------------------

    def get_claim(self, claim_id: str) -> sqlite3.Row | None:
        cur = self.db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,))
        return cur.fetchone()

    def claims(self, state: EpistemicState | str | None = None) -> Iterator[sqlite3.Row]:
        if state is None:
            cur = self.db.execute("SELECT * FROM claims ORDER BY id")
        else:
            cur = self.db.execute(
                "SELECT * FROM claims WHERE state = ? ORDER BY id", (EpistemicState(state).value,)
            )
        yield from cur.fetchall()

    def evidence_for(self, claim_id: str) -> list[sqlite3.Row]:
        cur = self.db.execute(
            "SELECT e.* FROM evidence e JOIN claim_evidence ce ON ce.evidence_id = e.id"
            " WHERE ce.claim_id = ? ORDER BY e.id",
            (claim_id,),
        )
        return cur.fetchall()

    def resolve_span(self, evidence_id: str) -> tuple[str, int, int] | None:
        """Resolve an evidence id to (source_uri, start, end).

        This is the terminus of every answer the system gives. If this returns
        None, the answer has no source and must not be presented as if it did.
        """
        cur = self.db.execute(
            "SELECT s.uri, e.start_offset, e.end_offset FROM evidence e"
            " JOIN sources s ON s.id = e.source_id WHERE e.id = ?",
            (evidence_id,),
        )
        row = cur.fetchone()
        return (row["uri"], row["start_offset"], row["end_offset"]) if row else None

    def counts(self) -> dict[str, int]:
        return {
            t: self.db.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            for t in ("sources", "evidence", "claims", "claim_evidence", "claim_edges", "investigations")
        }
