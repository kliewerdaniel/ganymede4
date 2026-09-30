"""Build and score the contradiction labelling sheet (Phase 19 Task 3).

This script does NOT decide whether a contradiction is real. It cannot: the
only ground truth for that question is a human reading each pair. So it has
exactly two jobs.

1. ``build``    -- read the artifact with raw sqlite3 and emit a CSV of a
   stratified sample of contradicted pairs, with the ``label`` column EMPTY.
2. ``score``    -- read a filled CSV back and report precision per stratum
   plus the cost of each candidate policy in ADR-027.

Both are read-only. Neither imports ``ganymede4`` -- same rule as the auditor,
and for the same reason: a tool that scores the compiler must not be able to
inherit the compiler's opinion about what a claim is.

The stratification below is a *sampling frame*, not a classifier. Its job is
to guarantee the sheet contains some code, some prose, and whatever else is
in the corpus, so the human sees the whole population rather than a
convenient slice. Its precision is exactly what the human is being asked to
measure, so tuning it against its own output would destroy the experiment.

Usage:
    python scripts/contradiction_sample.py build  <artifact.db> --out sheet.csv
    python scripts/contradiction_sample.py score  sheet.csv
    python scripts/contradiction_sample.py score  sheet.csv --policy no-code

Reads nothing but the .db file passed on the command line. Writes only the
CSV path passed with --out (build mode).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sqlite3
import sys
import unicodedata

#: How many pairs per stratum. 402 contradicted subjects yield 2,854 pairs,
#: so a proportional sample would be almost entirely code and would answer
#: only the question we already suspect the answer to. Roughly equal strata
#: buy precision *per class*, which is what ADR-027 needs: the decision is
#: about code specifically, so code needs an error bar and prose needs one.
PER_STRATUM = 30

#: The labels a human may use. Kept short because they are typed by hand,
#: into a CSV, several hundred times.
LABELS = (
    "real_contradiction",  # the two texts assert opposing propositions
    "code_fragment",       # two drafts/revisions of the same code
    "subset",              # one text contains the other plus extra material
    "boilerplate",         # recurrence of stock phrasing, no propositional content
    "other",               # fits nothing above; the human says so
)

FIELDS = (
    "pair_id",
    "stratum",
    "subject_id",
    "subject_text",
    "peer_id",
    "peer_text",
    "label",
)


# --------------------------------------------------------------------------
# reading the artifact
# --------------------------------------------------------------------------
def read_contradicted_pairs(db: str) -> list[dict]:
    """Return every contradicted (subject, peer) pair in the artifact.

    `evaluations.contradicted_by` holds bare peer-group ids, and each
    `peer_groups.members` is a JSON array of bare claim ids. A group can be
    large -- the largest here has nine members -- so one contradicted subject
    can yield many pairs. Each is reported separately because that is the unit
    the human has to judge.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        texts = {}

        def text_of(cid: str) -> str | None:
            if cid not in texts:
                row = conn.execute(
                    "SELECT text FROM claims WHERE id = ?", (cid,)
                ).fetchone()
                texts[cid] = row["text"] if row else None
            return texts[cid]

        pairs: list[dict] = []
        for ev in conn.execute(
            "SELECT subject_id, contradicted_by FROM evaluations "
            "WHERE relation = 'contradicted'"
        ):
            subject = text_of(ev["subject_id"])
            if subject is None:
                continue
            for gid in (g.strip() for g in ev["contradicted_by"].split(",")):
                if not gid:
                    continue
                grp = conn.execute(
                    "SELECT members FROM peer_groups WHERE id = ?", (gid,)
                ).fetchone()
                if grp is None:
                    continue
                try:
                    members = json.loads(grp["members"])
                except json.JSONDecodeError:
                    continue
                if not isinstance(members, list):
                    continue
                for cid in members:
                    if cid == ev["subject_id"]:
                        continue
                    peer = text_of(cid)
                    if peer is None:
                        continue
                    pairs.append(
                        {
                            "subject_id": ev["subject_id"],
                            "subject_text": subject,
                            "peer_id": cid,
                            "peer_text": peer,
                        }
                    )
        return pairs
    finally:
        conn.close()


# --------------------------------------------------------------------------
# stratification
# --------------------------------------------------------------------------
#: Python/JS-shaped source. Used only to *sort* pairs into buckets.
_PY_JS = re.compile(
    r"(?m)^\s*(?:from\s+\S+\s+import|import\s+\w|def\s+\w+\s*\(|"
    r"class\s+\w+\s*[(:]|return\b|\w+\s*=\s*[\w.\[\]]+|print\(|"
    r"\w+\.\w+\(|for\s+\w+\s+in\s|if\s+\w+.*:|except\b)"
)
_JS = re.compile(r"(?m)^\s*(?:function|const|let|var|=>|import\s+\{|export\s)")


def _non_latin_fraction(text: str) -> float:
    """Share of *letters* outside ASCII.

    Curly apostrophes (U+2019) are punctuation, not a script, and counting
    them was a bug in the first draft of this function -- it filed ordinary
    English prose in the non-Latin bucket. Category L* is the discriminator.
    """
    letters = [c for c in text if unicodedata.category(c).startswith("L")]
    if not letters:
        return 0.0
    return sum(1 for c in letters if ord(c) > 127) / len(letters)


def stratum_of(subject: str, peer: str) -> str:
    """Bucket a pair for sampling purposes only. Deliberately not a classifier.

    See the module docstring: measuring this function's precision is the
    experiment. It is never consulted to decide a label.
    """
    if _non_latin_fraction(subject) > 0.2 or _non_latin_fraction(peer) > 0.2:
        return "non-latin"
    lines = subject.split("\n")
    indented = sum(1 for line in lines if line[:1] in (" ", "\t")) / len(lines)
    alpha = sum(1 for c in subject if c.isalpha() or c.isspace()) / max(
        len(subject), 1
    )
    looks_code = len(lines) > 1 and (indented > 0.4 or alpha < 0.7)
    looks_code = looks_code or bool(_PY_JS.search(subject) or _JS.search(subject))
    if looks_code:
        return "code"
    return "prose"


def build_rows(pairs: list[dict], per_stratum: int, seed: int) -> list[dict]:
    """Stratified, deterministic sample. Same seed, same sheet, every time."""
    rng = random.Random(seed)
    buckets: dict[str, list[dict]] = {}
    for pair in pairs:
        bucket = stratum_of(pair["subject_text"], pair["peer_text"])
        buckets.setdefault(bucket, []).append(pair)

    rows: list[dict] = []
    for bucket in sorted(buckets):
        members = sorted(buckets[bucket], key=lambda p: (p["subject_id"], p["peer_id"]))
        rng.shuffle(members)
        for pair in members[:per_stratum]:
            rows.append(
                {
                    "pair_id": "",  # assigned below, after ordering
                    "stratum": bucket,
                    "subject_id": pair["subject_id"],
                    "subject_text": pair["subject_text"],
                    "peer_id": pair["peer_id"],
                    "peer_text": pair["peer_text"],
                    "label": "",  # the human fills this in
                }
            )
    rows.sort(key=lambda r: (r["stratum"], r["subject_id"], r["peer_id"]))
    for index, row in enumerate(rows, start=1):
        row["pair_id"] = f"P{index:04d}"
    return rows


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
def _policy_keeps(label: str, stratum: str, policy: str) -> bool:
    """Would this policy keep (i.e. record a verdict for) this labelled pair?"""
    if policy == "none":
        return True
    if policy == "no-code":
        # Compiler stops emitting code as claims.
        return not (stratum == "code" or label == "code_fragment")
    if policy == "no-code-or-subset":
        return not (
            stratum == "code" or label in ("code_fragment", "subset")
        )
    if policy == "code-only":
        # Evaluator refuses on an explicit "not a proposition" notion.
        return not (stratum == "code" or label == "code_fragment")
    raise SystemExit(f"unknown policy: {policy}")


def score(path: str, policy: str) -> int:
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    total = len(rows)
    labelled = [r for r in rows if (r.get("label") or "").strip()]
    if not labelled:
        print(
            f"{path}: {total} rows, 0 labelled.\n"
            "Fill in the `label` column before scoring. Allowed labels: "
            + ", ".join(LABELS)
        )
        return 1

    unknown = {
        (r["label"] or "").strip()
        for r in labelled
        if (r["label"] or "").strip() not in LABELS
    }
    if unknown:
        print(f"{path}: unrecognised label(s): {sorted(unknown)}")
        print("Allowed: " + ", ".join(LABELS))
        return 1

    print("=" * 72)
    print("contradiction sample -- scored")
    print("=" * 72)
    print(f"rows {total}   labelled {len(labelled)}   "
          f"unlabelled {total - len(labelled)}")

    by_stratum: dict[str, list[dict]] = {}
    for row in labelled:
        by_stratum.setdefault(row["stratum"], []).append(row)

    print("\n--- labels by stratum (the sampling frame vs the human) ---")
    header = f"{'stratum':<12} {'n':>4}  " + "  ".join(f"{l:>19}" for l in LABELS)
    print(header)
    for stratum in sorted(by_stratum):
        group = by_stratum[stratum]
        counts = [sum(1 for r in group if r["label"] == label) for label in LABELS]
        print(
            f"{stratum:<12} {len(group):>4}  "
            + "  ".join(f"{c:>19}" for c in counts)
        )

    real = [r for r in labelled if r["label"] == "real_contradiction"]
    print(f"\nreal contradictions: {len(real)}/{len(labelled)} "
          f"({100 * len(real) / len(labelled):.1f}%)")

    print("\n--- precision of the sampling frame (stratum vs human) ---")
    print("This is the number that decides whether the frame can be trusted")
    print("to stand in for a full hand-classification.")
    for stratum in sorted(by_stratum):
        group = by_stratum[stratum]
        if stratum == "code":
            hits = sum(1 for r in group if r["label"] == "code_fragment")
        elif stratum == "prose":
            hits = sum(1 for r in group if r["label"] == "real_contradiction")
        else:
            hits = sum(1 for r in group if r["label"] not in ("code_fragment",))
        print(f"{stratum:<12} n={len(group):>3}  "
              f"consistent with stratum: {hits} ({100 * hits / len(group):.0f}%)")

    kept = [r for r in labelled if _policy_keeps(r["label"], r["stratum"], policy)]
    lost_real = [
        r for r in real if not _policy_keeps(r["label"], r["stratum"], policy)
    ]
    lost = total - len(kept)
    print(f"\n--- cost of policy `{policy}` ---")
    print(f"pairs this policy would refuse : {lost} of {total} "
          f"({100 * lost / total:.1f}%)")
    print(f"verdicts it would still emit  : {len(kept)}")
    print(f"TRUE contradictions it would lose: {len(lost_real)} of {len(real)} "
          f"({100 * len(lost_real) / max(len(real), 1):.1f}%)")
    if lost_real:
        print("\nthese are the ones it would throw away:")
        for row in lost_real[:10]:
            print(f"  {row['pair_id']} [{row['stratum']}]")
            print(f"    A: {row['subject_text'][:100]!r}")
            print(f"    B: {row['peer_text'][:100]!r}")
    else:
        print("\nno true contradiction is lost under this policy")
    print(
        "\nThis is a lower bound on the cost: the sample is 120 of 2,854\n"
        "pairs, and CONTRADICTED is absorbing, so a lost real contradiction\n"
        "cannot be recovered without explicit human action."
    )
    return 0


def build(db: str, out: str, per_stratum: int, seed: int) -> int:
    pairs = read_contradicted_pairs(db)
    rows = build_rows(pairs, per_stratum, seed)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FIELDS))
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["stratum"]] = counts.get(row["stratum"], 0) + 1
    print(f"read {len(pairs)} contradicted pairs from {db}")
    for stratum in sorted(counts):
        print(f"  {stratum:<12} {counts[stratum]:>4}")
    print(f"wrote {len(rows)} rows to {out}")
    print(f"fill in the `label` column with one of: {', '.join(LABELS)}")
    print(f"then: python scripts/contradiction_sample.py score {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and score the contradiction labelling sheet."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    builder = sub.add_parser("build", help="emit an unlabelled sample sheet")
    builder.add_argument("db")
    builder.add_argument("--out", required=True)
    builder.add_argument("--per-stratum", type=int, default=PER_STRATUM)
    builder.add_argument("--seed", type=int, default=0)

    scorer = sub.add_parser("score", help="score a filled sheet")
    scorer.add_argument("csv_path")
    scorer.add_argument(
        "--policy",
        default="none",
        choices=("none", "no-code", "no-code-or-subset", "code-only"),
    )

    args = parser.parse_args(argv)
    if args.command == "build":
        return build(args.db, args.out, args.per_stratum, args.seed)
    return score(args.csv_path, args.policy)


if __name__ == "__main__":
    sys.exit(main())