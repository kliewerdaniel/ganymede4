"""Independent provenance audit.

Deliberately written against the *raw database* rather than the Store API, and
in a separate process, per the "verify independently, fail-closed" rule. A test
that uses the same code path as the code under test proves only that the code is
self-consistent, not that it is correct.

This walks every claim in the artifact and requires it to terminate in a
character offset in an identified source. A claim that cannot is reported, not
skipped. This is the invariant that must never be relaxed.

**Set-based, not row-by-row.** The previous implementation looped over every
claim and issued a ``SELECT`` per claim. With an index that would be fine;
without one, ``evaluations WHERE subject_id = ?`` over 308,935 rows is a full
table scan per claim, and 324,951 of those is ~10^11 row visits. On the real
corpus it did not finish in 15 minutes. This is not an optimization — it is the
difference between an auditor that runs on the artifact it exists to check and
one that does not.

Every check is a set operation, so no answer depends on row or iteration order.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any

#: Claims permitted to carry no evidence at all (ADR-002). `unresolved` is in
#: this set because a claim that was searched for and not found has, by
#: definition, no evidence span — its evidence is the investigation record,
#: which is checked separately and just as strictly. The previous row-by-row
#: implementation branched on `unresolved` first for exactly this reason, and
#: dropping that branch turned every legitimate absence into an "orphan".
EVIDENCE_FREE_OK = ("unexamined", "assumed", "derived", "unresolved")

#: Claims permitted to have moved state without an evaluation (ADR-006).
UNEXPLAINED_STATE_OK = ("unexamined", "assumed", "derived", "unresolved")

#: Cap on *reported examples* per check. The counts above each list are exact;
#: only the sample of failures is bounded, so a corrupt artifact cannot produce
#: a gigabyte of output. The verdict is unaffected — a bounded non-empty list is
#: still non-empty.
EXAMPLE_CAP = 50


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[Any]:
    return conn.execute(sql, params).fetchall()


def audit(db_path: str) -> dict[str, Any]:
    """Walk every claim to a source offset. Returns a report; never raises."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    report: dict[str, Any] = {
        "claims": 0,
        "resolved": 0,
        "unresolved_absence": 0,
        "orphans": [],
        "dangling_edges": [],
        "state_without_evaluation": [],
        "unresolved_without_investigation": [],
        "unresolvable_attestation": [],
        "span_mismatches": [],
    }
    cap = f"LIMIT {EXAMPLE_CAP}"

    try:
        report["claims"] = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]

        # ------------------------------------------------------------------
        # 1. Evidence resolution and exact span matching.
        #
        #    A span is resolved when its source exists and the recorded offsets
        #    still contain exactly the recorded text. This is the check that
        #    catches a source edited after compilation: the offsets still
        #    resolve to *something*, just not to what was claimed.
        #
        #    The comparison is done in **Python, not SQL**, and that is not a
        #    performance concession — it is a correctness requirement.
        #    `substr()` in SQLite operates on a NUL-terminated C string, so any
        #    source containing a NUL byte truncates there and every span past
        #    that point silently reads as empty. On the real corpus, 1,434
        #    evidence rows were reported as mismatches purely because the
        #    document they came from contains a literal NUL. The provenance was
        #    correct in every one of those rows; the *checker* was wrong.
        #
        #    An auditor that invents defects is as useless as one that misses
        #    them, and worse: it trains people to ignore it. So the spans are
        #    re-derived from the source bytes here, where slicing is a Python
        #    semantic and a NUL is just a character.
        # ------------------------------------------------------------------
        span_expr = """
            SELECT ce.claim_id, e.id, e.text, e.start_offset, e.end_offset, s.content
            FROM claim_evidence ce
            JOIN evidence e ON e.id = ce.evidence_id
            JOIN sources s ON s.id = e.source_id
        """
        for claim_id, ev_id, ev_text, start, end, content in _rows(conn, span_expr):
            actual = content[start:end]
            if actual == ev_text:
                report["resolved"] += 1
            elif len(report["span_mismatches"]) < EXAMPLE_CAP:
                report["span_mismatches"].append(
                    {"evidence_id": ev_id, "claim_id": claim_id,
                     "expected": ev_text, "actual": actual}
                )

        # Evidence whose source is gone. Foreign keys should make this
        # impossible, which is exactly why it is worth checking: a database
        # copied without its constraints would produce it.
        report["dangling_edges"] = [
            r[0]
            for r in _rows(
                conn,
                """
                SELECT e.id FROM claim_evidence ce
                JOIN evidence e ON e.id = ce.evidence_id
                LEFT JOIN sources s ON s.id = e.source_id
                WHERE s.id IS NULL
                """
                + cap,
            )
        ]

        # ------------------------------------------------------------------
        # 2. Orphans: a claim in a state that promises evidence, with none.
        # ------------------------------------------------------------------
        ok_ev = ",".join("?" * len(EVIDENCE_FREE_OK))
        report["orphans"] = [
            dict(r)
            for r in _rows(
                conn,
                f"""
                SELECT c.id AS claim_id, c.state FROM claims c
                WHERE c.state NOT IN ({ok_ev})
                  AND NOT EXISTS (
                    SELECT 1 FROM claim_evidence ce WHERE ce.claim_id = c.id
                  )
                """
                + cap,
                EVIDENCE_FREE_OK,
            )
        ]

        report["unresolved_absence"] = conn.execute(
            "SELECT COUNT(*) FROM claims c WHERE c.state = 'unresolved'"
            " AND NOT EXISTS ("
            "   SELECT 1 FROM claim_evidence ce WHERE ce.claim_id = c.id)"
        ).fetchone()[0]

        # ------------------------------------------------------------------
        # 3. Absence must be provable (ADR-002). An UNRESOLVED claim names an
        #    investigation, and that investigation must exist.
        # ------------------------------------------------------------------
        report["unresolved_without_investigation"] = [
            r[0]
            for r in _rows(
                conn,
                """
                SELECT c.id FROM claims c
                LEFT JOIN investigations i ON i.id = c.investigation_id
                WHERE c.state = 'unresolved'
                  AND (c.investigation_id IS NULL OR i.id IS NULL)
                """
                + cap,
            )
        ]

        # ------------------------------------------------------------------
        # 4. State changes must show their work (ADR-006). SUPPORTED,
        #    CONTRADICTED and INCONCLUSIVE each need an evaluation row — the
        #    positive-direction mirror of the check above. A claim that moved
        #    state with no evaluation behind it is an assertion that cannot
        #    explain what decided it.
        # ------------------------------------------------------------------
        ok_st = ",".join("?" * len(UNEXPLAINED_STATE_OK))
        report["state_without_evaluation"] = [
            {"claim_id": r[0], "state": r[1]}
            for r in _rows(
                conn,
                f"""
                SELECT c.id, c.state FROM claims c
                WHERE c.state NOT IN ({ok_st})
                  AND NOT EXISTS (
                    SELECT 1 FROM evaluations e WHERE e.subject_id = c.id
                  )
                """
                + cap,
                UNEXPLAINED_STATE_OK,
            )
        ]

        # ------------------------------------------------------------------
        # 5. ADR-013: peer groups must resolve to real claim ids.
        #
        #    An auditor that only asked "does an evaluation row exist" would
        #    pass an artifact whose attesting evidence resolves to nothing — a
        #    support record naming nothing. The members are re-derived from the
        #    database here and never imported from the store, so a hydration
        #    bug cannot vouch for itself.
        # ------------------------------------------------------------------
        for row in _rows(
            conn,
            """
            SELECT e.id AS evaluation_id, e.attested_by AS gid
            FROM evaluations e
            LEFT JOIN peer_groups g ON g.id = e.attested_by
            WHERE e.attested_by != '' AND g.id IS NULL
            """
            + cap,
        ):
            report["unresolvable_attestation"].append(
                {"evaluation_id": row["evaluation_id"], "kind": "attested",
                 "group": row["gid"], "reason": "group does not exist"}
            )

        for row in _rows(
            conn,
            """
            SELECT e.id AS evaluation_id, e.contradicted_by AS gid
            FROM evaluations e
            LEFT JOIN peer_groups g ON g.id = e.contradicted_by
            WHERE e.contradicted_by != '' AND g.id IS NULL
            """
            + cap,
        ):
            report["unresolvable_attestation"].append(
                {"evaluation_id": row["evaluation_id"], "kind": "contradicted",
                 "group": row["gid"], "reason": "group does not exist"}
            )

        # A relation that claims peers but names none is the "SUPPORTED with
        # no attestors" case ADR-006 exists to make impossible. Checked against
        # the group table, not the column, because an empty group id and a
        # group holding an empty list are two different ways to say nothing.
        for row in _rows(
            conn,
            """
            SELECT e.id AS evaluation_id, e.relation
            FROM evaluations e
            JOIN peer_groups g
              ON g.id = e.attested_by OR g.id = e.contradicted_by
            WHERE e.relation IN ('attested', 'contradicted')
              AND (g.members IS NULL OR g.members IN ('[]', 'null', ''))
            """
            + cap,
        ):
            report["unresolvable_attestation"].append(
                {"evaluation_id": row["evaluation_id"], "kind": row["relation"],
                 "reason": "relation names a group that is empty"}
            )

        # Members must be claim ids that exist. Expanded with ``json_each`` so
        # the cost is one pass over the groups, not one query per member.
        try:
            for row in _rows(
                conn,
                """
                SELECT e.id AS evaluation_id, j.value AS missing_claim
                FROM evaluations e
                JOIN peer_groups g
                  ON g.id = e.attested_by OR g.id = e.contradicted_by
                JOIN json_each(g.members) j
                LEFT JOIN claims c ON c.id = j.value
                WHERE c.id IS NULL
                """
                + cap,
            ):
                report["unresolvable_attestation"].append(
                    {"evaluation_id": row["evaluation_id"],
                     "missing_claim": row["missing_claim"]}
                )
        except sqlite3.OperationalError as exc:
            # ``json_each`` needs the JSON1 extension. Without it the check is
            # reported as NOT PERFORMED rather than passed: an audit that
            # quietly stops checking is worse than one that says it stopped.
            report["unresolvable_attestation"].append(
                {"reason": f"peer membership NOT CHECKED (needs JSON1): {exc}"}
            )
    finally:
        conn.close()

    report["clean"] = (
        not report["orphans"]
        and not report["dangling_edges"]
        and not report["unresolved_without_investigation"]
        and not report["span_mismatches"]
        and not report["state_without_evaluation"]
        and not report["unresolvable_attestation"]
    )
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: audit_provenance.py <artifact.db>", file=sys.stderr)
        return 2
    report = audit(argv[1])
    print(json.dumps(report, indent=2, sort_keys=True))
    # fail-closed: exit non-zero on any provenance defect
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
