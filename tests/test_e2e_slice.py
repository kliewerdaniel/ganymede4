"""End-to-end vertical slice: corpus → artifact → independent audit.

Builds a small artifact through the real Store API in one process, then audits
it from a *separate* process using only stdlib sqlite3 and no knowledge of the
Store schema's intent. The two stages share no code, so a bug that satisfies
Store's own tests but leaves a claim without a source is still caught.

The adversarial half deliberately corrupts an artifact and asserts the auditor
*fails*. An auditor that cannot detect a planted defect cannot be trusted to
certify absence of one.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ganymede4.knowledge.epistemic import EpistemicState
from ganymede4.knowledge.store import Store

REPO = Path(__file__).resolve().parent.parent
AUDITOR = REPO / "scripts" / "audit_provenance.py"

CORPUS = (
    "Daniel built a local inference runtime in 2026.\n"
    "The runtime compiles a corpus into a versioned artifact.\n"
    "Ganymede v0.1 produced a fabricated narrative.\n"
    "Water is wet and the sky is blue.\n"
)

#: A second, independent source. It exists so the fixture has something the
#: evaluator can genuinely find: each FACTS entry is restated here, so the
#: first source's claims are attested by a *different* claim rather than by
#: themselves. That distinction is the whole point of ADR-006 §2.2, and a
#: fixture that faked it would test nothing.
CORROBORATION = (
    "Daniel built a local inference runtime in 2026.\n"
    "The runtime compiles a corpus into a versioned artifact.\n"
    "Ganymede v0.1 produced a fabricated narrative.\n"
)

#: Offsets are derived from the corpus text, never hand-counted. A hardcoded
#: offset is a test that fails for the wrong reason the moment anyone edits a
#: word — and the span verifier would (correctly) reject it, burying the real
#: assertion under an offset-arithmetic error.
FACTS = (
    "Daniel built a local inference runtime in 2026.",
    "The runtime compiles a corpus into a versioned artifact.",
    "Ganymede v0.1 produced a fabricated narrative.",
)


def _span(needle: str) -> tuple[int, int, str]:
    start = CORPUS.index(needle)
    return start, start + len(needle), needle


def build_artifact(path: Path, extra_source: str | None = None) -> None:
    """Compile a small corpus through the real API.

    ``extra_source`` adds a third document verbatim. It exists for the NUL
    regression test: the auditor's span check has to survive a source
    containing a literal NUL, and that can only be tested with a source that
    actually contains one.
    """
    store = Store(str(path))
    src = store.add_source(uri="test://corpus/v1", source_type="note", content=CORPUS)
    src2 = store.add_source(uri="test://corpus/v2", source_type="note", content=CORROBORATION)
    if extra_source is not None:
        src3 = store.add_source(
            uri="test://corpus/nul", source_type="note", content=extra_source
        )
        # Evidence *past* the NUL, which is exactly where a C-string
        # implementation stops reading and reports a false mismatch.
        tail = "A later sentence survives the NUL byte."
        start3 = extra_source.index(tail)
        eid3 = store.add_evidence(
            source_id=src3,
            start_offset=start3,
            end_offset=start3 + len(tail),
            text=tail,
        )
        store.add_claim(
            text=tail,
            state=EpistemicState.UNEXAMINED,
            evidence_ids=[eid3],
            transaction_time="2026-01-01T00:00:00Z",
        )

    for fact in FACTS:
        start, end, text = _span(fact)
        eid = store.add_evidence(source_id=src, start_offset=start, end_offset=end, text=text)
        store.add_claim(
            text=text,
            # UNEXAMINED, not SUPPORTED. Writing SUPPORTED here would assert
            # that the evidence bears the claim with nothing having decided
            # that — the unjustified assertion ADR-006 makes unrepresentable.
            state=EpistemicState.UNEXAMINED,
            evidence_ids=[eid],
            transaction_time="2026-01-01T00:00:00Z",
        )

    # The restating source. Each of these is a distinct claim (different
    # source, different offset, therefore different content id) that states
    # the same proposition as its counterpart above.
    for fact in FACTS:
        start2 = CORROBORATION.index(fact)
        eid2 = store.add_evidence(
            source_id=src2, start_offset=start2, end_offset=start2 + len(fact), text=fact
        )
        store.add_claim(
            text=fact,
            state=EpistemicState.UNEXAMINED,
            evidence_ids=[eid2],
            transaction_time="2026-01-01T00:00:00Z",
        )

    # Now something actually decides, and every state change it makes leaves an
    # Evaluation record naming the claim that attested it. The independent
    # auditor requires that record, so a store that moved state without one
    # now fails the audit instead of passing it.
    from ganymede4.knowledge.evaluator import Evaluator

    class _Manifest:
        claim_ids = tuple(r["id"] for r in store.claims())
        source_ids = (src, src2)
        evidence_ids = ()
        heuristic_ids = ()
        version = "test-fixture"
        root = "test-fixture"
        state_counts: dict = {}

    Evaluator(store, _Manifest()).evaluate_all(
        apply=True, transaction_time="2026-01-01T00:00:00Z"
    )

    # absence, made provable
    inv = store.add_investigation(
        question="When does the corpus mention the moon?",
        searched=["test://corpus/v1"],
        created_at="2026-01-01T00:00:00Z",
    )
    store.add_claim(
        text="no mention of the moon in this corpus",
        state=EpistemicState.UNRESOLVED,
        investigation_id=inv,
        transaction_time="2026-01-01T00:00:00Z",
    )
    store.close()


def run_auditor(path: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(AUDITOR), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


@pytest.fixture(scope="module")
def artifact(tmp_path_factory):
    p = tmp_path_factory.mktemp("artifact") / "s.db"
    build_artifact(p)
    return p


def audit_json(path):
    """Parse the auditor's report.

    The assertions below used to grep the printed text, which made them
    assertions about *formatting* rather than about the audit: renaming a key
    would fail every one of them while the audit was unchanged, and a value
    printed in a way the substring missed would pass while the defect was
    there. They now read the values.
    """
    code, out = run_auditor(path)
    return code, json.loads(out)


class TestIndependentAudit:
    def test_auditor_passes_on_a_well_formed_artifact(self, artifact):
        code, report = audit_json(artifact)
        assert code == 0, f"auditor rejected a valid artifact:\n{report}"
        assert report["clean"] is True

    def test_auditor_is_a_separate_process(self):
        # the auditor must not import the package under test
        source = AUDITOR.read_text()
        assert "ganymede4" not in source

    def test_every_claim_resolves_to_a_source_span(self, artifact):
        _, report = audit_json(artifact)
        assert report["orphans"] == []
        assert report["span_mismatches"] == []

    def test_absence_is_auditable(self, artifact):
        _, report = audit_json(artifact)
        assert report["unresolved_without_investigation"] == []
        assert report["unresolved_absence"] == 1

    def test_support_is_auditable(self, artifact):
        """The mirror of the check above (ADR-006).

        A claim that moved past UNEXAMINED with no evaluation naming what
        decided it is asserting support it cannot show. The auditor is the
        only thing in the system not written by the same hand as the store, so
        if this check lives anywhere it has to live here too.
        """
        _, report = audit_json(artifact)
        assert report["state_without_evaluation"] == []


class TestAdversarial:
    """An auditor that cannot detect a planted defect certifies nothing."""

    def test_detects_a_claim_whose_span_no_longer_matches(self, tmp_path):
        p = tmp_path / "bad.db"
        build_artifact(p)
        # corrupt the source *after* the evidence was recorded — the classic
        # silent-drift failure, where provenance is structurally intact but no
        # longer points at anything real
        conn = sqlite3.connect(p)
        conn.execute("UPDATE sources SET content = ?", ("completely different text",))
        conn.commit()
        conn.close()

        code, report = audit_json(p)
        assert code == 1, "auditor failed to detect source drift"
        assert report["span_mismatches"]

    def test_detects_an_unresolved_claim_with_no_investigation(self, tmp_path):
        p = tmp_path / "bad2.db"
        build_artifact(p)
        conn = sqlite3.connect(p)
        # forge a claim that asserts absence with no record of what was searched
        conn.execute(
            "INSERT INTO claims (id, text, state, valid_time, transaction_time,"
            " investigation_id, meta) VALUES ('clm-forged','forged absence','unresolved',"
            " NULL,'t0',NULL,'{}')"
        )
        conn.commit()
        conn.close()

        code, report = audit_json(p)
        assert code == 1, "auditor failed to detect unproven absence"
        assert report["unresolved_without_investigation"]

    def test_detects_an_evidence_bearing_claim_with_no_evidence(self, tmp_path):
        p = tmp_path / "bad3.db"
        build_artifact(p)
        conn = sqlite3.connect(p)
        conn.execute(
            "INSERT INTO claims (id, text, state, valid_time, transaction_time,"
            " investigation_id, meta) VALUES ('clm-orphan','asserted with no source',"
            "'supported',NULL,'t0',NULL,'{}')"
        )
        conn.commit()
        conn.close()

        code, report = audit_json(p)
        assert code == 1, "auditor failed to detect an orphan claim"
        assert report["orphans"]

    def test_a_nul_byte_in_a_source_is_not_a_provenance_defect(self, tmp_path):
        """The auditor must not invent defects.

        SQLite's ``substr()`` works on a NUL-terminated C string, so a source
        containing a literal NUL truncates there and every span past that point
        reads as empty. An auditor written that way reported **1,434 false span
        mismatches** on the real corpus — correct provenance, broken checker.

        This is worth a test because the failure mode is quiet and expensive in
        the wrong direction: a checker that cries wolf is one people learn to
        ignore, and then it also fails to report the defects that are real.
        """
        p = tmp_path / "nul.db"
        prefix = "The runtime enforces provenance at write time. "
        body = "\x00" + "Filler text before the claim. " * 4
        tail = "A later sentence survives the NUL byte."
        build_artifact(p, extra_source=prefix + body + tail)

        code, report = audit_json(p)
        assert report["span_mismatches"] == [], (
            "a NUL byte in the source was reported as broken provenance: "
            f"{report['span_mismatches'][:2]}"
        )
        assert code == 0, f"auditor rejected a valid artifact containing a NUL:\n{report}"

    def test_detects_a_state_change_with_no_evaluation(self, tmp_path):
        """Strip the evaluator's records and every state becomes unjustified.

        This is the positive-direction twin of the unproven-absence check. The
        claims are intact and their spans still resolve, so every other check
        passes — which is exactly why this check has to exist. A store that can
        reach `supported` and then lose the record of why is asserting support
        on the strength of nothing.
        """
        p = tmp_path / "bad4.db"
        build_artifact(p)
        conn = sqlite3.connect(p)
        conn.execute("DELETE FROM evaluations")
        conn.commit()
        conn.close()

        code, report = audit_json(p)
        assert code == 1, "auditor accepted states with nothing behind them"
        assert report["state_without_evaluation"]
        # The other checks still pass, so this detection is doing real work.
        assert report["span_mismatches"] == []
        assert report["orphans"] == []
