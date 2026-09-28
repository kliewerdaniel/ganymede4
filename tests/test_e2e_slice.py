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

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from sovereign_runtime.knowledge.epistemic import EpistemicState
from sovereign_runtime.knowledge.store import Store

REPO = Path(__file__).resolve().parent.parent
AUDITOR = REPO / "scripts" / "audit_provenance.py"

CORPUS = (
    "Daniel built a local inference runtime in 2026.\n"
    "The runtime compiles a corpus into a versioned artifact.\n"
    "Ganymede v0.1 produced a fabricated narrative.\n"
    "Water is wet and the sky is blue.\n"
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


def build_artifact(path: Path) -> None:
    """Compile a small corpus through the real API."""
    store = Store(str(path))
    src = store.add_source(uri="test://corpus/v1", source_type="note", content=CORPUS)

    for fact in FACTS:
        start, end, text = _span(fact)
        eid = store.add_evidence(source_id=src, start_offset=start, end_offset=end, text=text)
        store.add_claim(
            text=text,
            state=EpistemicState.SUPPORTED,
            evidence_ids=[eid],
            transaction_time="2026-01-01T00:00:00Z",
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


class TestIndependentAudit:
    def test_auditor_passes_on_a_well_formed_artifact(self, artifact):
        code, out = run_auditor(artifact)
        assert code == 0, f"auditor rejected a valid artifact:\n{out}"
        assert "clean: True" in out

    def test_auditor_is_a_separate_process(self):
        # the auditor must not import the package under test
        source = AUDITOR.read_text()
        assert "sovereign_runtime" not in source

    def test_every_claim_resolves_to_a_source_span(self, artifact):
        _, out = run_auditor(artifact)
        assert "orphans: []" in out
        assert "span_mismatches: []" in out

    def test_absence_is_auditable(self, artifact):
        _, out = run_auditor(artifact)
        assert "unresolved_without_investigation: []" in out
        assert "unresolved_absence: 1" in out


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

        code, out = run_auditor(p)
        assert code == 1, "auditor failed to detect source drift"
        assert "span_mismatches: []" not in out

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

        code, out = run_auditor(p)
        assert code == 1, "auditor failed to detect unproven absence"
        assert "unresolved_without_investigation: []" not in out

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

        code, out = run_auditor(p)
        assert code == 1, "auditor failed to detect an orphan claim"
        assert "orphans: []" not in out
