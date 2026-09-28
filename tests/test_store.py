"""Store tests.

These assert the two properties the whole system rests on: provenance is
mechanically enforced, and absence is provable. Both are checked by attempting
the violation, not by inspecting the schema.
"""

from __future__ import annotations

import sqlite3

import pytest

from sovereign_runtime.knowledge.epistemic import EpistemicState, MissingInvestigation
from sovereign_runtime.knowledge.store import (
    ProvenanceError,
    Store,
    UnknownReference,
)

TEXT = "The sky is blue. The grass is green. Water is wet."


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    yield s
    s.close()


@pytest.fixture()
def src(store):
    return store.add_source(uri="test://corpus", source_type="note", content=TEXT)


class TestContentAddressing:
    def test_same_source_ingested_twice_writes_nothing(self, store):
        a = store.add_source(uri="test://x", source_type="note", content="hello")
        before = store.counts()["sources"]
        b = store.add_source(uri="test://x", source_type="note", content="hello")
        assert a == b
        assert store.counts()["sources"] == before

    def test_changed_content_is_a_new_source(self, store):
        a = store.add_source(uri="test://x", source_type="note", content="hello")
        b = store.add_source(uri="test://x", source_type="note", content="hello world")
        assert a != b

    def test_different_uri_same_content_is_a_different_source(self, store):
        a = store.add_source(uri="test://a", source_type="note", content="same")
        b = store.add_source(uri="test://b", source_type="note", content="same")
        assert a != b


class TestEvidenceProvenance:
    def test_valid_span_accepted(self, store, src):
        eid = store.add_evidence(
            source_id=src, start_offset=0, end_offset=15, text="The sky is blue"
        )
        assert eid.startswith("ev-")

    def test_span_text_must_match_source(self, store, src):
        # the hallucinated-citation guard: a span whose text does not appear at
        # the stated offsets cannot be stored
        with pytest.raises(ProvenanceError, match="does not match source"):
            store.add_evidence(
                source_id=src, start_offset=0, end_offset=15, text="Mountains are purple"
            )

    def test_wrong_offset_rejected(self, store, src):
        with pytest.raises(ProvenanceError):
            store.add_evidence(
                source_id=src, start_offset=0, end_offset=9, text="The sky is blue"
            )

    def test_unknown_source_rejected(self, store):
        with pytest.raises(UnknownReference):
            store.add_evidence(
                source_id="src-nope", start_offset=0, end_offset=1, text="x"
            )

    def test_identical_span_twice_is_idempotent(self, store, src):
        kwargs = dict(source_id=src, start_offset=0, end_offset=15, text="The sky is blue")
        assert store.add_evidence(**kwargs) == store.add_evidence(**kwargs)
        assert store.counts()["evidence"] == 1

    def test_resolve_span_returns_uri_and_offsets(self, store, src):
        eid = store.add_evidence(
            source_id=src, start_offset=4, end_offset=15, text="sky is blue"
        )
        assert store.resolve_span(eid) == ("test://corpus", 4, 15)

    def test_resolve_span_of_unknown_id_is_none(self, store):
        assert store.resolve_span("ev-nope") is None


class TestForeignKeysAreReal:
    def test_claim_cannot_cite_nonexistent_evidence(self, store):
        with pytest.raises(sqlite3.IntegrityError):
            store.add_claim(
                text="bogus",
                state=EpistemicState.SUPPORTED,
                evidence_ids=["ev-does-not-exist"],
                transaction_time="t0",
            )

    def test_foreign_keys_pragma_is_on(self, store):
        # the pragma defaults to OFF in SQLite; if this ever regresses, every
        # provenance guarantee above becomes decorative
        assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


class TestClaimProvenance:
    def test_supported_claim_requires_evidence(self, store):
        with pytest.raises(ProvenanceError, match="cites no evidence"):
            store.add_claim(text="x", state=EpistemicState.SUPPORTED, transaction_time="t0")

    def test_supported_claim_with_evidence_accepted(self, store, src):
        eid = store.add_evidence(
            source_id=src, start_offset=0, end_offset=15, text="The sky is blue"
        )
        cid = store.add_claim(
            text="The sky is blue.",
            state=EpistemicState.SUPPORTED,
            evidence_ids=[eid],
            transaction_time="t0",
        )
        assert len(store.evidence_for(cid)) == 1

    def test_unexamined_claim_needs_no_evidence(self, store):
        cid = store.add_claim(text="?", transaction_time="t0")
        assert store.get_claim(cid)["state"] == "unexamined"

    def test_assumed_claim_needs_no_evidence(self, store):
        cid = store.add_claim(text="axiom", state=EpistemicState.ASSUMED, transaction_time="t0")
        assert store.get_claim(cid)["state"] == "assumed"

    def test_derived_claim_needs_no_evidence(self, store):
        cid = store.add_claim(text="mined", state=EpistemicState.DERIVED, transaction_time="t0")
        assert store.get_claim(cid)["state"] == "derived"


class TestAbsenceIsProvable:
    def test_unresolved_without_investigation_rejected(self, store):
        with pytest.raises(MissingInvestigation):
            store.add_claim(text="?", state=EpistemicState.UNRESOLVED, transaction_time="t0")

    def test_unresolved_with_investigation_accepted(self, store):
        iid = store.add_investigation(
            question="what colour is the sky?",
            searched=["corpus", "notes"],
            created_at="t0",
        )
        cid = store.add_claim(
            text="no answer for sky colour",
            state=EpistemicState.UNRESOLVED,
            investigation_id=iid,
            transaction_time="t0",
        )
        assert store.get_claim(cid)["state"] == "unresolved"

    def test_unresolved_with_unknown_investigation_rejected(self, store):
        with pytest.raises(UnknownReference):
            store.add_claim(
                text="?",
                state=EpistemicState.UNRESOLVED,
                investigation_id="inv-nope",
                transaction_time="t0",
            )

    def test_transition_to_unresolved_requires_investigation(self, store, src):
        eid = store.add_evidence(
            source_id=src, start_offset=0, end_offset=15, text="The sky is blue"
        )
        cid = store.add_claim(
            text="sky", state=EpistemicState.SUPPORTED, evidence_ids=[eid], transaction_time="t0"
        )
        with pytest.raises(MissingInvestigation):
            store.set_state(cid, EpistemicState.UNRESOLVED, transaction_time="t1")

    def test_investigation_records_what_was_searched(self, store):
        iid = store.add_investigation(
            question="q", searched=["a.md", "b.md"], created_at="t0"
        )
        row = store.db.execute(
            "SELECT searched FROM investigations WHERE id = ?", (iid,)
        ).fetchone()
        assert "a.md" in row["searched"]


class TestStateTransitions:
    def test_legal_transition_persists(self, store, src):
        eid = store.add_evidence(
            source_id=src, start_offset=0, end_offset=15, text="The sky is blue"
        )
        cid = store.add_claim(
            text="sky", state=EpistemicState.SUPPORTED, evidence_ids=[eid], transaction_time="t0"
        )
        store.set_state(cid, EpistemicState.VALIDATED, transaction_time="t1")
        assert store.get_claim(cid)["state"] == "validated"

    def test_illegal_transition_raises_and_leaves_state_intact(self, store):
        cid = store.add_claim(
            text="axiom", state=EpistemicState.ASSUMED, transaction_time="t0"
        )
        store.set_state(cid, EpistemicState.RETRACTED, transaction_time="t1")
        with pytest.raises(Exception):
            store.set_state(cid, EpistemicState.SUPPORTED, transaction_time="t2")
        assert store.get_claim(cid)["state"] == "retracted"

    def test_transition_unknown_claim_raises(self, store):
        with pytest.raises(UnknownReference):
            store.set_state("clm-nope", EpistemicState.SUPPORTED, transaction_time="t1")


class TestDeterminism:
    def test_two_stores_from_same_input_are_identical(self, tmp_path):
        def build(path):
            s = Store(str(path))
            sid = s.add_source(uri="u", source_type="note", content=TEXT)
            eid = s.add_evidence(
                source_id=sid, start_offset=0, end_offset=15, text="The sky is blue"
            )
            s.add_claim(
                text="blue",
                state=EpistemicState.SUPPORTED,
                evidence_ids=[eid],
                transaction_time="t0",
            )
            counts = s.counts()
            s.close()
            return counts

        assert build(tmp_path / "a.db") == build(tmp_path / "b.db")

    def test_same_input_same_claim_id_across_stores(self, tmp_path):
        def claim_id_for(path):
            s = Store(str(path))
            sid = s.add_source(uri="u", source_type="note", content=TEXT)
            eid = s.add_evidence(
                source_id=sid, start_offset=0, end_offset=15, text="The sky is blue"
            )
            cid = s.add_claim(
                text="blue",
                state=EpistemicState.SUPPORTED,
                evidence_ids=[eid],
                transaction_time="t0",
            )
            s.close()
            return cid

        assert claim_id_for(tmp_path / "a.db") == claim_id_for(tmp_path / "b.db")

    def test_transaction_time_is_not_part_of_identity(self, tmp_path):
        # bitemporal history: the same fact asserted at a different time is the
        # same claim, recorded again — not a new claim
        def claim_id_for(path, tt):
            s = Store(str(path))
            sid = s.add_source(uri="u", source_type="note", content=TEXT)
            eid = s.add_evidence(
                source_id=sid, start_offset=0, end_offset=15, text="The sky is blue"
            )
            cid = s.add_claim(
                text="blue",
                state=EpistemicState.SUPPORTED,
                evidence_ids=[eid],
                transaction_time=tt,
            )
            s.close()
            return cid

        assert claim_id_for(tmp_path / "a.db", "t0") == claim_id_for(tmp_path / "b.db", "t9")


class TestEdges:
    def test_edge_recorded(self, store, src):
        e = store.add_evidence(
            source_id=src, start_offset=0, end_offset=15, text="The sky is blue"
        )
        a = store.add_claim(
            text="blue", state=EpistemicState.SUPPORTED, evidence_ids=[e], transaction_time="t0"
        )
        b = store.add_claim(
            text="blue and cloudy", state=EpistemicState.SUPPORTED, evidence_ids=[e],
            transaction_time="t0",
        )
        store.add_edge(a, b, "SUPPORTS")
        n = store.db.execute("SELECT COUNT(*) AS n FROM claim_edges").fetchone()["n"]
        assert n == 1

    def test_edge_to_unknown_claim_rejected(self, store):
        with pytest.raises(sqlite3.IntegrityError):
            store.add_edge("clm-nope", "clm-also-nope", "SUPPORTS")
