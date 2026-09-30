"""Read evaluator verdicts and report what they actually mean (ADR-024).

This is deliberately NOT the independent auditor. The auditor answers "is this
artifact internally consistent", which is a different question from "do these
verdicts mean anything". A verifier that only ever asked the first question
would happily report `clean: true` on an artifact where every verdict is
vacuous -- which is exactly what happened, four times, in ADR-014, ADR-018,
ADR-020 and ADR-021.

So this script reaches into the artifact with raw stdlib sqlite3 and *reads*.
It imports nothing from `ganymede4` for the same reason the auditor does, and
it never writes.

Usage:
    python scripts/read_verdicts.py <artifact.db> [--sample 50] [--seed 0]

Every number it prints is counted from the database. Nothing is estimated and
nothing is fitted to this corpus.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys

#: A claim whose text is a substring of another claim is not corroborated by
#: it. The container and the contained are the same assertion, at two lengths.
#: Used as a *reported fraction*, never as a threshold: see ADR-024.
EXAMPLE_CAP = 50


def _texts(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in ids:
        row = conn.execute("SELECT text FROM claims WHERE id = ?", (i,)).fetchone()
        if row is not None:
            out[i] = row["text"].strip()
    return out


def _peers(members: dict[str, list[str]], raw: str | None) -> list[str]:
    """Expand an `attested_by` / `contradicted_by` field to claim ids.

    These hold *bare* peer-group ids, not a JSON array -- a peer group already
    has a content-addressed id of its own (ADR-013), so there is nothing to
    serialize here. A comma-separated list is accepted too, because a single
    id and a list of one are easy to confuse when reading a database.
    """
    if not raw:
        return []
    ids = [raw] if raw.startswith("agr-") else raw.split(",")
    out: list[str] = []
    for gid in ids:
        gid = gid.strip()
        if gid:
            out.extend(members.get(gid, []))
    return out


def _members(conn: sqlite3.Connection) -> dict[str, list[str]]:
    return {
        r["id"]: json.loads(r["members"])
        for r in conn.execute("SELECT id, members FROM peer_groups")
    }


def _sources(conn: sqlite3.Connection) -> dict[str, str]:
    """claim_id -> source_id, via its evidence. A claim with no evidence has none."""
    out: dict[str, str] = {}
    for r in conn.execute(
        "SELECT ce.claim_id AS cid, e.source_id AS sid "
        "FROM claim_evidence ce JOIN evidence e ON e.id = ce.evidence_id"
    ):
        out.setdefault(r["cid"], r["sid"])
    return out


def attested(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT e.subject_id AS subject_id, c.text AS text, e.attested_by AS attested_by "
        "FROM evaluations e JOIN claims c ON c.id = e.subject_id "
        "WHERE e.relation = 'attested'"
    ).fetchall()


def contradiction_containment(
    conn: sqlite3.Connection, members: dict[str, list[str]]
) -> dict[str, int]:
    stats = {"n": 0, "contained_in_peer": 0, "same_source": 0, "both": 0}
    for r in conn.execute(
        "SELECT e.subject_id AS subject_id, c.text AS text, "
        "e.contradicted_by AS contradicted_by FROM evaluations e "
        "JOIN claims c ON c.id = e.subject_id WHERE e.relation = 'contradicted'"
    ):
        peers = _peers(members, r["contradicted_by"])
        if not peers:
            continue
        stats["n"] += 1
        subject = r["text"].strip()
        ptexts = _texts(conn, peers)
        if all(subject in t for t in ptexts.values()):
            stats["contained_in_peer"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db")
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rng = random.Random(args.seed)

    print("=" * 72)
    print("ganymede4 — read the verdicts (not the auditor)")
    print("=" * 72)

    states = {
        r["state"]: r["n"]
        for r in conn.execute("SELECT state, COUNT(*) AS n FROM claims GROUP BY state")
    }
    rels = {
        r["relation"]: r["n"]
        for r in conn.execute(
            "SELECT relation, COUNT(*) AS n FROM evaluations GROUP BY relation"
        )
    }
    print("\n--- counts ---")
    print("claims:", sum(states.values()))
    for k, v in sorted(states.items()):
        print(f"  {k:<16} {v:>9,}")
    print("evaluations by relation:")
    for k, v in sorted(rels.items()):
        print(f"  {k:<16} {v:>9,}")

    members = _members(conn)
    srcmap = _sources(conn)
    rows = attested(conn)

    # ---- 1. is SUPPORTED just containment? -------------------------------
    print("\n--- SUPPORTED: is the attester just a longer copy? ---")
    n = short_n = contained = short_contained = same_source = 0
    lengths: list[int] = []
    examples: list[tuple[str, str, str]] = []
    for r in rows:
        peers = members.get(r["attested_by"] or "", [])
        if not peers:
            continue
        subject = r["text"].strip()
        lengths.append(len(subject))
        ptexts = _texts(conn, peers)
        if not ptexts:
            continue
        n += 1
        is_short = len(subject) <= 40
        short_n += is_short
        if all(subject in t for t in ptexts.values()):
            contained += 1
            short_contained += is_short
            if srcmap.get(r["subject_id"]) and all(
                srcmap.get(p) == srcmap.get(r["subject_id"]) for p in peers
            ):
                same_source += 1
            if len(examples) < 6:
                one = next(iter(ptexts.values()))
                examples.append((subject, one, srcmap.get(r["subject_id"], "?")))
    print(f"attested with >=1 attester: {n:,}")
    print(f"  subject is a SUBSTRING of every attester: {contained:,} ({_pct(contained, n)})")
    print(f"  ...and every attester is the SAME source:  {same_source:,} ({_pct(same_source, n)})")
    print(f"subjects of <=40 chars: {short_n:,} ({_pct(short_n, n)})")
    print(
        f"  short subjects supported only by containing attesters: "
        f"{short_contained:,} ({_pct(short_contained, short_n)})"
    )
    if lengths:
        lengths.sort()
        print(
            f"subject length: min {lengths[0]}, median {lengths[len(lengths) // 2]}, "
            f"max {lengths[-1]}"
        )
    print("\nexamples (subject | one attester):")
    for s, a, _sid in examples:
        print(f"  {s[:60]!r}")
        print(f"    <- {a[:78]!r}")

    # ---- 2. CONTRADICTED ------------------------------------------------
    print("\n--- CONTRADICTED ---")
    print("counts:", rels.get("contradicted", 0))
    cstats = contradiction_containment(conn, members)
    print(f"with peers: {cstats['n']:,}")
    print(
        f"  subject is a substring of every contradicting peer: "
        f"{cstats['contained_in_peer']:,} ({_pct(cstats['contained_in_peer'], cstats['n'])})"
    )
    crows = conn.execute(
        "SELECT e.subject_id AS subject_id, c.text AS text, "
        "e.contradicted_by AS contradicted_by FROM evaluations e "
        "JOIN claims c ON c.id = e.subject_id WHERE e.relation = 'contradicted'"
    ).fetchall()
    for r in rng.sample(crows, min(args.sample, len(crows))):
        peers = _peers(members, r["contradicted_by"])
        ptexts = _texts(conn, peers)
        print(f"\n  SUBJECT: {r['text'][:90]!r}")
        for t in list(ptexts.values())[:2]:
            print(f"    AGAINST: {t[:90]!r}")

    # ---- 3. DERIVED -----------------------------------------------------
    print("\n--- DERIVED (mined recurrence) ---")
    # Direction matters: a `DERIVED_FROM` edge runs FROM the mined heuristic
    # TO each source claim that exhibits the shape. Joining on `to_id` finds
    # zero for every derived claim, which reads as "these claims assert
    # nothing" when it means the opposite.
    derived = conn.execute(
        "SELECT c.id, c.text, COUNT(e.to_id) AS n FROM claims c "
        "LEFT JOIN claim_edges e ON e.from_id = c.id AND e.relation = 'DERIVED_FROM' "
        "WHERE c.state = 'derived' GROUP BY c.id"
    ).fetchall()
    print(f"derived claims: {len(derived):,}")
    print(
        "edges:",
        conn.execute(
            "SELECT COUNT(*) FROM claim_edges WHERE relation = 'DERIVED_FROM'"
        ).fetchone()[0],
    )
    for r in rng.sample(derived, min(args.sample, len(derived))):
        print(f"  [{r['n']:>3}x] {r['text'][:100]!r}")

    # ---- 4. authorship --------------------------------------------------
    print("\n--- authorship: are other people's words in the claims? ---")
    by_type = {
        r["source_type"]: r["n"]
        for r in conn.execute(
            "SELECT source_type, COUNT(*) AS n FROM sources GROUP BY source_type"
        )
    }
    for k, v in sorted(by_type.items()):
        print(f"  {k:<26} {v:>7,}")
    print(
        "note: source_type records who WROTE the document. A claim's evidence "
        "spans the document it was segmented from. Whether a span is the "
        "author's own testimony is NOT recorded anywhere in the schema -- see "
        "ADR-024; this script can count the gap but not fill it."
    )
    conn.close()
    return 0


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "n/a"


if __name__ == "__main__":
    sys.exit(main())
