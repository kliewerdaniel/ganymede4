"""Independent provenance audit.

Deliberately written against the *raw database* rather than the Store API, and
in a separate process, per the "verify independently, fail-closed" rule. A test
that uses the same code path as the code under test proves only that the code is
self-consistent, not that it is correct.

This walks every claim in the artifact and requires it to terminate in a
character offset in an identified source. A claim that cannot is reported, not
skipped. This is the invariant that must never be relaxed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any


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
    try:
        for claim in conn.execute("SELECT * FROM claims").fetchall():
            report["claims"] += 1
            state = claim["state"]
            evs = conn.execute(
                "SELECT e.* FROM evidence e JOIN claim_evidence ce ON ce.evidence_id = e.id"
                " WHERE ce.claim_id = ?",
                (claim["id"],),
            ).fetchall()

            if not evs:
                if state == "unresolved":
                    report["unresolved_absence"] += 1
                elif state in ("unexamined", "assumed", "derived"):
                    pass  # legitimately evidence-free by ADR-002
                else:
                    report["orphans"].append({"claim_id": claim["id"], "state": state})

            for ev in evs:
                src = conn.execute(
                    "SELECT * FROM sources WHERE id = ?", (ev["source_id"],)
                ).fetchone()
                if src is None:
                    report["dangling_edges"].append(ev["id"])
                    continue
                # the span must still be present in the source content
                actual = src["content"][ev["start_offset"] : ev["end_offset"]]
                if actual != ev["text"]:
                    report["span_mismatches"].append(
                        {"evidence_id": ev["id"], "expected": ev["text"], "actual": actual}
                    )
                else:
                    report["resolved"] += 1

            if state == "unresolved":
                inv = claim["investigation_id"]
                if inv is None:
                    report["unresolved_without_investigation"].append(claim["id"])
                elif (
                    conn.execute(
                        "SELECT 1 FROM investigations WHERE id = ?", (inv,)
                    ).fetchone()
                    is None
                ):
                    report["unresolved_without_investigation"].append(claim["id"])

            # The mirror of the check above, and the reason the auditor cannot
            # be satisfied by a store that merely records absence (ADR-006).
            # UNRESOLVED must show its work; SUPPORTED/CONTRADICTED/INCONCLUSIVE
            # must too. A claim that moved state with no evaluation behind it
            # is the positive-direction version of the same defect: an
            # assertion that cannot explain what decided it.
            if state not in ("unexamined", "assumed", "derived", "unresolved"):
                has_eval = conn.execute(
                    "SELECT 1 FROM evaluations WHERE subject_id = ?", (claim["id"],)
                ).fetchone()
                if has_eval is None:
                    report["state_without_evaluation"].append(
                        {"claim_id": claim["id"], "state": state}
                    )

        # ADR-013: `evaluations.attested_by` holds an `agr-` group id, not a
        # member list. An auditor that only checked "does an evaluation row
        # exist" would pass an artifact whose attesting evidence cannot be
        # resolved at all — a support record naming nothing.
        #
        # This is a separate process on purpose: it re-derives the members from
        # the database without importing the store, so a bug in the store's
        # hydration cannot vouch for itself here.
        for ev in conn.execute(
            "SELECT id, subject_id, relation, attested_by FROM evaluations"
            " WHERE attested_by != ''"
        ).fetchall():
            group = conn.execute(
                "SELECT members FROM attestation_groups WHERE id = ?", (ev["attested_by"],)
            ).fetchone()
            if group is None:
                report["unresolvable_attestation"].append(
                    {"evaluation_id": ev["id"], "group": ev["attested_by"]}
                )
                continue
            try:
                members = json.loads(group["members"])
            except (TypeError, ValueError):
                report["unresolvable_attestation"].append(
                    {"evaluation_id": ev["id"], "group": ev["attested_by"],
                     "reason": "members are not valid JSON"}
                )
                continue
            if not isinstance(members, list) or not members:
                report["unresolvable_attestation"].append(
                    {"evaluation_id": ev["id"], "group": ev["attested_by"],
                     "reason": "attested relation with no attesting claims"}
                )
                continue
            for member in members:
                if conn.execute(
                    "SELECT 1 FROM claims WHERE id = ?", (member,)
                ).fetchone() is None:
                    report["unresolvable_attestation"].append(
                        {"evaluation_id": ev["id"], "missing_claim": member}
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
    for key, value in report.items():
        print(f"{key}: {value}")
    # fail-closed: exit non-zero on any provenance defect
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
