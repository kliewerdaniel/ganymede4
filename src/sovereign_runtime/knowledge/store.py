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

import sqlite3
from typing import Any, Iterator, Mapping, Sequence

from ..core.canonical import canonical_bytes
from ..core.content import content_id, digest
from .epistemic import EpistemicState, MissingInvestigation, check_transition

__all__ = ["Store", "SCHEMA_VERSION", "ProvenanceError", "UnknownReference"]

SCHEMA_VERSION = 1

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
"""


class ProvenanceError(Exception):
    """A record was written without resolvable provenance.

    Fail-closed: a claim that cannot name its source is not a degraded claim,
    it is an unverifiable one, and it is rejected rather than stored with a
    warning.
    """


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
        self.db.commit()

    def close(self) -> None:
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
            # No OR IGNORE: a dangling evidence id must raise, not be skipped.
            # OR IGNORE would swallow exactly the FK violation that proves
            # provenance is real.
            self.db.execute(
                "INSERT INTO claim_evidence (claim_id, evidence_id) VALUES (?,?)",
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
    ) -> EpistemicState:
        """Transition a claim's epistemic state, validating the edge."""
        cur = self.db.execute("SELECT state FROM claims WHERE id = ?", (claim_id,))
        row = cur.fetchone()
        if row is None:
            raise UnknownReference(f"no such claim: {claim_id}")
        new = check_transition(row["state"], target, has_investigation=investigation_id is not None)
        if new is EpistemicState.UNRESOLVED and investigation_id is None:
            raise MissingInvestigation(
                "UNRESOLVED requires an investigation record naming what was searched"
            )
        self.db.execute(
            "UPDATE claims SET state = ?, transaction_time = ? WHERE id = ?",
            (new.value, transaction_time, claim_id),
        )
        self.db.commit()
        return new

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
