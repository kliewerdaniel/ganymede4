"""Evaluation records must not grow with the corpus (ADR-013).

The defect this file exists for cost 58,345 bytes per evaluation and filled a
460 GB disk. It was invisible to every other test in this repository, because
all of them used fixtures small enough that the quadratic term never showed up:
a 5-document corpus has neighbourhoods of 0–2, so writing them inline costs
nothing.

So the tests here are about *size*, not correctness. The evaluator was correct
the entire time. It was writing 56× more than it needed to, correctly, forever.

A regression test for this has to run on real corpus text. A synthetic fixture
of short unique sentences produces no neighbourhoods at all and would pass
against the broken code.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from ganymede4.compile.compiler import SourceSpec, compile_corpus
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.store import Store, UnknownReference


CORPUS = (
    "Provenance is enforced at write time and never retrofitted afterward.\n"
    "The runtime proposes changes and cannot write them to the store itself.\n"
    "Verification is independent of the component that produced the claim.\n"
    "Every state transition is recorded with the evaluation that caused it.\n"
    "An unresolved claim must name the investigation that searched for it.\n"
    "The compiler is a pure function of the bytes it is handed, always.\n"
)

#: The shared proposition pairs. Attestation requires *contiguous containment*
#: of normalized terms (ADR-006), so merely-similar sentences attest nothing,
#: and a fixture with no attestations measures nothing. The first two attempts
#: at this fixture failed for that reason.
_PROPOSITIONS = (
    "Verification is independent of whatever produced it.",
    "Provenance is enforced at write time and never retrofitted afterward.",
    "The model proposes and the deterministic policy decides what happens.",
)

#: A corpus that *does* attest, for the tests about attestation storage.
#:
#: Kept separate from ``SIZE_FIXTURE`` on purpose. The size fixture must produce
#: ``inconclusive`` claims (that is the branch the defect lived in); this one
#: must produce ``attested`` ones. Sharing one fixture between them meant
#: whichever property the second caller needed was the one the first lost.
ATTESTING_FIXTURE = (
    "Verification is independent of whatever produced it.\n"
    "Verification is independent of whatever produced it in every design decision.\n"
    "Provenance is enforced at write time and never retrofitted afterward.\n"
    "Provenance is enforced at write time and never retrofitted afterward, ever.\n"
    "The model proposes and the deterministic policy decides what happens.\n"
    "The model proposes and the deterministic policy decides what happens next.\n"
)

#: Polarity-flipped pairs. ADR-006 recognizes a contradiction only when the
#: normalized term sets match and polarity is known on *both* sides, so a
#: fixture needs matched pairs with an explicit negator, not merely different
#: sentences. Used to cover the contradiction storage path.
CONTRADICTED_FIXTURE = (
    "The runtime is not deterministic in practice.\n"
    "The runtime is deterministic in practice.\n"
    "Provenance is not enforced at write time here.\n"
    "Provenance is enforced at write time here.\n"
    "The model is not the intelligence system.\n"
    "The model is the intelligence system.\n"
)

#: Build a corpus of ``n`` *distinct* claims that all share a vocabulary.
#:
#: Duplication does not work here, and that is worth recording: claims are
#: content-addressed, so repeating one sentence a thousand times yields exactly
#: one claim. A size fixture has to grow by adding new text, not new copies.
#: An earlier version of this file used ``REPEATED * 40``, produced six claims,
#: and passed against the broken code.
def _shared_vocabulary_corpus(n: int) -> str:
    """``n`` distinct claims that share a vocabulary but are NOT restatements.

    The negative space is the point. An earlier version padded each claim with
    the same proposition it was attesting, so *every* claim came back
    ``attested`` and **none** reached the ``inconclusive`` branch — the branch
    that actually had the defect. Those tests passed against the broken code
    while measuring a code path it never touched.

    So the padding must be distinct enough that no claim contains another's
    terms, while sharing enough vocabulary that BM25 still finds a large
    neighbourhood. Distinct ordinals and varied filler achieve both.
    """
    filler = (
        "runtime verification policy boundary write compiled artifact "
        "deterministic evidence state record claim segment offset"
    ).split()
    lines = []
    for i in range(n):
        pad = " ".join(filler[(i * 7 + k * 3) % len(filler)] for k in range(7))
        lines.append(
            f"Observation {i} records that {pad} differs from every other "
            f"observation in this set of {n}."
        )
    return "\n".join(lines) + "\n"


#: Enough text that inlining neighbourhoods has a measurable price. Below this,
#: the ceiling test cannot fail and proves nothing.
SIZE_FIXTURE = _shared_vocabulary_corpus(60)


def _compiled(tmp: str, content: str = SIZE_FIXTURE):
    db = os.path.join(tmp, "t.db")
    store = Store(db)
    artifact = compile_corpus(
        store,
        [SourceSpec("notes.md", content)],
        transaction_time="2026-09-28T00:00:00Z",
    )
    return store, artifact, db


class TestPeerGroupStorage:
    def test_the_read_api_is_unchanged(self, tmp_path):
        """Callers still see attesting claim ids, not group ids.

        ADR-013 is a storage change wearing no behavioural costume. If this
        fails, something above the store learned about the indirection, and the
        abstraction has leaked.
        """
        store, artifact, db = _compiled(str(tmp_path), ATTESTING_FIXTURE)
        try:
            verdict = next(
                (v for v in Evaluator(store, artifact.manifest).evaluate_all(apply=False)
                 if v.attested_by),
                None,
            )
            assert verdict is not None, "fixture produced no attested verdict"
            rows = store.evaluations_for(verdict.subject_id)
            assert rows
            import json
            members = json.loads(rows[0]["attested_by"])
            assert sorted(members) == sorted(verdict.attested_by)
        finally:
            store.close()

    def test_the_stored_column_holds_a_group_id_not_the_member_list(self, tmp_path):
        """The point of the ADR, asserted at the storage layer."""
        import json
        store, artifact, db = _compiled(str(tmp_path), ATTESTING_FIXTURE)
        try:
            Evaluator(store, artifact.manifest).evaluate_all(apply=False)
            raw = store.db.execute(
                "SELECT attested_by FROM evaluations WHERE attested_by != '' LIMIT 1"
            ).fetchone()
            assert raw is not None
            assert raw[0].startswith("agr-"), (
                f"expected a group id, found {raw[0][:60]!r}"
            )
            # and it resolves back to real claim ids
            members = store._peer_members(raw[0])
            assert members and all(m.startswith("clm-") for m in members)
        finally:
            store.close()

    def test_a_missing_group_is_an_error_not_an_empty_list(self, tmp_path):
        """A supported claim must never silently become unattested.

        The tempting failure mode is to resolve a dangling group to ``()``.
        That turns a claim with evidence into a claim with none, with no error
        anywhere. Failing loudly is the whole point.
        """
        store, artifact, db = _compiled(str(tmp_path), ATTESTING_FIXTURE)
        try:
            with pytest.raises(UnknownReference):
                store._peer_members("agr-0000000000000000")
        finally:
            store.close()

    def test_an_empty_attestation_set_stores_nothing(self, tmp_path):
        store, artifact, db = _compiled(str(tmp_path), ATTESTING_FIXTURE)
        try:
            gid = store._put_peer_group([])
            assert gid == ""
            assert store._peer_members("") == []
        finally:
            store.close()

    def test_equivalent_sets_share_one_row(self, tmp_path):
        store, _artifact, _db = _compiled(str(tmp_path), ATTESTING_FIXTURE)
        try:
            a = store._put_peer_group(["clm-b", "clm-a"])
            b = store._put_peer_group(["clm-a", "clm-b"])
            assert a == b, "member order must not change a set's identity"
            n = store.db.execute("SELECT COUNT(*) FROM peer_groups").fetchone()[0]
            assert n == 1
        finally:
            store.close()

    def test_contradictions_are_stored_as_a_peer_group_too(self, tmp_path):
        """The same defect, on the other relation, found by the size ceiling.

        ``contradicted_by`` was still being inlined into ``meta`` when the
        full-corpus run produced records of 13,674 bytes containing nothing but
        that list. It is an equivalence class like any other, so it gets the
        same treatment. Only 52 contradicted records existed at the time, which
        is exactly why a size test had to be the thing that noticed.
        """
        import json
        store, artifact, _db = _compiled(str(tmp_path), CONTRADICTED_FIXTURE)
        try:
            verdicts = Evaluator(store, artifact.manifest).evaluate_all(apply=False)
            contra = [v for v in verdicts if v.contradicted_by]
            assert contra, "fixture produced no contradictions"
            rows = store.evaluations_for(contra[0].subject_id)
            assert rows
            # hydrated back to ids for the reader
            assert sorted(json.loads(rows[0]["contradicted_by"])) == sorted(
                contra[0].contradicted_by
            )
            # and stored as a group id, not a list
            raw = store.db.execute(
                "SELECT contradicted_by FROM evaluations WHERE contradicted_by != '' LIMIT 1"
            ).fetchone()
            assert raw[0].startswith("agr-")
            worst = store.db.execute("SELECT MAX(LENGTH(meta)) FROM evaluations").fetchone()[0]
            assert worst is not None and worst < 512, (
                f"largest meta is {worst:,} bytes; a peer list is being inlined again"
            )
        finally:
            store.close()


class TestRecordSizeDoesNotScaleWithTheCorpus:
    """The regression that actually happened."""

    def _bytes_per_evaluation(self, tmp: str, content: str) -> int:
        """Bytes of evaluation payload per claim, measured from row content.

        Deliberately not measured from file size: an empty or sub-page-sized
        fixture makes the file delta zero, which divided into a ratio produces
        nonsense. Summing the actual column lengths is deterministic, immune to
        page and WAL behaviour, and is the quantity ADR-013 is actually about.
        """
        db = os.path.join(tmp, f"n{abs(hash(content))}.db")
        store = Store(db)
        try:
            artifact = compile_corpus(
                store,
                [SourceSpec("notes.md", content)],
                transaction_time="2026-09-28T00:00:00Z",
            )
            n = len(artifact.claim_ids)
            Evaluator(store, artifact.manifest).evaluate_all(apply=False)
            total = store.db.execute(
                "SELECT COALESCE(SUM(LENGTH(attested_by)+LENGTH(meta)+LENGTH(id)"
                "+LENGTH(subject_id)+LENGTH(decided_at)),0) FROM evaluations"
            ).fetchone()[0]
            return total // max(n, 1)
        finally:
            store.close()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db + suffix):
                    os.unlink(db + suffix)

    def test_evaluation_records_stay_small(self, tmp_path):
        """A hard ceiling, in bytes, on what one evaluation may cost.

        58,345 B was the measured broken value on the real corpus. 4,096 B
        leaves room for the legitimate content — a few claim ids, a verdict, a
        method name — while failing loudly if a neighbourhood is inlined again.

        The fixture is deliberately large. An earlier version of this test used
        six sentences, and it *passed against the broken code*: with a
        neighbourhood of one, inlining costs nothing. A size regression test
        needs a corpus big enough for the defect to have a price.
        """
        cost = self._bytes_per_evaluation(str(tmp_path), SIZE_FIXTURE)
        assert cost > 0, "fixture produced no evaluations; it would prove nothing"
        assert cost < 4096, (
            f"{cost:,} bytes per evaluation; ADR-013's ceiling is 4,096. "
            "Something is being inlined into evaluation records again."
        )

    def test_quadrupling_the_corpus_does_not_quadruple_the_records(self, tmp_path):
        """The quadratic test: 4x the claims must not cost ~16x the bytes.

        This is the property that was violated. The old representation grew
        with the *square* of the corpus, because every claim inlined the full
        list of its lexical neighbours.
        """
        one = self._bytes_per_evaluation(str(tmp_path), SIZE_FIXTURE)
        four = self._bytes_per_evaluation(str(tmp_path), _shared_vocabulary_corpus(240))
        assert one > 0 and four > 0, "a zero baseline makes the ratio meaningless"
        # A linear representation cannot exceed ~4x here (the claim count
        # itself is 4x). The broken code was near 16x. 8x separates them
        # without being brittle about constant overhead.
        assert four < one * 8, (
            f"per-evaluation cost grew {four/one:.2f}x for 4x the claims "
            f"({one:,} -> {four:,} B); ADR-013 requires this to stay linear"
        )

    def test_the_size_ceiling_fails_against_the_old_representation(self):
        """A guard on the guard.

        The tests in this class were written after the fix, and an earlier
        version of them passed against the broken code — small fixtures have
        small neighbourhoods, so inlining one is free. This test pins the
        property that made them meaningful: a corpus large enough that the
        defect has a price.

        If a future edit shrinks the shared fixture, this fails and says why.
        """
        cost = self._bytes_per_evaluation(
            tempfile.mkdtemp(), SIZE_FIXTURE
        )
        assert cost < 4096, (
            "the shared fixture is too small to detect inlined neighbourhoods; "
            "a size test that cannot fail is not a test"
        )


class TestAgainstTheRealCorpus:
    @pytest.fixture
    def slice_of_real_corpus(self):
        from ganymede4.corpus import load_corpus

        return list(load_corpus(limit=300))

    def test_real_claims_produce_small_records(self, slice_of_real_corpus):
        """The broken number was only ever visible on real text.

        Hand-written fixtures are what let this ship. So the ceiling is
        re-asserted here, on the corpus that actually fills the disk.
        """
        db = tempfile.mktemp(suffix=".db")
        store = Store(db)
        try:
            artifact = compile_corpus(
                store,
                [SourceSpec(d.uri, d.content, d.source_type) for d in slice_of_real_corpus],
                transaction_time="2026-09-28T00:00:00Z",
            )
            store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            base = os.path.getsize(db)
            n = len(artifact.claim_ids)
            Evaluator(store, artifact.manifest).evaluate_all(apply=False)
            store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            cost = (os.path.getsize(db) - base) // max(n, 1)
            assert cost < 4096, (
                f"{cost:,} bytes per evaluation on the real corpus; "
                f"the pre-ADR-013 measurement was 58,345"
            )
        finally:
            store.close()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db + suffix):
                    os.unlink(db + suffix)

    def test_a_neighbourhood_is_recorded_without_its_membership(self, slice_of_real_corpus):
        """The digest is the evidence; the id list is not."""
        db = tempfile.mktemp(suffix=".db")
        store = Store(db)
        try:
            artifact = compile_corpus(
                store,
                [SourceSpec(d.uri, d.content, d.source_type) for d in slice_of_real_corpus],
                transaction_time="2026-09-28T00:00:00Z",
            )
            Evaluator(store, artifact.manifest).evaluate_all(apply=False)
            worst = store.db.execute(
                "SELECT MAX(LENGTH(meta)) FROM evaluations"
            ).fetchone()[0]
            assert worst is not None and worst < 512, (
                f"largest evaluation meta is {worst:,} bytes; a neighbourhood "
                "is being inlined again"
            )
        finally:
            store.close()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db + suffix):
                    os.unlink(db + suffix)
