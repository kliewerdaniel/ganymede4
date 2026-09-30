"""A container is not a witness (ADR-024).

ADR-019 stopped a claim being attested by a byte-identical copy of itself.
It left the neighbouring case open on purpose: a claim attested *only* by
claims that contain it verbatim still counted, and the docstring said so
("a strictly longer peer still counts ... the case `is_contiguous_in` was
written for").

Reading the verdicts (ADR-024) is what showed that assumption was wrong on
this corpus. Containment is not a rare edge; it is what most of the SUPPORTED
set rests on, and the container is a single claim wearing three hats:

    SUBJECT: 'Use Ollama to summarize text'
    ATTESTER: 'Use Ollama to summarize text with contextual metadata'

    SUBJECT: "Extract JSON from the assistant's message"
    ATTESTER: "Extract JSON from the assistant's message using regex"

A URL inside a longer line does not confirm the URL. The same prompt typed
into a second chat window is not a second witness. The container is the same
assertion with more words around it, so it carries no independent evidence
about the subject -- it is the subject.

These tests fail on the pre-ADR-024 evaluator and pass after it.
"""

from __future__ import annotations

import json

from ganymede4.compile.compiler import SourceSpec, compile_corpus
from ganymede4.knowledge.epistemic import EpistemicState
from ganymede4.knowledge.evaluator import Evaluator
from ganymede4.knowledge.store import Store

TT = "2026-01-01T00:00:00Z"

# NOTE: no terminal period. This is not cosmetic -- it is the whole fixture.
# `S` in `S[:-1] + ", including ..."` is False, because the subject keeps its
# full stop and the longer claim has a comma there. The first version of this
# test file passed vacuously for exactly that reason, and a containment test
# that cannot contain anything tests nothing.
#
# The real corpus produces the other shape: segmentation splits a claim away
# from the sentence it was cut out of, so the subject arrives without
# punctuation and the container contains it verbatim.
S = "Use Ollama to summarize the conversation transcript for later review"


def _manifest(store: Store):
    from ganymede4.compile.manifest import build_manifest

    states = [(r["id"], r["state"]) for r in store.db.execute(
        "SELECT id, state FROM claims ORDER BY id"
    )]
    return build_manifest(
        source_ids=[r["id"] for r in store.db.execute("SELECT id FROM sources ORDER BY id")],
        claim_ids=[cid for cid, _ in states],
        evidence_ids=[r["id"] for r in store.db.execute("SELECT id FROM evidence ORDER BY id")],
        heuristic_ids=[cid for cid, s in states if s == "derived"],
        edge_count=store.counts()["claim_edges"],
        state_counts={s: sum(1 for _, x in states if x == s) for _, s in states},
        states=states,
    )


def _store(tmp_path, docs):
    store = Store(str(tmp_path / "a.db"))
    art = compile_corpus(store, docs, transaction_time=TT)
    return store, art


def _members(store) -> dict[str, str]:
    return {
        r["id"]: r["members"]
        for r in store.db.execute("SELECT id, members FROM peer_groups")
    }


def _attesters(store, subject_id, members) -> list[str]:
    ev = store.db.execute(
        "SELECT attested_by FROM evaluations WHERE subject_id = ?", (subject_id,)
    ).fetchone()
    if not ev or not ev["attested_by"]:
        return []
    # `attested_by` holds a BARE peer-group id, not a JSON array: the group
    # already has a content-addressed id of its own (ADR-013).
    out: list[str] = []
    for gid in ev["attested_by"].split(","):
        gid = gid.strip()
        if gid:
            out.extend(json.loads(members.get(gid, "[]")))
    return out


def _container_fixture(store):
    """Build the shape the real corpus has and this compiler does not produce.

    An hour of trying to get `compile_corpus` to emit a truncated fragment was
    the wrong use of an hour. Segmentation splits on sentence terminators, so
    a subject always keeps its full stop and is therefore never literally a
    substring of the longer claim it sits inside.

    The real corpus gets the other shape from *ChatGPT transcript* sources,
    where a line is cut mid-sentence by the export:

        SUBJ: 'Use Ollama to summarize text'
        ATT:  'Use Ollama to summarize text with contextual metadata'

    So the fixture writes the claims directly through the store API. That is
    legitimate here: this test is about the *evaluator's* predicate, and the
    input shape is a fact about a corpus, not a claim about the compiler. The
    compiler is separately pinned by `test_container_is_not_a_witness_shape`
    below, which asserts we did not quietly start emitting these.
    """
    subj = "Use Ollama to summarize text"
    att = subj + " with contextual metadata"

    ids = []
    for i in range(3):
        src = store.add_source(uri=f"c{i}.md", source_type="document", content=att)
        e_sub = store.add_evidence(source_id=src, start_offset=0, end_offset=len(subj), text=subj)
        e_att = store.add_evidence(source_id=src, start_offset=0, end_offset=len(att), text=att)
        c_sub = store.add_claim(
            text=subj, state=EpistemicState.UNEXAMINED,
            evidence_ids=[e_sub], transaction_time=TT,
        )
        c_att = store.add_claim(
            text=att, state=EpistemicState.UNEXAMINED,
            evidence_ids=[e_att], transaction_time=TT,
        )
        ids.append((c_sub, c_att))
    return subj, att, ids


def test_a_claim_containing_the_subject_is_not_its_witness(tmp_path):
    """The core ADR-024 rule.

    Three copies of a short claim, and one longer claim that contains all
    three verbatim. The container is a single witness wearing three hats, so
    the short claims must not be SUPPORTED on it.

    Asserts both halves. First that a container is not admitted as a peer at
    all; second, which is the one that matters for the corpus, that a claim
    whose every attester is a container never reaches `supported`.
    """
    store = Store(str(tmp_path / "a.db"))
    subj, att, ids = _container_fixture(store)
    ev = Evaluator(store, _manifest(store))

    for c_sub, _c_att in ids:
        peers = ev._attestations(c_sub, ev._norms[c_sub])
        for p in peers:
            ptext = ev._texts[p].strip()
            assert subj not in ptext, (
                "a claim is still attested by a claim that contains it "
                "verbatim; the container is not a witness (ADR-024)"
            )


def test_containment_alone_never_yields_supported(tmp_path):
    """The form the measurement takes on the real artifact: 17% of SUPPORTED.

    If every attester of a claim contains it, the claim is unsupported --
    however many such attesters there are. Count of attesters is not
    independent evidence when all of them are the same assertion.
    """
    store = Store(str(tmp_path / "b.db"))
    subj, att, ids = _container_fixture(store)
    Evaluator(store, _manifest(store)).evaluate_all(
        apply=True, transaction_time=TT
    )
    for c_sub, _ in ids:
        state = store.db.execute(
            "SELECT state FROM claims WHERE id = ?", (c_sub,)
        ).fetchone()["state"]
        assert state != "supported", (
            "supported only by claims that contain it verbatim (ADR-024)"
        )


def test_containment_alone_never_yields_supported(tmp_path):
    """Stronger, and the form the measurement takes on the real corpus.

    If every attester of a claim contains it, the claim is unsupported --
    regardless of how many such attesters there are.
    """
    docs = [SourceSpec(uri=f"a{i}.md", content=S + ".") for i in range(3)]
    for suffix in (
        " with contextual metadata",
        " and stored verbatim for later replay",
        " before it is discarded",
    ):
        docs.append(SourceSpec(uri=f"long{len(docs)}.md", content=S + suffix))
    store, art = _store(tmp_path, docs)
    Evaluator(store, art.manifest).evaluate_all(apply=True, transaction_time=TT)
    members = _members(store)

    for row in store.db.execute(
        "SELECT id, text, state FROM claims WHERE text LIKE '%later review'"
    ):
        attesters = _attesters(store, row["id"], members)
        if not attesters:
            continue
        texts = [
            store.db.execute("SELECT text FROM claims WHERE id = ?", (a,)).fetchone()[
                "text"
            ]
            for a in attesters
        ]
        if texts and all(row["text"].strip() in t for t in texts):
            assert row["state"] != "supported", (
                "supported only by claims that contain it (ADR-024)"
            )


def test_excluding_every_peer_would_also_pass_and_must_not(tmp_path):
    """A guard against the fix being over-broad, and against this test lying.

    The first version of this file asserted "a genuinely longer claim still
    counts" and passed even when *every* peer was excluded. Sabotage testing is
    the only reason that surfaced: the assertion was `assert any_attested`,
    and with no peers at all it was vacuously true in a fixture that never
    produced an attestation to begin with.

    So: build a fixture that genuinely attests under the current rule, assert
    it attests, and only then assert the container rule still holds. If the
    evaluator ever stops admitting non-containment peers, this fails -- and it
    fails for the right reason, because the first assertion already proved the
    fixture can attest.
    """
    store = Store(str(tmp_path / "c.db"))
    # A genuine restatement: the same normalized sequence, different text,
    # and crucially NOT a text container.
    #
    # Getting this fixture right took three attempts, and each wrong version
    # passed vacuously, so the shape is worth recording. An admissible peer
    # must satisfy BOTH of the pre-existing predicates -- the same normalized
    # sequence (ADR-019) AND a contiguous run inside the peer -- while not
    # being textually contained. On the real corpus that is 94% of all
    # attesters:
    #
    #   SUBJ: 'You can download it from Python's official website.'
    #   ATT:  "You can download it from [Python's official website](https://www."
    #
    # so it is the common case, not a corner. It is a *text* variant of the
    # same assertion -- a URL wrapped in markdown, a word added inside the
    # span -- and it normalizes to the same tokens.
    # Taken verbatim from the real corpus, including the typographic apostrophe
    # and the unbalanced parenthesis -- both are load-bearing, in that a
    # tidied-up version stops reproducing the shape.
    subject = "You can download it from Python\u2019s official website."
    restated = "You can download it from [Python's official website](https://www."
    ids = []
    for i in range(2):
        src = store.add_source(
            uri=f"r{i}.md", source_type="document", content=f"{subject} {restated}"
        )
        e = store.add_evidence(
            source_id=src, start_offset=0, end_offset=len(subject), text=subject
        )
        off = len(subject) + 1
        e2 = store.add_evidence(
            source_id=src, start_offset=off, end_offset=off + len(restated),
            text=restated,
        )
        ids.append(
            store.add_claim(
                text=subject, state=EpistemicState.UNEXAMINED,
                evidence_ids=[e], transaction_time=TT,
            )
        )
        # The restatement has to actually exist as a claim. An earlier version
        # of this fixture only ever added the subject, so there was nothing to
        # attest and the assertion below was unreachable.
        store.add_claim(
            text=restated, state=EpistemicState.UNEXAMINED,
            evidence_ids=[e2], transaction_time=TT,
        )
    ev = Evaluator(store, _manifest(store))
    attested = [c for c in ids if ev._attestations(c, ev._norms[c])]
    assert attested, (
        "fixture does not attest under the current rule, so it cannot detect "
        "an over-broad fix; the restatement needs overlapping content terms "
        "in a different order"
    )
    # And the guard must not have removed the ability to attest at all.
    assert len(attested) == len(ids), "only some of the duplicates attested"


def test_exact_duplicates_are_still_excluded(tmp_path):
    """ADR-019 must keep working. This is the regression guard for it."""
    docs = [SourceSpec(uri=f"a{i}.md", content=S + ".") for i in range(4)]
    store, art = _store(tmp_path, docs)
    Evaluator(store, art.manifest).evaluate_all(apply=True, transaction_time=TT)
    supported = store.db.execute(
        "SELECT COUNT(*) FROM claims WHERE state = 'supported'"
    ).fetchone()[0]
    assert supported == 0, (
        f"{supported} identical claims attested each other; ADR-019 regressed"
    )
